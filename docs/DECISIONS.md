# Decision record — schema and API

Lead's record after the first agent pass. Each entry: decision, why, what it costs.
Status: draft until the schema and route artifacts land.

## D1. Catalog sync uses a change log, not `updated_at`

`catalog_changes(seq INTEGER PRIMARY KEY, entity, entity_id, op, version, changed_at)`, with `seq` reserved
from a single `catalog_seq` allocator in the same batch that writes the change rows. A terminal pulls
`WHERE seq > ? ORDER BY seq LIMIT n`, then fetches the referenced documents by id (batched, 100 per query).

Why: D1 is single-threaded per database, so commit order equals sequence order — a scalar checkpoint is safe.
`updated_at` cannot represent a delete, breaks on equal-millisecond writes, and depends on clock agreement
between writers. Reserving the block in the same batch means a rolled-back batch gives the numbers back.

Cost: the pull is a range scan plus a handful of `IN (...)` fetches, not one query. That is cheaper than
duplicating every document into the log, and it keeps the log small enough to prune. A terminal offline longer
than the retained window gets `410 stale_cursor` and must full-refresh.

## D2. Bills stay immutable; the server verifies, it never rewrites

The Worker recomputes the applicable list price and ceiling from its own `item_prices` at push time and stores
the comparison on the line (`computed_list_price_minor`, `price_mismatch`, `policy_violation`). The charged
amount stays as the terminal reported it, because the customer already paid it.

Why: the security review's F1 — a modified terminal can otherwise write any revenue figure and any "list price"
that makes its discount look in-policy. Rejecting the bill is worse: it loses a real sale. Verify, record both,
surface the delta.

## D3. Offline caps authority, not the sale

Offline with no valid `approval_grant`, a line falls back to the store ceiling or list price. The sale still
completes. Beyond-ceiling prices require a cached, unexpired grant minted online.

Why: F2 — "allow and flag" gives every cashier unlimited discount authority by unplugging the LAN. This keeps
availability while removing the incentive.

## D4. A terminal has an offline lease

`devices.lease_expires_at`, refreshed on every successful sync (default 7 days). Past the lease, the terminal
stops creating bills and shows a blocking dialog. Separately, a terminal that has learned it is revoked
hard-stops billing immediately.

Why: F3 — a revoked register that never reconnects can keep printing receipts forever. The lease bounds that
window without punishing a genuinely offline shop.

## D5. Two-step enrolment

Terminal posts `install_id` + code → Worker parks a pending device → owner approves that install in the admin
web → credential issued. Codes are single-use, 15 min, high entropy.

Why: F9 — a code typed in the open is observable; approval of a specific install closes the race.

## D6. Device JWT lives 5 minutes; revocation is immediate

No refresh token; the credential is the refresh mechanism. Revocation writes to KV synchronously and the token
TTL bounds the rest. Rotation marks the old credential superseded on first use of the new one.

Why: F10/F11 — 15 minutes plus a 60 s cache is a long window for a stolen credential, and "keep the old until
the next call" leaves two live secrets.

## D7. Attribution is cryptographic when online, asserted when offline

Bills carry `staff_assertion_id` when the shift started online (server-minted, 12 h). Offline PIN bills carry
`auth_method = 'pin_offline'` and no assertion, and the Worker marks them `unverified_staff`.
Migration 0004 adds both columns and a trigger that rejects a `password` bill with no assertion — otherwise
`auth_method` is a claim with nothing behind it.

Why: F5/F17 — a cached PIN hash is brute-forceable from a stolen disk, so a PIN can never be the thing that
proves a human. The assertion is the only claim the server can verify.

## D8. No money-gating secret ships to a terminal

Manager approval is an `approval_grant` token, never a cached PIN hash. The note-10 wording ("manager PIN
prompt") is superseded.

Why: F17 — a cached manager PIN is the same brute-force problem, applied to the authority that gates money.

## D9. Catalog is store-scoped at the query, not the payload

`/v1/catalog` filters prices, identifiers, roster and settings by the token's `store_id`. Items themselves are
franchise-wide (a store sells the same catalog), but nothing about another store's roster or overrides ships.

Why: F7 — one terminal must not hold every store's cashier hashes.

## D10. Reports read `bill_lines` only, and are range-bounded

Day and month reports aggregate `bill_lines` joined to `bills` on `(store_id, business_date)`. A month range is
allowed; a year range is rejected with `413 range_too_large` rather than risking the 30 s query cap.

Why: D1's real ceiling is query duration and result size, not the 10 GB storage cap.

## D11. `business_date` is validated, not trusted

The terminal computes it from the store's `business_day_start_minutes`. The Worker rejects a bill whose
`business_date` is more than one day from the server's date, and records `received_at` for every bill.

Why: F14 — otherwise a terminal can move sales between report days.

## Open questions for the owner

1. Offline lease length: 7 days is a guess. A shop with unreliable broadband may need 14.
2. Does a franchisee ever need cross-store reporting in the terminal, or is that admin-web only?
3. Retention: how long do bills stay in D1 before archiving to R2? 10 GB is years at current volume, but the
   policy has to exist before it is urgent.

## D12. One bill per batch, never 100 (TRIAGE B1)

A malformed bill must not roll back 99 good ones, and a lost response must not duplicate lines. The terminal
pipelines requests; the server commits one bill per `db.batch()` and returns `accepted`/`rejected` per bill.

## D13. `sales_daily` is the month report's source (TRIAGE B5)

Upserted in the bill's batch. The day report still reads `bill_lines`; the month report reads 30 × items.
Rebuildable from `bill_lines` after a restore or a void.

## D14. `synchronous=FULL` on the billing connection (TRIAGE B7)

WAL + `NORMAL` can lose the last committed bill on power loss. At ~1 bill/s the fsync cost is irrelevant.

## D15. Immutability is enforced by triggers, not by convention (TRIAGE B13)

Money columns on `bills` and every column on `bill_lines` are frozen after insert; `invoice_no` may be assigned
once; a void needs an actor and a reason. SQLite cannot add table constraints by `ALTER`, so migration 0004
uses triggers — which also run on the terminal, where the same rules must hold.

## D16. Cashier identity is a session, and logout is local-first (migration 0006)

`staff_sessions(user_id, terminal_id, started_at, ended_at, end_reason)` with one live session per terminal.
Bills carry `staff_session_id`, and a trigger rejects a bill whose `created_at` falls outside its session
window — on D1 and on the terminal.

Why: a register is shared. Several cashiers use it in a day, and one cashier logs out and back in. A device
credential cannot say who is standing there, and a bare `user_id` on the bill cannot say whether the cashier
was logged in at that moment.

`POST /v1/staff/logout` exists but is not the mechanism: the terminal ends the session in SQLite first and
queues the row for the next push. Offline logout therefore works, and a bill created before the logout stays
valid because validity is the window, not the push order. The route is used when the terminal is online and
the owner wants the assertion revoked within seconds.

## D17. Better Auth owns the admin plane; the device plane stays custom (migration 0007)

Better Auth handles email/password, Google OAuth, TOTP, session rotation and CSRF for the admin web. The
register keeps its own credential → JWT flow, because a device is not a browser and Better Auth does not model
device credentials.

Cost: Better Auth stores session tokens in plaintext, so a D1 dump yields live admin sessions until they
expire. Bought back with short idle expiry and immediate revocation. Benefit: OAuth and 2FA without writing
either.

Also corrected: rate-limit counters cannot live in Workers KV (no atomic ops, 60 s minimum TTL). Use D1 or a
Durable Object.

## D18. One enterprise, one D1; stores are rows (measured, not estimated)

A tenant is an enterprise. Its stores, terminals, cashiers and bills live in one database. There is no
per-store database and no sharding plan.

Measured on the real schema: 1,067 bytes per bill at 4 lines, including indexes, movements and the daily
rollup. Five stores at 600 bills/day is 1.17 GB/year, or about 8.6 years to the 10 GB cap; at 10 lines per
bill it is about 3.4 years. The earlier "10 months" figure was an estimate ten times too high and is withdrawn.

The growth answer is archival: export bills older than the retention window to R2 and keep `sales_daily`.

## D19. Sharded by enterprise, not pooled — the arithmetic decides it

Terminology first, because it changes the answer: the current design is already **sharded** — one database per
enterprise. The alternative is **pooled** — one database, a `tenant_id` on every row. They are not two flavours
of the same thing.

Measured at 1,067 bytes per bill (`capacity.md`), 600 bills/day/store:

| Tenants | Stores | Bills/day | Growth | Pooled D1 hits 10 GB in |
|---|---|---|---|---|
| 5 | 50 | 30,000 | 11.7 GB/year | **312 days** |
| 10 | 100 | 60,000 | 23.4 GB/year | **156 days** |
| 20 | 200 | 120,000 | 46.7 GB/year | **78 days** |

The 10 GB cap is per database and cannot be raised. A pooled database is therefore not a design choice that
survives the first year of selling — it is a countdown. That alone settles it, before the security argument
(one missing `WHERE tenant_id` is a cross-customer leak) or the write-contention argument (one writer for every
tenant's evening rush).

What pooling would buy is real, and worth naming: one deploy, one migration, one version, one secret set. That
is why it is tempting. The cost is a database that fills in under a year and a blast radius that includes every
customer at once.

### The middle option, and why it is not chosen yet

One Worker with N D1 bindings, routed by hostname, gives per-tenant data isolation with a single code deploy.
It loses per-tenant version pinning — every tenant moves together — which is a requirement here. Workers for
Platforms restores pinning with per-tenant script instances, at the cost of a dispatcher and a more complex
release path than twenty ordinary Workers.

At ten to twenty tenants, per-tenant Workers are the simplest thing that satisfies isolation *and* pinning.
Revisit at roughly fifty tenants, or when the deploy matrix itself becomes the bottleneck; the handlers are
already tenant-agnostic, so that move is a deployment change, not a rewrite.

## D20. One Worker, N databases — compute pooled, data sharded

D19 settled the data plane: one D1 per enterprise, because the 10 GB cap is per database and pooling fills it
in under a year. It did **not** settle the compute plane. Running one Worker per tenant was the first answer,
and it is the wrong default for a two-person team: twenty Workers, twenty secrets, twenty environments and
twenty release paths is hand-holding, not isolation.

Revised default:

| Plane | Shape | Why |
|---|---|---|
| Compute | **one Worker**, routed by hostname | one deploy, one secret set, one cron, one rollback |
| Data | **one D1 per enterprise** | the cap forces it; measured in `capacity.md` |
| KV | one namespace, tenant-prefixed keys | one resource |
| Signing key | one master secret, per-tenant key via HKDF(master, tenant_id) | one secret to rotate; a token minted for A still fails at B |
| OAuth | one Google client, per-tenant redirect URI | one console |
| Cron | one trigger, loops tenants | one schedule |

Routing is a static map generated at build time from `infra/tenants/*.json`, so adding a tenant is a data
change plus a redeploy, not a code change:

```ts
const TENANTS: Record<string, keyof Env> = {
  "reliance.pos.app": "DB_RELIANCE",
  "coles.pos.app":    "DB_COLES",
};
```

A Worker can hold thousands of D1 bindings, so this holds well past the tenant count where it stops mattering.

### What is given up

**Per-tenant version pinning.** Every tenant moves together on one deploy. That is the price of one release
path, and it is the right price for ten to twenty tenants. Buy back the risk with stages, not with versions:

- `staging` — every merge to main.
- `canary` — one real shop, a week ahead.
- everyone else — after canary survives.

**Blast radius.** A bad deploy hits every tenant at once. Mitigated by `wrangler rollback` (seconds), the
expand/contract migration rule, and the canary stage. A bad *migration* is the dangerous one, so migrations run
canary-first and stop on the first failure.

### The exception that keeps the door open

If a customer ever contracts for version isolation — Reliance plausibly will — give **that tenant** its own
Worker and its own release path. The handlers are tenant-agnostic and the code is identical, so the exception
costs a second workflow, not a second codebase. Default simple, exception possible.

Revisit at roughly a hundred tenants, where Workers for Platforms (one upload, per-tenant script instances)
beats both.

## D21. A control-plane D1 holds tenant metadata; it cannot hold the routing table

The ask: Hono keeps the tenant → database map in a metadata D1. Half of that is right, and the other half is
blocked by how D1 works.

**D1 is binding-only.** A Worker reaches a database through a binding declared in its Wrangler config
(`env.DB_RELIANCE`). There is no runtime call that opens a database by id. So a metadata table can *say*
"reliance → 8f2c…", but the Worker still cannot reach 8f2c… unless a binding for it was deployed. Routing stays
a build artifact.

What the control-plane D1 is genuinely for:

| Table | Holds | Read by |
|---|---|---|
| `tenants` | slug, host, tenant_id, binding name, plan, store limit, status | onboarding, admin |
| `tenant_schema` | schema version per tenant, last migration result | migrate workflow, `/health` |
| `tenant_flags` | feature flags that must change without a deploy | request path, cached |
| `deploy_log` | tag, tenant, who, when, result | admin, incident review |

### The shape

- `infra/tenants.json` stays **authoritative** — reviewable, diffable, and a deploy is reproducible from a
  commit. The pipeline syncs it into `tenants` on every deploy.
- The build generates the routing map from that JSON, so routing is a compiled constant with zero latency.
- `env.META` is queried only for policy that must change *without* a deploy: suspension, store-limit bumps,
  feature flags. Cached in KV for 60 s.

### Why not make META the routing source

Three reasons, in order of how much they hurt:

1. **Reproducibility.** If the database is the source of truth, a code rollback does not roll back tenant
   config, and you cannot diff what changed between two deploys.
2. **Hot-path dependency.** Routing every request through a second database makes the control plane a single
   point of failure for all tenants. A static map cannot go down.
3. **Chicken and egg.** The build would need credentials to read the control database to generate the config
   that connects to it.

### Failure behaviour

| Event | Behaviour |
|---|---|
| META unreachable | routing continues on the static map; policy falls back to the last KV value, then to allow, and alerts |
| Tenant suspended | takes effect within the 60 s cache window, no deploy |
| New tenant added | needs a deploy, because a binding is a deploy-time object |
| META database lost | rebuild from `infra/tenants.json`; nothing else is authoritative |

### If zero-redeploy onboarding ever matters

The only Cloudflare-native way to add a tenant without touching the Worker config is **Workers for
Platforms** — each tenant gets its own script instance with its own bindings, and a dispatcher routes by host.
That is the answer at a hundred tenants, not at ten. A metadata D1 is not a substitute for it.
