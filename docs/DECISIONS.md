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

**Revised 2026-11-14.** The delta embeds documents (`api-routes.md` §3.3); the fetch-by-id step above is
withdrawn. The prune floor is `MIN(devices.last_catalog_seq)` over active devices, recorded on each pull
(migration 0008); before 0008 no column held it.

## D2. Bills stay immutable; the server verifies, it never rewrites

The Worker recomputes the applicable list price and ceiling from its own `item_prices` at push time and stores
the comparison on the line (`computed_list_price_minor`, `price_mismatch`, `policy_violation`). The charged
amount stays as the terminal reported it, because the customer already paid it.

Why: the security review's F1 — a modified terminal can otherwise write any revenue figure and any "list price"
that makes its discount look in-policy. Rejecting the bill is worse: it loses a real sale. Verify, record both,
surface the delta.

**Revised 2026-11-14.** On a bill line, `price_minor` and `list_price_minor` are rates per
`price_basis_qty_base`, which is mandatory on the wire. The old canonical example sent a 1.5 kg line with no
basis, which reads as a per-gram price and would have flagged every tiered line as `price_mismatch`. Catalog
tier rows (`item_prices`) stay pack prices: `price_minor` is what `qty_base` costs, and a tiered line's
`price_breakdown.parts` are those rows, summing to `list_amount_minor`.

## D3. Offline caps authority, not the sale

Offline with no valid `approval_grant`, a line falls back to the store ceiling or list price. The sale still
completes. Beyond-ceiling prices require a cached, unexpired grant minted online.

Why: F2 — "allow and flag" gives every cashier unlimited discount authority by unplugging the LAN. This keeps
availability while removing the incentive.

**Revised 2026-11-14.** The ceiling is only checkable if the discount is: 0003's identity skipped when
`list_amount_minor = 0`, the column default. 0008 removes the bypass (`line_total = list_amount − discount`).

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

**Revised 2026-11-14.** The route table issued the credential at enrol, so D5 was not in the design. Now
`/v1/enroll` creates a pending device, `/v1/token` answers `403 approval_pending` until the owner approves
(`devices.approved_at`, 0008).

## D6. Device JWT lives 5 minutes; revocation is immediate

No refresh token; the credential is the refresh mechanism. Revocation writes to KV synchronously and the token
TTL bounds the rest. Rotation marks the old credential superseded on first use of the new one.

Why: F10/F11 — 15 minutes plus a 60 s cache is a long window for a stolen credential, and "keep the old until
the next call" leaves two live secrets.

**Revised 2026-11-14.** "Immediate" was wrong: KV is eventually consistent. Revocation takes effect within
the 5-minute JWT TTL; `/v1/token` reads `devices.revoked_at` from D1, KV is only a shortcut.

## D7. Attribution is cryptographic when online, asserted when offline

Bills carry `staff_assertion_id` when the shift started online (server-minted, 12 h). Offline PIN bills carry
`auth_method = 'pin_offline'` and no assertion, and the Worker marks them `unverified_staff`.
Migration 0004 adds both columns and a trigger that rejects a `password` bill with no assertion — otherwise
`auth_method` is a claim with nothing behind it.

Why: F5/F17 — a cached PIN hash is brute-forceable from a stolen disk, so a PIN can never be the thing that
proves a human. The assertion is the only claim the server can verify.

**Superseded by D16.** Migration 0006 dropped `staff_assertion_id` and its trigger; the assertion now rides
the staff session row.

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

**Revised 2026-11-14.** One rule: 92 days maximum; a single day reads `bill_lines`, more than one reads
`sales_daily`, so the 30 s argument no longer applies to month reports.

## D11. `business_date` is validated, not trusted

The terminal computes it from the store's `business_day_start_minutes`. The Worker rejects a bill whose
`business_date` is more than one day from the server's date, and records `received_at` for every bill.

Why: F14 — otherwise a terminal can move sales between report days.

**Revised 2026-11-14.** The ±1-day rule is withdrawn (`api-routes.md` §9 item 4 already did): `business_date`
is checked against `created_at` plus the store's cutover, and the cutover is copied onto the bill
(`bills.business_day_start_minutes`, 0008) so a later change cannot move old sales between days.

## Open questions for the owner

1. Offline lease length: 7 days is a guess. A shop with unreliable broadband may need 14.
2. Does a franchisee ever need cross-store reporting in the terminal, or is that admin-web only?
3. Retention: how long do bills stay in D1 before archiving to R2? 10 GB is years at current volume, but the
   policy has to exist before it is urgent.

Question 3 is answered by D22.

## D12. One bill per batch, never 100 (TRIAGE B1)

A malformed bill must not roll back 99 good ones, and a lost response must not duplicate lines. The terminal
pipelines requests; the server commits one bill per `db.batch()` and returns `accepted`/`rejected` per bill.

**Verified on D1, 2026-11-14.** A Worker invocation may make 1,000 D1 calls; a `db.batch()` is one call
whatever it contains. 100 batches of 14 statements succeeded; 1,001 batches of one failed with "Too many API
requests by single Worker invocation". A 100-bill push uses ~100–200 calls. Each bill's batch runs
`PRAGMA defer_foreign_keys = on`, lines, movements, then the header (D23).

## D13. `sales_daily` is the month report's source (TRIAGE B5)

Upserted in the bill's batch. The day report still reads `bill_lines`; the month report reads 30 × items.
Rebuildable from `bill_lines` after a restore or a void.

**Revised 2026-11-14.** The rollup is maintained by triggers (0008): `AFTER INSERT ON bills` adds,
`AFTER INSERT ON bill_voids` subtracts. Upserting it from the Worker double-counted when two invocations
replayed the same bill: the bill insert was `ON CONFLICT DO NOTHING`, the rollup upsert was not. A trigger
fires only when the bill row is actually inserted.

## D14. `synchronous=FULL` on the billing connection (TRIAGE B7)

WAL + `NORMAL` can lose the last committed bill on power loss. At ~1 bill/s the fsync cost is irrelevant.

## D15. Immutability is enforced by triggers, not by convention (TRIAGE B13)

Money columns on `bills` and every column on `bill_lines` are frozen after insert; `invoice_no` may be assigned
once; a void needs an actor and a reason. SQLite cannot add table constraints by `ALTER`, so migration 0004
uses triggers — which also run on the terminal, where the same rules must hold.

**Revised 2026-11-14.** Three holes, all reproduced: deleting a bill cascaded its lines away past the line
trigger; a voided bill could be un-voided; the discount identity had a bypass. 0008 adds a delete guard
(`business_date < schema_meta.archive_before`, which is also the archival gate of D22), a void-once trigger,
and voids only through `bill_voids`.

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

**Revised 2026-11-14.** The window stays, but a mismatch is **accepted and flagged** (`unverified_staff`),
never aborted. As triggers on the shared DDL the rule rejected sales on the terminal itself: a 2-second
clock step back after login, and a cart finished after switch-user (both reproduced on `modernc.org/sqlite`).
0008 drops both triggers. Session push is an upsert that applies `ended_at` once; before, duplicate session
ids were acked and never updated, so the server's copy stayed open and the next login failed
`uq_staff_sessions_live`. On the terminal an open cart blocks switch-user.

## D17. Better Auth owns the admin plane; the device plane stays custom (migration 0007)

Better Auth handles email/password, Google OAuth, TOTP, session rotation and CSRF for the admin web. The
register keeps its own credential → JWT flow, because a device is not a browser and Better Auth does not model
device credentials.

Cost: Better Auth stores session tokens in plaintext, so a D1 dump yields live admin sessions until they
expire. Bought back with short idle expiry and immediate revocation. Benefit: OAuth and 2FA without writing
either.

Also corrected: rate-limit counters cannot live in Workers KV (no atomic ops, 60 s minimum TTL). Use D1 or a
Durable Object.

**Revised 2026-11-14.** The table mapping in this entry did not work. Verified on Better Auth 1.7.23 against
a local D1: `modelName` renames tables, not columns, and its startup check refused to run; with columns
mapped it still needed `sessions.token`; with that fixed, every sign-up failed because Better Auth writes
dates as ISO TEXT into STRICT INTEGER columns. Better Auth now owns separate `auth_*` tables with TEXT dates
(0007, rewritten); domain `users` links by `users.auth_user_id`. The `admin` plugin is dropped (roles and store
scope are ours). The instance is built **per request**: a module-level instance wrote tenant B's owner into
tenant A's database. Details in `auth-better-auth.md`.

## D18. One enterprise, one D1; stores are rows (measured, not estimated)

A tenant is an enterprise. Its stores, terminals, cashiers and bills live in one database. There is no
per-store database and no sharding plan.

Measured on the real schema: 1,067 bytes per bill at 4 lines, including indexes, movements and the daily
rollup. Five stores at 600 bills/day is 1.17 GB/year, or about 8.6 years to the 10 GB cap; at 10 lines per
bill it is about 3.4 years. The earlier "10 months" figure was an estimate ten times too high and is withdrawn.

The growth answer is archival: export bills older than the retention window to R2 and keep `sales_daily`.

**Revised 2026-11-14 — the 1,067-byte figure is withdrawn.** It does not reproduce and its script was never
committed. Measured on real D1: **4,961 bytes per 4-line bill** (3,000 realistic bills, 0.71 → 15.60 MB), so
5 stores × 600 bills/day is ~5.4 GB/year, ~1.8 years to the cap with no archival; at 10 lines, under a year.
One D1 per enterprise still holds because archival is now v1 (D22) and D1 reclaims space on delete
(verified). An enterprise that would pass 70% of 10 GB at 12 months' retention gets one D1 per group of
stores; every billing row carries `store_id`, so the split is a copy. `capacity.md` has the table;
`capacity-bench.py` reproduces it.

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

**Revised 2026-11-14.** At the measured 4,961 bytes per bill, the pooled database in the first row fills in
about 67 days, not 312. The conclusion is stronger, not different.

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

**Revised 2026-11-14 — tenant resolution is the isolation boundary.** One Worker means the old
`tenant_id == TENANT_ID` environment check no longer exists, and nothing replaced it. Every request now:

1. `tenant = HOSTS[Host]`, else `421 unknown_host`; `db = env[tenant.binding]`.
2. `schema_meta.tenant_id == tenant.id`, cached per isolate, else `503 tenant_mismatch`.
3. Device JWT: key chosen by `kid` and derived for **the routed tenant**,
   `HKDF-SHA256(MASTER_KEY_<kid>, salt "traasu", info "device-jwt/v1/" + tenant.id)` — never from a claim;
   then `claim.tenant_id == tenant.id`, `aud`, `typ`, `scope`; the `devices` row must exist.
4. Admin: Better Auth built per request with `db` and `baseURL` for this host (D17).

Deriving the key from the claim would let a token minted for A verify at B, and the old seed gave every
tenant the same store and terminal ids, so A's claims would have resolved in B's database. 0001 no longer
seeds them. Cost of HKDF stated plainly: a leaked master derives every tenant's keys, so the per-tenant
secrets in the old deploy inventory bought isolation this does not. Rotation adds `MASTER_KEY_<newkid>` and
verifies both for 12 h, the life of the longest token (grants, assertions).

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

**Revised 2026-11-14.** Schema level has one source of truth per tenant: that database's `d1_migrations`
table, mirrored in `schema_meta.schema_version` (every migration now sets it; before, nothing did and it
read 1 after 0007). META `tenant_schema` and `infra/versions.json` are caches the migrate workflow writes.
Release order is migrate (expand) → deploy → smoke; a Worker declares `min_schema` and must run on every
schema from there up (`release-management.md`).

## D22. Archival is v1, behind a per-tenant flag

The owner sets **retention in months** in the admin panel; archival is a per-tenant feature flag. Both live
in META (`tenant_flags.archival_enabled`, `tenant_flags.retention_months`), next to the other per-tenant
policy that changes without a deploy (D21). A monthly job per tenant, outside shop hours and in batches of
≤ 500 bills:

1. Export a store-month to R2 (`archive/<tenant>/<store>/<yyyy-mm>.jsonl.gz`: bills, lines, voids).
2. Verify row counts and Σ totals against D1.
3. Advance `schema_meta.archive_before`, delete that month's `bill_voids`, then its bills (lines and
   movements cascade). `sales_daily` stays, so reports on archived months still work.

Why: D18's measured growth. Verified on D1: deletes free space at once (301 → 151 MB; 900 bills 15.60 →
12.67 MB, the rest being kept `sales_daily` rows), and `VACUUM` is refused and not needed. The first delete
took **8,048 ms and read 73,463,700 rows** because `inventory_movements` had no index on its FK to
`bill_lines`; with `idx_inventory_movements_line` (0008), **55 ms and 33,600 rows**.

Sizing: keep the retained window under 70% of 10 GB: ≈ 15 months at 5 stores and 4 lines, ≈ 7 at 10 lines.
R2 keeps at least six years — the GST record period as we understand it; to be confirmed with the
owner's accountant. Terminals keep 30 days of synced bills for the restore re-push (D20, `api-routes.md` §4).
A void for an archived bill is parked `bill_archived`, never dropped.

Cost: R2 joins the inventory, and a month's receipt detail needs a restore into a scratch D1 to query.

## D23. Money rules, one place

- Line: `line_total = taxable + cgst + sgst + cess + igst` (0002 omitted IGST, so an IGST line could only be
  stored with the tax missing from its total) and `line_total = list_amount − discount`.
- Split: `cgst = floor(tax / 2)`, `sgst = tax − cgst`. With round-half-up on both halves a ₹10.29 line at 5%
  sums to 1,030 paise, and the identity trigger aborted the sale on the terminal.
- Header: `subtotal = Σ taxable`, `tax = Σ taxes`, `total = Σ line_total + rounding`, at least one line.
  Enforced by inserting lines first and the header last in one transaction with deferred foreign keys.
  Tax is per line; a per-rate summary is derived from lines, never computed separately (three ₹10.01 lines
  at 18% give 459 per line and 458 on the invoice total).

`docs/check-invariants.py` holds each of these as a case.

## D24. Workers Paid before the first tenant

The Free plan caps a database at 500 MB and a day at 100,000 rows written and 5 million read; the D1
experiments used up both daily quotas on 3,000 bills and one unindexed delete. One bill writes about 42 rows
including index entries.

## D25. One-way doors, closed before code

From the deferred list in `TRIAGE.md` §F, the ones that change immutable rows or need a table rebuild:

| Item | Decision |
|---|---|
| D42 business day | `bills.business_day_start_minutes` copied at sale time (0008) |
| D30 attribution | devices and shifts are never deleted (0008 triggers) |
| D33 grants | `bill_lines.approval_grant_id`; the Worker counts uses against `max_overrides` |
| D45 returns | quantities and totals stay positive; returns are a separate document type after v1 |
| D44, D46 enums | no new CHECK vocabularies on `bills`, `bill_lines`, `catalog_changes`; the Worker validates |
| D16, D17 price revisions | decided in D26: one version per revision of one scope, no half-open tier sets |

## D26. The register's catalog is the truth for a sale; a line records the versions it used

The register prices every line from its **local SQLite** catalog, online or offline. The server never sends a
corrected price into a bill and never rewrites one (D2). What the customer paid is the bill.

Each line freezes, at the moment it is added to the cart:

- `item_version` (new, 0008) and the copied item fields: `item_name`, `dimension`, `hsn`, `tax_rate_bp`,
  `price_includes_tax`;
- `price_version` and `price_store_id` (`NULL` = franchise default), which together name exactly one tier set;
- the money: `list_price_minor`, `price_minor`, `price_breakdown`, `list_amount_minor`, taxes.

**Open cart.** A catalog update that lands while a cart is open does not reprice it. Lines already in the cart
keep their frozen versions and money; lines added after the update use the new catalog. A price never changes
on screen in front of the customer, and the bill shows exactly what the cashier saw.

**Versions are unambiguous** (closes schema review D16, D17; 0008 triggers):

| Rule | Enforced by |
|---|---|
| `items.version` grows on every content change; a replay of the same version is a no-op | `trg_items_version` |
| A price version belongs to one scope and versions only grow per item, across all scopes | `trg_item_prices_version_scope` |
| A scope's open tiers share one version and one `valid_from`; a partial write aborts | `trg_item_prices_revision_coherent` |

The Worker allocates the next price version as `MAX(version) + 1` over the item, closes every open row of the
scope, then inserts the whole new tier set in the same batch. The register applies an item's price set in
ascending version order, so the same triggers hold on its copy.

**Server check on push.** The Worker finds the line's tier set by key, not by clock:
`item_id`, `IFNULL(store_id,'') = IFNULL(price_store_id,'')`, `version = price_version`.

| Lookup result | Flag |
|---|---|
| Found, and open at `created_at` | none |
| Found, but closed before `created_at` | `stale_price`: the register had not synced the change yet |
| Not found | `price_mismatch`: a version the server never issued |
| Claimed list price differs from that version's rows | `price_mismatch` |

`computed_list_price_minor` still records the price the server would have charged at `created_at`, so the owner
sees the size of the gap. Flags are for review; the bill is accepted as charged.

Cost: every item edit must bump `version` (the trigger refuses one that does not), and the register must keep
the version numbers it received. Item history is not stored as rows: the line's copies are the record of what
an old version said.

## D27. Batches: each receipt of stock is a batch with its own MRP, price, quantity and optional expiry

Stock of one item arrives in batches. Soap received in April at MRP ₹100 and in May at MRP ₹200 are two
batches, both on the shelf. Indian law forbids charging above the printed MRP, so the April pack cannot move
to the May price. This replaces the earlier "MRP variant" draft of D27, and it moves batch tracking (without
serial numbers) into v1. Stock is now tracked per batch but still never blocks a sale.

### How GoFrugal does it, and what we take

| GoFrugal | Source | Here |
|---|---|---|
| A purchase or opening-stock entry with different price details creates a batch | [Sales – Batch LOV](https://help.serveeasy.gofrugal.com/Sales-Batch-LOV.html) | A stock receipt (purchase entry) into a book creates a batch, or adds to an existing one (D28). No purchase module in v1. |
| Batch has number, MRP, selling price, quantity, expiry; expiry optional or mandatory per item | [Batch and Expiry](https://community.gofrugal.com/portal/en/kb/gofrugalretaileasy/inventory-management/physical-inventory/articles/batch-and-expiry) | Same; expiry is optional (`NULL` = never expires) |
| At billing a batch popup lists batches; the cashier selects; price follows the batch | Batch LOV | Same, with the visibility rule below |
| Expired batches are struck out and cannot be sold | Batch and Expiry | Same |
| Batch list sorted FIFO or short-expiry first | [Batch sorting](https://community.gofrugal.com/portal/en/kb/gofrugalmanageeasy/inventory-management/customer-category-wise-sales-bill/articles/batch-sorting-6-8-2020) | Newest first (owner's requirement); FIFO/short-expiry can be a store setting later |
| "Change Selling" edits price and MRP batch-wise after inward | [Change Selling](https://community.gofrugal.com/portal/en/kb/gofrugalretaileasyoncloud/inventorymanagement/change-selling/articles/change-selling-28-10-2025) | Edit a batch's prices (new price version) or its MRP/expiry (new batch version) |
| Cost price, supplier, purchase invoice | Batch and Expiry | Not stored: purchases stay a v1 non-goal |

### Data

| Table / column | Holds |
|---|---|
| `items.uses_batches` | 1 = sold from batches; 0 = one item-level price set (loose rice) |
| `item_batches` | `store_id`, `batch_no`, `mrp_minor`, `expiry_date` (nullable), `received_at`, `retired_at`, `version` |
| `item_prices.batch_id` | a batch's own selling-price tier set, in the batch's store |
| `inventory_movements` + `batch_id`, `store_id`, `reason` | the ledger: `receipt`, `adjustment`, `sale`, `void` |
| `batch_stock` | running on-hand per batch, kept by trigger; may go negative |
| `bill_lines.batch_id`, `batch_no`, `mrp_minor` | the batch billed, copied onto the immutable line |

### What the cashier sees

A new line always uses the **latest batch** (the most recently received, not retired, not expired), even if
its count is zero. If other batches still have stock at a **different MRP**, the register prompts the cashier
with each one's MRP and quantity, and the cashier can switch the line to it (D28). When the April batch sells
out, the prompt disappears. Expired batches cannot be picked.

### Quantity across registers

- **The server is the stock truth.** Receipts and adjustments come from admin. Each pushed sale line writes
  a `sale` movement, and each void writes a `void` movement back.
- **Registers pull on-hand by cursor.** `GET /v1/stock?since=` returns rows changed after `stock_seq`. This
  is kept out of `catalog_changes`, which would otherwise gain a row per line of every sale.
- **The register shows its own estimate.** Local `batch_stock` = server value minus this register's unsynced
  sales. Other registers' sales appear on the next pull.
- **Never a block.** Two offline registers can both sell the "last" packs, so `on_hand` can go negative. v1
  never blocks a sale on stock; the owner fixes the count with an adjustment.

### Updating a batch

| Change | How |
|---|---|
| Selling price | New price version for that batch (D26 rules, per batch) |
| MRP, batch number, expiry | Batch update with `version + 1` (`trg_item_batches_version`) |
| Quantity | Never overwritten: an `adjustment` movement with a reason. A stock count enters the counted number; the Worker writes the difference. |
| Stop selling it | Retire (`retired_at`). Batches are never deleted (`trg_item_batches_no_delete`). |

Enforced in 0008 and covered by `check-invariants.py` (the April/May soap case end to end).

Cost:
- Every sold line on a batched item writes about two more rows (`batch_stock` upsert and the `stock_seq` bump)
  on top of the movement.
- `inventory_movements` stops being optional, so the ~25% saving from dropping it (D18) is gone for batched items.
- Archival deletes old sale movements by cascade; `batch_stock` keeps the balance and the R2 export keeps the
  history, so on-hand cannot be rebuilt from D1 alone after archival.

## D28. Books (stock locations); the latest batch with an MRP prompt

A **book** is a stock location inside one store: shop floor, godown, back room. It is GoFrugal's godown: a
purchase entry picks the godown, and its stock lands there
([Godown-wise transactions](https://community.gofrugal.com/portal/en/kb/gofrugalmanageeasy/inventory-management/customer-category-wise-sales-bill/articles/godown-wise-transactions-6-8-2020)).
A book is not a legal entity: one GSTIN, one invoice series, one bill.

### Data (0008)

| Table / column | Holds |
|---|---|
| `books` | `store_id`, `name`, `priority` (1 = main book), `active`, `version`. One active book per priority per store. Never deleted. Priority orders books in admin and in the register's prompt; it does not choose the batch billed. |
| `item_batches.book_id` | The book a batch lives in. Fixed for the batch's life: moving stock between books is a transfer, not an edit. |
| `stock_receipts` | One purchase entry: `store_id`, `book_id`, `reference` (supplier bill no.), `received_at`. Its `receipt` movements must land in batches of that book. |

Provisioning creates a book named "Main" with priority 1 for every store, so a store that never uses books
still has one.

### Purchase entry (admin)

1. The admin picks the store and the book. The screen header shows the book name for the whole entry.
2. **Every line is a new batch.** Each line creates its own batch with batch number (generated from the
   entry if left blank), MRP, optional expiry and selling prices. Lines never top up an older batch, even at
   the same MRP and price, so each batch is exactly one stock entry and its stock age stays visible.
3. Save writes the receipt, the new batches and their prices, and one `receipt` movement per line in one
   `db.batch()`.

Switching book starts a new entry, so one entry never mixes books.

### Which batch a line uses

There is no primary or default batch. A new line always uses the **latest batch** of the item in the store:
the most recently received batch that is not retired, not expired and in an active book, whatever its count.

**The MRP prompt.** If other batches have stock above zero and an MRP different from the latest batch's, the
register prompts: "Other stock: MRP ₹100 (APR-24, Main) — 6 left". The prompt lists each such batch with its
MRP, book, batch number and quantity, ordered by book priority. The cashier keeps the latest batch or
switches the line to the batch whose MRP is printed on the pack. Batches with the same MRP as the latest are
not listed: they are the same pack to the customer. One query returns the latest batch first and the
prompt rows after it (`query-catalogue.md` §3).

| Batches (received order) | Line uses | Prompt lists |
|---|---|---|
| A Main MRP ₹150 (5), B Main MRP ₹160 (3), C Godown MRP ₹160 (10) | C | A, 5 left |
| A sold out | C | nothing |
| A restocked (2) | C | A, 2 left |
| Book priorities swapped | C | A, 2 left |

`check-invariants.py` runs this table and the soap case.

GoFrugal, when the quantity entered exceeds the chosen batch, takes the rest from another batch in the same
location ([RPOS7 RC99.94](https://cdn-download.gofrugal.com/GoFrugal_RPOS7/RC99/EXT_95/Releasenotes.html)).
We do not split a line automatically in v1: stock is an offline estimate, and one line stays one batch, one
price. The register warns when the quantity exceeds the batch's on-hand, and the cashier can add a second
line from another batch.

### Admin rules

| Action | Rule |
|---|---|
| Reorder books | SQLite checks the unique priority row by row, so a one-statement swap fails (tested). Park one book on a free priority, move the other, then set the first: three updates in one `db.batch()`. |
| Deactivate a book | Its batches drop out of the register's lookup. Stock in it stays recorded and returns if the book is reactivated. |
| Move stock between books | A transfer (not built yet): an `out` movement from the batch in book A and an `in` movement to a matching batch in book B. GoFrugal calls this a Godown Transfer. |

Cost: one more join per scan (indexed), one more table to sync (`book` catalog entity), and a purchase entry
screen. Without a transfer, stock received into the wrong book is fixed with two adjustments. With "latest
batch always", older stock at the same MRP is not sold first unless the cashier switches; the batch counts
show it.
