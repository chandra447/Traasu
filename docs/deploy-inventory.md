# Deploy inventory: one Worker, N databases

> Release flow, schema levels and fleet-wide promotion live in [`release-management.md`](release-management.md).

Compute is pooled, data is sharded (`DECISIONS.md` D19, D20). One Worker serves every franchisee, routed
by the Host header. Around it: one KV namespace with tenant-prefixed keys, one secret set, one cron that
loops the tenants, one custom hostname per tenant, and one D1 per enterprise bound as `env.DB_<SLUG>`.
Every binding and every route is generated at build time from `infra/tenants.json`.

The per-tenant Worker, KV namespace and secret set are gone. A customer that contracts for version
isolation gets its own Worker as the D20 exception, not as a default.

Handlers are tenant-agnostic, and nothing in a request is trusted to say which tenant it is. Tenant
identity comes from the host, is checked against the tenant's own `schema_meta` row on every request
(§2), and is never an environment constant read at boot. A `TENANT_ID` constant only ever described one
tenant; with N bindings it would be N constants checked once, which is the same query the request path
already needs.

## 1. Cloudflare resources

| Resource | Scope | Name | Why it exists | If missing or broken |
|---|---|---|---|---|
| D1 database | per tenant | `pos-<slug>` | System of record: items, prices, bills, users, audit. One writer makes `catalog_changes.seq` and commit order the same. | That tenant is down; its terminals keep billing offline and retry. No cross-tenant impact. |
| Worker | shared, one script | `pos-api` | Hono API, auth, reports, catalog, bill ingest; also serves the admin SPA. | Every tenant's sync and admin down; offline billing continues. |
| Worker static assets | shared build | `[assets]` → `dist-admin` in the same Worker | Admin SPA on the same origin as the API, so `__Host-sid` + SameSite=Lax + CSRF work without CORS. Same-origin holds per host, not per build. | Admin UI unavailable; the API still serves sync. |
| D1 binding | per tenant | `env.DB_<SLUG>` in `wrangler.toml` | The only way a Worker reaches a database. Generated from `infra/tenants.json`. | `421 unknown_host` if the route is missing; a wrong id fails the `schema_meta` check with `503 tenant_mismatch`. |
| KV namespace | shared, one | `pos-cache` | Session cache, device-revocation cache, heartbeat snapshot, control-plane policy cache. Never authoritative for money or auth writes. Keys carry the tenant prefix. | Extra D1 reads; revocation window grows to the JWT TTL (5 min). |
| R2 bucket | shared, one | `pos-archive` | Bill archives (`archive/<tenant>/<store>/<yyyy-mm>.jsonl.gz`), the retention target in D22. | Archival cannot run; D1 keeps growing. Billing unaffected. |
| Queue | none in v1 | n/a | Every write is small and synchronous; per-bill D1 batches need no fan-out. Reopen for email/PDF/async export or rollup backfills. | n/a |
| Cron trigger | shared, one | `15 0 * * *` (00:15 UTC / 05:45 IST) | Loops the tenants in `infra/tenants.json`: delete expired sessions, enrol codes, approval grants and `auth_rate_limit` rows; expire stale heartbeat KV entries; run the monthly archive for tenants whose day it is. No billing work. | Expired rows accumulate, archives stop. No user-visible failure until D1 fills. |
| Rate limiting bindings | shared, four | `RL_AUTH` 10/60 s, `RL_DEVICE` 60/60 s, `RL_ADMIN` 120/60 s, `RL_REPORTS` 10/60 s | Per-colo abuse control. Keys are prefixed `t:<tenant_id>:` so one tenant cannot spend another's budget. Approximate, never a correctness boundary. | Abuse protection lost; D1 and password hashing exposed. |
| Custom hostname | per tenant | `<slug>.<zone>` (`custom_domain = true`) | Per-tenant host isolation, automatic TLS, `__Host-` cookies, pinned `api_base`. Every tenant route lives on the same Worker. | That tenant cannot sync or log in. A `421` on every route is the visible symptom. |
| Secrets | shared, one set | `MASTER_KEY_<kid>`, `BETTER_AUTH_SECRET`, `ENROL_CODE_PEPPER` | HKDF input for device JWT, staff assertions and approval grants; Better Auth cookie signatures; enrolment-code HMAC. Keys are derived per tenant from the tenant id, so one secret set serves the fleet. | Wrong or missing: every tenant's tokens fail. Rotate by `kid` (§7). |
| Vars | shared | `ENVIRONMENT`, `MIN_TERMINAL_VERSION`, `LATEST_TERMINAL_VERSION`, `LEASE_DAYS`, `OVERRIDE_CEILING_PCT`, `OVERRIDE_CEILING_MINOR`, `CATALOG_COMPACT_AT` | Version gates, offline lease, the default override ceiling (a store's `store_settings.override_ceiling_*` wins when set, 0008), compaction threshold. Tenant identity is not a var. | Limits and upgrade gates are wrong; tenant routing is unaffected. |
| Workers Paid plan | account | n/a | 10 GB per D1 instead of 500 MB, no daily row quotas, the 1,000-call invocation ceiling the bill push is designed against. | Free exceeded its 5M rows read / 100k rows written daily quota during the audit run and caps a database at 500 MB. Production requires Paid (D24). |
| Control-plane D1 | shared, one | `env.META` | `tenants`, `tenant_schema`, `tenant_flags` (including `archival_enabled`), `deploy_log`. Never in the routing path (D21). | Routing continues on the compiled map; policy falls back to the last KV value, then to allow, and alerts. |
| D1 Time Travel | per database, 30 days | n/a | Restore after a bad migration or bad data write. A restore MUST be followed by the epoch bump and the terminal re-push (§7). | Recovery window is 30 days; after that, the R2 archive and exports. |
| Workers Logs | shared, 3-day retention, sampling 1 | n/a | Debug sync/auth failures. No request bodies, credentials, PIN hashes or full query text. | Blind operations; alerts still fire. |

Not created: Durable Objects, Hyperdrive, Access/SSO, Turnstile, email. Account-level WAF and managed
rules are shared.

KV keys. The tenant prefix is part of the key, so a handler that forgets it cannot read another tenant's
cache: the key simply misses.

| Key | Value | TTL | Authoritative copy |
|---|---|---|---|
| `t:<tenant_id>:sess:<hmac(session_id)>` | `{user_id, role, stores, expires_at}` | 30 s | `auth_session` row in that tenant's D1 |
| `t:<tenant_id>:dev:<device_id>:revoked` | `1` | 5 min (JWT TTL) | `devices.revoked_at`; `/v1/token` reads D1 directly |
| `t:<tenant_id>:hb:<device_id>` | `{at, unsynced, oldest_age_s, app_version}` | 15 min | none; dashboard only |
| `t:<tenant_id>:meta:<key>` | control-plane flags copied from META | 60 s | `tenant_flags` (D21) |

## 2. Bindings and tenant resolution

`infra/tenants.json` compiles into a static map at build time. Nothing is looked up per request beyond a
hash map and one cached query.

```ts
// Generated by `bun run gen:bindings`; never hand-edited.
const HOSTS: Record<string, { binding: keyof Env; id: string; appId: string }> = {
  "reliance.pos.app": { binding: "DB_RELIANCE", id: "tnt_reliance", appId: "pos-prod-reliance" },
  "coles.pos.app":    { binding: "DB_COLES",    id: "tnt_coles",    appId: "pos-prod-coles" },
};
```

Resolution runs as middleware before any handler, on every plane:

1. `tenant = HOSTS[request.headers.get("host")]`. Unknown host → `421 unknown_host`.
2. `db = env[tenant.binding]`. A missing binding is a deploy bug, and the same `421`/`503` path catches
   it rather than throwing.
3. `schema_meta.tenant_id == tenant.id`, cached per isolate per binding. A mismatch → `503
   tenant_mismatch`. This is what turns a routing-table mistake into an outage for one host instead of
   writes into another franchisee's database.
4. Device JWT: the verification key comes from the header `kid` and is derived for the routed tenant
   only: `HKDF-SHA256(ikm=MASTER_KEY_<kid>, salt="traasu", info="device-jwt/v1/" + tenant.id)`. Never
   derive the key from a claim, or a token can choose its own tenant. Then require `claim.tenant_id ==
   tenant.id`, `aud == tenant.app_id`, `typ`, `scope`, and a `devices` row for `sub` that exists and is
   unrevoked; a missing row is `401 invalid_token`. Revocation takes effect within 5 min (JWT TTL), and
   `/v1/token` checks `devices.revoked_at` in D1.
5. Admin: the Better Auth instance is constructed per request with `database: db`, `baseURL:
   https://<host>`, `trustedOrigins: [https://<host>]`. Never at module scope. A module-level instance
   was reproduced writing tenant B's owner and session into tenant A's D1.

Staff assertions and approval grants use the same HKDF scheme under different `info` labels
(`staff-assertion/v1/`, `approval-grant/v1/`), so a key derived for one purpose never verifies another.

`wrangler.toml` is one file for the one Worker. Names only; ids are filled by the generator, secrets are
never in this file.

```toml
name = "pos-api"
main = "src/index.ts"
compatibility_date = "2026-10-01"
workers_dev = false

[assets]
directory = "./dist-admin"
binding = "ASSETS"
not_found_handling = "single-page-application"
run_worker_first = ["/v1/*", "/health"]

# One block per tenant, generated from infra/tenants.json.
[[d1_databases]]
binding = "DB_RELIANCE"
database_name = "pos-reliance"
database_id = "<d1-id>"
migrations_dir = "migrations"

[[d1_databases]]
binding = "DB_COLES"
database_name = "pos-coles"
database_id = "<d1-id>"
migrations_dir = "migrations"

[[d1_databases]]
binding = "META"
database_name = "pos-meta"
database_id = "<meta-d1-id>"
migrations_dir = "migrations-meta"

[[kv_namespaces]]
binding = "CACHE"
id = "<kv-id>"

[[r2_buckets]]
binding = "ARCHIVE"
bucket_name = "pos-archive"

[[ratelimits]]
name = "RL_AUTH"
namespace_id = "1001"
  [ratelimits.simple]
  limit = 10
  period = 60

# RL_DEVICE 1002 (60/60), RL_ADMIN 1003 (120/60), RL_REPORTS 1004 (10/60) follow the same shape.

[triggers]
crons = ["15 0 * * *"]

[observability]
enabled = true
head_sampling_rate = 1

[vars]
ENVIRONMENT = "production"
MIN_TERMINAL_VERSION = "1.4.0"
LATEST_TERMINAL_VERSION = "1.5.0"
LEASE_DAYS = "7"
OVERRIDE_CEILING_PCT = "10"
OVERRIDE_CEILING_MINOR = "5000"
CATALOG_COMPACT_AT = "200000"

# One route per tenant, also generated from infra/tenants.json.
[[routes]]
pattern = "reliance.pos.app"
custom_domain = true

[[routes]]
pattern = "coles.pos.app"
custom_domain = true

# Set once per account, with `wrangler secret put`:
#   MASTER_KEY_1        base64, 32 random bytes; HKDF input, never a signing key itself
#   BETTER_AUTH_SECRET  Better Auth cookie signature secret
#   ENROL_CODE_PEPPER   enrolment-code HMAC
```

Do not use wrangler environments for tenants: one config, one deploy, and no `--env` that can select the
wrong database. `wrangler.toml` is committed and reviewable; the generated D1 and route blocks are
checked by CI (`gen:bindings --check`) so a hand edit that drifts from `tenants.json` fails the build.

## 3. Migrations

Layout:

```
migrations/
  0001_init.sql                 # docs/d1-schema.sql: tables, triggers, reference seeds
  0002-sync-and-money-fixes.sql
  0003-voids-and-coherence.sql
  0004-immutability-and-attribution.sql
  0005-history-and-outbox.sql
  0006-staff-sessions.sql
  0007-better-auth.sql          # rewritten in place: Better Auth tables, users.auth_user_id, sessions dropped
  0008-audit-fixes.sql          # the audit fixes below; sets schema_version = 8
  checksums.sha256              # sha256 of every file; CI fails on drift
```

Ordering is lexical on the zero-padded number. A migration is immutable once applied anywhere. Every
migration sets `schema_meta.schema_version` in the same file that changes the shape, so there is no
window where the tables and the number disagree. The truth for a tenant's level is that database's own
`d1_migrations` table plus `schema_meta.schema_version`; `META.tenant_schema` and `infra/versions.json`
are caches the migrate workflow writes after reading both.

0001 no longer seeds a store, terminal or numbering policy. It keeps the reference rows (units, tax
rates) and the catalog-log seeds, which are the same on every install; provisioning generates random
UUIDv7 ids for the first store, terminal and numbering policy (§4). Nothing is applied to a live tenant
yet, so 0007 was rewritten in place and 0008 is new.

`0008-audit-fixes.sql` adds, at minimum:

| Change | Reason |
|---|---|
| `devices.last_catalog_seq`, `UNIQUE(credential_prefix)` | Pruning and delta replay need the last cursor each device reported; credential prefixes are unique (T12). |
| `UNIQUE(install_id) WHERE revoked_at IS NULL` | One live install per id. A re-enrol after revocation is allowed; a silent second live install is not. |
| `staff_sessions.assertion_verified_at`, `bills.unverified_shift_id` | The server accepts and flags what it cannot verify instead of rejecting the bill (T6). |
| Removal of the two staff-window ABORT triggers | A clock step or a switch-user no longer aborts a bill; the row is stored with `unverified_staff` set. Both were reproduced aborting real bills. |
| `AFTER INSERT ON bills` maintains `sales_daily`; `AFTER INSERT ON bill_voids` decrements it | A duplicate push fires no AFTER INSERT trigger, so a replay cannot double-count; the header-last batch order lets the trigger reconcile against its lines (T4). |
| `bills.business_day_start_minutes` | The day boundary is copied at sale time, so a settings change never re-dates an old bill (D42). |
| `bill_lines.approval_grant_id` | Names the grant that authorised an override; the server counts uses against `max_overrides` (D33). |
| `items.version`, `bill_lines.item_version`, and price-version triggers (`trg_items_version`, `trg_item_prices_version_scope`, `trg_item_prices_revision_coherent`) | The register's catalog is the truth for a sale; a line names the exact item and price versions it used, and a price write cannot leave a half-open tier set (D26). |
| `item_batches`, `batch_stock`, `stock_seq`, `items.uses_batches`, `item_prices.batch_id`, `bill_lines.batch_id/batch_no/mrp_minor`, movement `batch_id/store_id/reason`, price triggers per batch, stock and void-restock triggers | Batches (D27): several batches of one item on sale at once, each with MRP, price, quantity and optional expiry; the cashier picks; stock is a ledger that never blocks a sale. |
| `books`, `item_batches.book_id/is_primary`, `stock_receipts`, movement `receipt_id` | Books (D28): stock locations with a priority (1 = main); purchase entries land in one book; the register preselects the main book's primary batch and falls back to the next book. |
| `schema_meta.archive_before`, `BEFORE DELETE ON bills` raising `bill_not_archivable` | The archive watermark and the guard that stops a delete of anything newer than it (T8). |
| `BEFORE DELETE` triggers on `devices` and `shifts` (`staff_sessions` were already `ON DELETE RESTRICT`) | A bill's parents are never deleted (D30). |
| `idx_bills_store_date` rebuilt non-partial, with `voided` in the key | The archive export walks one store-month; the old partial index (`WHERE voided = 0`) cannot serve that range, so the export becomes a range seek instead of a scan |

0008 also adds the bill-delete cascade's child index, `idx_inventory_movements_line` on
`inventory_movements(bill_id, bill_line_no)`. Without it, deleting 900 archived bills took 8,048 ms and
read 73,463,700 rows; with it, 55 ms and 33,600 rows (`d1-limits.md`).

Apply per tenant, canary first, then deploy:

```sh
node infra/migrate.mjs --tenant <slug>   # wraps:
# wrangler d1 migrations apply pos-<slug> --remote
# then: SELECT schema_version FROM schema_meta; SELECT COUNT(*) FROM d1_migrations
# then: GET https://<slug>.<zone>/health -> schema_version == latest, min_schema <= schema_version
```

Order is **migrate all tenants → deploy the Worker → smoke per tenant** (T3). A Worker release declares
`min_schema` and must run against `min_schema` and every newer schema, so the old Worker keeps serving
while a tenant's database is ahead of it. Migrations are expand-only for the rollout window: add tables
and columns, backfill idempotently. A `DROP` or rename ships in a later migration only after every
tenant reports the version that stopped using the column. A Worker rollback must still work against the
newer schema, which is what `min_schema` asserts.

Rollback of a migration is a compensating forward migration, not a down file. Time Travel restores the
database; after any restore run the epoch bump and the terminal re-push (§7) or terminals silently keep
an old catalog and lose the last hour of bills.

Local: `wrangler d1 migrations apply pos-dev --local`.

## 4. Provisioning a new franchisee

Run `bun run provision --slug <slug> --name "..." --owner-email ... --stores ...`. Steps are resumable via
`deploy/state/<slug>.json`; every create step is get-or-create, every seed is `INSERT OR IGNORE`, every
verification is read-only.

| # | Step | Failure point | Recovery |
|---|---|---|---|
| 1 | Validate slug (`^[a-z][a-z0-9-]{2,30}$`), tenant uniqueness, **Workers Paid active**, `wrangler whoami`, zone/DNS access, owner email, retention months | Nothing created | Abort; fix input or permission |
| 2 | `wrangler d1 create pos-<slug>`; capture `database_id` | Name collision or account permission | If ours, resume from state; else abort before anything reads the id |
| 3 | Append the tenant record to `infra/tenants.json`: slug, host, tenant_id, app_id, binding `DB_<SLUG>`, d1_id, stores_expected. Open a PR. | Invalid binding name or duplicate host | Nothing deployed; fix and re-run. The record is reviewed like code |
| 4 | Run `bun run gen:bindings` (D1 block + route) and `wrangler deploy --dry-run` | Generated drift, invalid config | No deploy; fix `tenants.json` and regenerate |
| 5 | Apply `0001` through `0008` to the new D1 | SQL error or checksum drift | D1 is empty: drop and recreate, or Time Travel; never edit an applied file |
| 6 | Seed in one D1 batch: `UPDATE schema_meta SET tenant_id=?, app_id=?, provisioned_at=?`; insert a store, `store_settings`, a "Main" book (priority 1), a terminal, `terminal_config` and `numbering_policy(scope='store')` with **random UUIDv7 ids generated here**; seed the complete catalog (units and tax rates come from 0001; items, prices, identifiers from the tenant's import); create the owner with a generated one-time password (`must_change_password=1`); append catalog change rows and advance `catalog_seq.last_seq` atomically. A fresh terminal must never see an empty catalog. | Unique constraint or malformed JSON | The batch rolls back; fix inputs and re-run; print the one-time password once |
| 7 | Deploy the Worker so the new binding and route exist | Build or binding error | No traffic lost for existing tenants; the release still has to pass smoke for them too |
| 8 | Smoke on the new host: `/health` (200, `app_id`/`tenant_id`/`schema_version`/`min_schema`), `/` (SPA), owner login, `/v1/admin/me`, mint an enrol code, enrol a test client (`403 approval_pending`), owner approves it, `/v1/token` succeeds, catalog snapshot, fake bill push | Any check fails | Do not hand over; roll back the Worker; keep D1 for diagnosis |
| 9 | DNS for the custom hostname; wait for TLS; repeat `/health` | Cert or DNS pending | Smoke via workers.dev; retry; do not issue the URL until TLS is active |
| 10 | Secrets: nothing to create. The account secret set already exists and the per-tenant keys come from HKDF. Verify a device JWT minted for the previous tenant is rejected here. | A token from another tenant verifying is a resolver bug | Stop; fix resolution before handover |
| 11 | Alerts: Worker 5xx > 1%, that tenant's D1 > 70% of 10 GB, cron failure, archive job failure, `/health` non-200; add an uptime check | Alert missing | Handover blocked until the check exists |
| 12 | Write the registry entry (slug, ids, host, app/tenant ids, versions, retention months); print the handover sheet: admin URL, owner email, one-time password (once), store and terminal ids, support contact | Registry write fails | Deployment works but is undocumented; fix before handover |

Adding a store later: insert `stores`, `store_settings`, a "Main" book (priority 1), `numbering_policy(scope='store')`, a terminal and
`terminal_config`; append the `store`, `store_settings`, `numbering_policy` and `terminal_config` change
rows and advance `catalog_seq.last_seq` in the same batch. No Worker or D1 creation and no deploy.
Adding a terminal is an admin action (`POST /v1/admin/terminals`, then an enrol code plus owner approval),
not provisioning.

Decommission: export the D1, keep the R2 prefix (`archive/<tenant>/`) for the retention period, revoke
every device, export audit rows, remove the tenant from `infra/tenants.json` and deploy (the binding and
route go with it), then delete the D1. GST record retention is years, not months, so the archive outlives
the database. Keep the registry entry marked `retired` for a year.

## 5. Shared vs per tenant

| Component | Scope | Blast radius if it breaks |
|---|---|---|
| Worker code and admin SPA | shared, one deploy | A bad release can break sync and admin for every tenant at once. **Offline billing continues** on local SQLite and the outbox. Mitigate with staging, a canary shop, `wrangler rollback`, and never shipping a schema-breaking change in the same release. |
| Migration files | shared | Highest-risk artifact: one bad migration touches every database. Expand-only, checksum guard, canary first, stop on failure, Time Travel restore. Sequential rollout bounds the damage to the tenants already migrated. |
| Secrets (`MASTER_KEY_<kid>`, `BETTER_AUTH_SECRET`, `ENROL_CODE_PEPPER`) | shared, keys derived per tenant | A master-key leak is a fleet compromise: the holder derives any tenant's device key, mints a token for any tenant, and passes the tenant claim checks because only the `devices` row still stands in the way. That is the stated price of one secret set. Mitigate with per-tenant derivation (a token minted for A still fails at B), a 12 h dual-key rotation window (§7), and treating the secret as the single highest-value credential in the system. |
| D1, custom hostname | **per tenant** | Failure is contained to one franchisee. A routing mistake surfaces as `503 tenant_mismatch` from the `schema_meta` check, not as a write into another tenant's rows. |
| KV namespace, rate-limit namespaces, R2 bucket | shared, prefixed | A prefix bug reads a miss or writes a key nobody reads; cache and archive paths are the only exposure. Money and auth truth stay in D1. |
| Cron, `wrangler.toml`, routing map | shared | One bad schedule or one bad binding edit affects every tenant. The generated map is checked against `tenants.json` in CI. |
| Provisioning and release scripts | shared | A bug can target the wrong D1. `gen:bindings --check`, the per-request `schema_meta` check, and a provisioning state file stop a cross-tenant deploy from serving. |
| Cloudflare account, zone, DNS | shared | Account or zone outage takes every hostname offline. Terminals retry; data stays local. Keep workers.dev smoke URLs and a status page. |
| Control-plane registry (`tenants.json`, `env.META`) | shared | META is never in the routing path; a wrong row breaks provisioning or a flag, not live traffic. `tenants.json` is authoritative and deployed with the code. |
| Report and query logic | shared code, per-tenant data | Wrong numbers for every tenant; bills and lines are immutable, so a fix is a redeploy plus a recompute, never a rewrite. |
| `units` and `tax_rates` seeds | per-tenant data | A bad seed is a per-tenant data fix; no runtime sharing. |

## 6. Versioning and `/health`

```json
{"status":"ok","app_id":"pos-prod-kukatpally","tenant_id":"tnt_kukatpally",
 "api_version":"v1","build":{"version":"1.8.0","commit":"a1b2c3d","deployed_at":1763100000},
 "schema_version":8,"min_schema":8,
 "min_terminal_version":"1.4.0","latest_terminal_version":"1.5.0",
 "catalog":{"epoch":1,"head":8431,"alloc_head":8431,"pruned_below":0,"reset_to":0},
 "lease_days":7,"server_time":1763107200}
```

- `schema_version` is the resolved tenant's `schema_meta.schema_version`, read from that database on each
  check, not from a cache. `min_schema` is the build constant the release was tested against. A database
  older than `min_schema` → `503 schema_mismatch` on every route except `/health`, which reports it. A
  database newer than the build is expected during a rollout and is reported as `schema_ahead: true`.
- The tenant guard is per request (§2 step 3), not a boot constant. `tenant_mismatch` means the host, the
  binding and the database disagree; no data route runs.
- `min_terminal_version` is enforced on device routes via `X-Client-Version`; below it → `426
  upgrade_required` (heartbeat and `/health` exempt). Billing continues offline; sync pauses.
  `latest_terminal_version` drives the upgrade banner. Versions are self-reported, so this is an
  operational gate, not a security control.
- `catalog.epoch` = `schema_meta.sync_epoch`; `head` = `MAX(seq)` over `catalog_changes` (committed, not
  the allocator); `alloc_head` = `catalog_seq.last_seq`; `pruned_below` = the lowest retained seq;
  `reset_to = max(0, pruned_below - 1)`. The `catalog` object on `/health`, `/v1/enroll` and `/v1/token`
  is diagnostic only: a new terminal MUST start at `since=0` or `?snapshot=1`, never at the returned
  `head`. A terminal holding an older epoch or `since < reset_to` gets `410` and a staged snapshot.
- `sync_epoch` changes only on a Time Travel restore, a database re-create or a destructive migration,
  never on routine compaction. Compaction advances `pruned_below` and `catalog_rebuild`; terminals below
  the floor reset, terminals at or above it keep pulling deltas. Bump to the pre-restore maximum + 1,
  never `restored_epoch + 1`, which can collide with an epoch a terminal already holds and defeat the
  sticky reset.
- API version is the `/v1` path. Additive fields only within v1; clients ignore unknown fields. A
  breaking change ships `/v2` and the old router stays until every tenant's heartbeats report a
  `/v2`-capable terminal version.
- Admin SPA version equals the Worker version because they are one deploy, so an SPA/API mismatch cannot
  happen mid-release. The SPA reads `/health` and shows `schema_version` and `schema_ahead` on its
  diagnostics page.

## 7. Capacity and operations

- D1 is 10 GB per database, one writer, 30 s query, 50 MB result. A measured 4-line bill costs **4,961
  bytes** on D1 including indexes and movements, so 5 stores × 600 bills/day is 5.4 GB/year, about 0.45
  GB/month, and 1.8 years to the cap with no archival (`capacity.md`). Reports are range-capped at 92
  days and multi-day ranges read `sales_daily`.
- Alert at **70% of 10 GB** per database. At that point shorten the retention window or move to the
  store-group split; do not wait for the cap.
- Bill push is at most 100 bills per request and one `db.batch()` per bill. A batch counts as **one** D1
  call regardless of statement count, so 100 bills is well inside the 1,000 calls one invocation may
  make (`d1-limits.md`).
- Workers Paid is a prerequisite, not a preference: Free caps a database at 500 MB and a day at 5M rows
  read and 100k rows written, both of which the audit run exceeded (D24).
- Cron loops the tenants: daily expiry cleanup, and on a tenant's monthly archive day the archival job.
  Catalog compaction stays a manual or CLI operation, and must run after every Time Travel restore. It
  appends a **complete** re-seed of every catalog document (including inactive rows, so the terminal can
  retire stale aliases and users) at new seqs. If it fits one D1 batch, it advances `pruned_below` in the
  same transaction. If it is chunked, it writes a `catalog_rebuild` row, marks `complete=1` only after
  the last chunk, and only then publishes the new floor; a terminal must never 410 into a half-written
  generation. Routine compaction does not bump `sync_epoch`. A 410 reset returns `reset_to =
  pruned_below - 1`, so `since=reset_to` pulls the first retained row and `since=reset_to-1` is stale.
- Archival (D22, v1). Per tenant, only when `tenant_flags.archival_enabled` is set in META, for a
  `retention_months` the owner chooses in the admin panel. The monthly job runs outside shop hours in
  batches of at most 500 bills per statement, and one store-month at a time, in this order: export the
  month to `archive/<tenant>/<store>/<yyyy-mm>.jsonl.gz` (bills, lines, voids); verify row counts and the
  sum of `total_minor` against D1; advance `schema_meta.archive_before`; delete that month's
  `bill_voids`; then delete the bills, whose lines and movements cascade. Nothing is deleted before the
  verified export exists, and the watermark moves before the delete so the guard admits exactly that
  month. `sales_daily` is kept.
  Space is reclaimed on delete immediately; `VACUUM` is refused inside a transaction and unnecessary. A
  measured 900-bill delete freed about 65% of those bills' footprint (15.60 → 12.67 MB) because the
  lines and movements go and the rollup stays. The `BEFORE DELETE` guard makes a bill newer than
  `archive_before` fail with `bill_not_archivable` instead of disappearing. A void for a bill already
  archived is parked `bill_archived`, never dropped. R2 keeps at least 6 years (GST record retention,
  owner to confirm with the accountant; unverified).
- Secret rotation, by `kid`. Add `MASTER_KEY_2`, sign with `2`, verify with `1` and `2` for 12 h (the
  longest-lived token is a grant or an assertion), then remove `MASTER_KEY_1`. Device JWTs live 5 min,
  so they converge long before the window closes. `BETTER_AUTH_SECRET` rotation invalidates every admin
  session and users log in again. `ENROL_CODE_PEPPER` rotation invalidates unused codes only. All three
  are fleet-wide, so rotate once, not tenant by tenant.
- Restore runbook: Time Travel restore → capture the pre-restore maximum `sync_epoch` **before**
  restoring → bump `sync_epoch` to that value + 1 → rebuild `sales_daily` from bills → verify
  `catalog_seq` and `catalog_rebuild` → `/health` → confirm a terminal gets `410` and completes a staged
  snapshot. The epoch change also tells every terminal to re-push bills, voids and sessions with
  `created_at >= restore_point - 1 h`; the push is idempotent by id and `payload_sha256`, so a replayed
  bill matches and a restored-away bill comes back. Without the bump a terminal silently keeps its old
  catalog and its unsynced hour is never asked for. Terminals keep at least 30 days of synced bills, the
  same window as Time Travel.
- Archive restore: load a month from R2 into a scratch D1 for an audit. Never merge it back into the live
  database; the live database is append-only for new bills and a re-insert would collide with ids.

## 8. Decisions and open items

1. Static assets ship in the Worker, not a separate Pages project, so the session cookie stays
   same-origin per tenant host.
2. One KV namespace with tenant-prefixed keys. KV is cache only, never money or auth truth.
3. No Queue in v1; per-bill D1 batches and bounded reports cover the load.
4. Bindings and routes are generated from `infra/tenants.json` and committed, so no `--env` can select
   the wrong tenant. CI checks the generated file for drift.
5. `0008-audit-fixes.sql` is required before the archival job and the bill contract in `api-routes.md`
   are enforceable: the archive guard, trigger-maintained `sales_daily`, grant attribution,
   `business_day_start_minutes`, the device cursor and the parent-delete guards. `users.auth_user_id`
   and the Better Auth tables come with the rewritten 0007.
6. Enrolment is two-step (D5): `/v1/enroll` creates a pending device, `/v1/token` returns `403
   approval_pending` until the owner approves in admin.
7. Owner MFA is Better Auth's `twoFactor()` plugin (D17 revised), not a launch gate tracked outside this
   inventory.
8. R2 is in v1 for the archive (T8). Time Travel is the 30-day undo, not the backup, and not the
   archive.
