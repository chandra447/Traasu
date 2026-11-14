# Deploy inventory — one tenant

> Release flow, version pinning and per-tenant promotion live in [`release-management.md`](release-management.md). deployment

One franchisee = one Cloudflare Worker + one D1 database + one KV namespace + one custom hostname.
The codebase, migrations and admin SPA build are shared; every runtime resource and every secret is
per tenant. Handlers are tenant-agnostic: `tenant_id` and `app_id` are environment constants, checked
against `schema_meta` at boot, never accepted from a request.

## 1. Cloudflare resources to create per tenant

| Resource | Name | Why it exists | If missing / broken |
|---|---|---|---|
| D1 database | `pos-<slug>` | System of record: items, prices, bills, users, audit. One writer makes `catalog_changes.seq` and commit order the same. | Tenant is down; terminals keep billing offline and retry. No cross-tenant impact. |
| Worker | `pos-api-<slug>` | Hono API, auth, reports, catalog, bill ingest; also serves the admin SPA. | Tenant's sync and admin down; offline billing continues. |
| Worker static assets | `[assets]` → `dist-admin` in the same Worker | Admin SPA on the same origin as the API, so `__Host-sid` + SameSite=Lax + CSRF work without CORS or a cross-site cookie. A separate Pages project per tenant is the alternative, but then cookies need `SameSite=None`. | Admin UI unavailable; API still serves sync. |
| KV namespace | `pos-cache-<slug>` | Session cache (30 s), device-revocation cache (60 s), heartbeat snapshot (15 min). Never authoritative for money or auth writes. | Extra D1 reads; revocation window grows to the JWT TTL (5 min). |
| Queue | **none in v1** | Every write is small and synchronous; per-bill D1 batches need no fan-out. Reopen when adding email/PDF/async exports or multi-day rollup backfills. | n/a |
| Cron trigger | `15 0 * * *` (00:15 UTC / 05:45 IST) | Delete expired sessions, enrol codes and approval grants; expire stale heartbeat KV entries; optionally compact the catalog log. No billing work. | Expired rows accumulate; catalog log grows. No user-visible failure. |
| Rate limiting bindings | `RL_AUTH` 10/60 s, `RL_DEVICE` 60/60 s, `RL_ADMIN` 120/60 s, `RL_REPORTS` 10/60 s | Per-colo abuse control; keys are `ip:`, `dev:`, `sess:`. Approximate, never a correctness boundary. | Abuse protection lost; D1/PBKDF2 exposed. |
| Custom domain | `api-<slug>.<zone>` (`custom_domain = true`) | Per-tenant host isolation, automatic TLS, `__Host-` cookies, pinned `api_base`. | Terminal cannot sync/admin cannot log in; DNS fix or workers.dev fallback for smoke tests only. |
| Secrets | `DEVICE_JWT_SECRET`, `SESSION_PEPPER`, `ENROL_CODE_PEPPER` | HS256 device JWT signing; session-token and CSRF HMAC; enrolment-code HMAC. 32 random bytes each, unique per tenant, never in the repo. | Wrong/missing secret: all tokens/sessions invalid; rotate and redeploy. Never share across tenants. |
| Vars | `APP_ID`, `TENANT_ID`, `ENVIRONMENT`, `MIN_TERMINAL_VERSION`, `LATEST_TERMINAL_VERSION`, `LEASE_DAYS`, `OVERRIDE_CEILING_PCT`, `OVERRIDE_CEILING_MINOR`, `CATALOG_COMPACT_AT`, `ADMIN_ORIGIN` | Tenant identity, version gates, offline lease, v1 override ceiling (tenant-wide until per-store columns land), compaction threshold, Origin allowlist. | Boot check fails (`tenant_mismatch`/`schema_mismatch`) or limits are wrong. |
| D1 Time Travel | account feature, 30 days | Restore after a bad migration or bad data write. A restore MUST be followed by an epoch bump and catalog rebuild (see §4). | Recovery window is 30 days; after that, exports/manual repair. |
| Workers Logs | observability, 3-day retention, sampling 1 | Debug sync/auth failures. No request bodies, credentials, PIN hashes or full query text in logs. | Blind operations; alerts still fire. |

Not created: R2 (Time Travel is the backup in v1), Durable Objects, Hyperdrive, Access/SSO, Turnstile,
email. Account-level WAF/managed rules are shared.

KV keys:

| Key | Value | TTL | Authoritative copy |
|---|---|---|---|
| `sess:<hmac(session_id)>` | `{user_id, role, stores, expires_at}` | 30 s | `sessions` row |
| `dev:<device_id>:revoked` | `1` | 5 min (JWT TTL) | `devices.revoked_at` |
| `hb:<device_id>` | `{at, unsynced, oldest_age_s, app_version}` | 15 min | none; dashboard only |
| `rate:<scope>:<key>` | not used | — | rate-limit binding instead; KV is eventually consistent and a poor counter |

## 2. `wrangler.toml` binding sketch

Names only; ids are filled by the provisioning script. Secrets are never in this file.

```toml
name = "pos-api-<slug>"
main = "src/index.ts"
compatibility_date = "2026-10-01"
workers_dev = false

[assets]
directory = "./dist-admin"
binding = "ASSETS"
not_found_handling = "single-page-application"
run_worker_first = ["/v1/*", "/health"]

[[d1_databases]]
binding = "DB"
database_name = "pos-<slug>"
database_id = "<d1-id>"
migrations_dir = "migrations"

[[kv_namespaces]]
binding = "CACHE"
id = "<kv-id>"

[[ratelimits]]
name = "RL_AUTH"
namespace_id = "1001"
  [ratelimits.simple]
  limit = 10
  period = 60

[[ratelimits]]
name = "RL_DEVICE"
namespace_id = "1002"
  [ratelimits.simple]
  limit = 60
  period = 60

[[ratelimits]]
name = "RL_ADMIN"
namespace_id = "1003"
  [ratelimits.simple]
  limit = 120
  period = 60

[[ratelimits]]
name = "RL_REPORTS"
namespace_id = "1004"
  [ratelimits.simple]
  limit = 10
  period = 60

[triggers]
crons = ["15 0 * * *"]

[observability]
enabled = true
head_sampling_rate = 1

[vars]
APP_ID = "pos-prod-<slug>"
TENANT_ID = "tnt_<slug>"
ENVIRONMENT = "production"
MIN_TERMINAL_VERSION = "1.4.0"
LATEST_TERMINAL_VERSION = "1.5.0"
LEASE_DAYS = "7"
OVERRIDE_CEILING_PCT = "10"
OVERRIDE_CEILING_MINOR = "5000"
CATALOG_COMPACT_AT = "200000"
ADMIN_ORIGIN = "https://api-<slug>.<zone>"

[[routes]]
pattern = "api-<slug>.<zone>"
custom_domain = true

# Set with `wrangler secret put --name ... --config ...`:
#   DEVICE_JWT_SECRET, SESSION_PEPPER, ENROL_CODE_PEPPER
```

Each tenant gets its own rendered copy under `deploy/tenants/<slug>.toml` (ids are not secrets; commit
them for audit). Do not use wrangler environments for tenants: one config per tenant keeps the D1 id,
KV id, hostname and secrets unambiguous.

## 3. Migrations

Layout:

```
migrations/
  0001_init.sql                 # docs/d1-schema.sql, verbatim, including seeds
  0002_must_land.sql            # additive fixes from TRIAGE B1/B2/B3/B4/B5/B6
  0003_...                      # append-only from here
  checksums.sha256              # sha256 of every file; CI fails on drift
```

Ordering is lexical on the zero-padded number. A migration is immutable once applied. `schema_meta.
schema_version` is incremented by every migration and is the number `/health` reports. `0002` must add,
at minimum:

| Change | Reason |
|---|---|
| `schema_meta.sync_epoch INTEGER NOT NULL DEFAULT 1` | Catalog generation; `/v1/catalog` returns it and `410` on a mismatch. Bumped on restore/re-create. |
| `bills.payload_sha256 TEXT` | Replay with the same id and a different body is detectable (`id_conflict`). |
| `bills.subtotal_minor`, `bills.tax_minor` | Header identity: `total = subtotal + tax + rounding`. |
| `bill_lines.taxable_minor`, `price_includes_tax`, `hsn` | Line identity: `line_total = taxable + cgst + sgst + cess`. |
| `sales_daily(store_id,business_date,item_id,qty_base,amount_minor)` | Month reports read the rollup; day reports read lines. |
| `store_settings.override_ceiling_pct`, `override_ceiling_minor` | Per-store ceiling; env vars become defaults only. |
| `item_prices.store_key` generated as `IFNULL(store_id,'')` + `UNIQUE(item_id, store_key, qty_base, version)` | API/terminal price identity; SQLite treats NULLs as distinct, so the sentinel key is required for the default scope. |
| `approval_grants.used_at`, `used_bill_id`, `used_line_no` + unique use index | One grant cannot cover unlimited lines. |
| `staff_sessions` (`auth_method`, `assertion_verified_at`), `bills.staff_session_id`, `bills.unverified_staff`, `bills.unverified_shift_id`, `uq_staff_sessions_live` | Server-verifiable online shift attribution; one live session per terminal; PIN sessions and unknown shifts are preserved and flagged, never silently dropped. |
| `catalog_rebuild(generation, start_seq, end_seq, complete, started_at)` | Chunked re-seed/compaction is invisible until `complete=1`; publish the new floor/epoch only after the whole re-seed commits. |
| `devices`/`enrol_codes` composite store/terminal FKs and `UNIQUE(install_id)` | A credential cannot cross stores; one install cannot enrol twice. |

Deploy gate: the staff routes, the `staff_sessions` push, and the paged catalog snapshot must not
deploy before `0002` is applied on every tenant. The seed's fixed `catalog_changes` seqs 1..14 are
fresh-DB only: guard them with `WHERE NOT EXISTS (SELECT 1 FROM catalog_changes)` or a provisioning
flag, or a re-run against a pruned DB resurrects old seqs that the floor says are gone.

Apply per tenant, one tenant at a time, canary first:

```sh
bun run tenant:migrate --slug <slug>   # wraps:
# wrangler d1 migrations apply pos-<slug> --remote --config deploy/tenants/<slug>.toml
# then: SELECT schema_version FROM schema_meta; SELECT COUNT(*) FROM schema_migrations/checksums
# then: GET https://api-<slug>.<zone>/health -> schema_version == latest
```

Deploy order is migrate → deploy Worker → smoke test. Migrations are expand-only for the rollout
window: add tables/columns, backfill idempotently, and only remove/rename in a later migration after
every tenant reports the new version. A Worker rollback must still work against the newer schema.
Rollback of a migration is a compensating forward migration, not a down file. Time Travel restores the
database; after any restore run the epoch bump and catalog rebuild (§4) or terminals will silently keep
an old catalog.

Local: `wrangler d1 migrations apply pos-dev --local --config deploy/tenants/dev.toml`.

## 4. Provisioning a new franchisee

Run `bun run provision --slug <slug> --name "..." --owner-email ... --stores ...`. Steps are resumable
via `deploy/state/<slug>.json`; every create step is get-or-create, every seed is `INSERT OR IGNORE`,
every verification is read-only.

| # | Step | Failure point | Recovery |
|---|---|---|---|
| 1 | Validate slug (`^[a-z][a-z0-9-]{2,30}$`), tenant uniqueness, `wrangler whoami`, zone/DNS access, owner email | Nothing created | Abort; fix input/permission |
| 2 | `wrangler d1 create pos-<slug>`; capture `database_id` | Name collision or account permission | If ours, resume from state; else abort before KV/secrets |
| 3 | `wrangler kv namespace create POS_CACHE_<slug>`; capture id | KV quota/permission | D1 is empty and harmless; re-run is idempotent; print cleanup |
| 4 | Render `deploy/tenants/<slug>.toml`; `wrangler deploy --dry-run` | Missing var/template, invalid config | No deploy; fix template and re-render |
| 5 | Generate 3 secrets; store in the password manager/state; `wrangler secret put` each | Partial secret set | Re-run all three; pre-launch regeneration is safe |
| 6 | Apply `0001`+`0002` to the new D1 | SQL error / checksum drift | D1 is empty: drop and recreate, or Time Travel; never edit an applied file |
| 7 | Seed in one D1 batch: `UPDATE schema_meta SET tenant_id=?, app_id=?, provisioned_at=?`; rename seed store; set store settings/numbering/terminal config; seed the **complete** catalog (units, tax rates, store, settings, numbering, config, every item/price/identifier); create the owner with a generated one-time password (`must_change_password=1`); append catalog change rows and advance `catalog_seq.last_seq` atomically. A fresh terminal must never see an empty catalog. | Unique constraint or malformed JSON | Batch rolls back; fix inputs and re-run; print the one-time password once |
| 8 | `wrangler deploy` the Worker | Build/binding error | No traffic on first deploy; fix and redeploy; later deploys keep the previous version |
| 9 | Smoke: `/health` (200, `app_id`/`tenant_id`, `schema_version`), `/` (SPA), owner login, `/v1/admin/me`, mint an enrol code, test-client enrol + catalog snapshot + fake bill push | Any check fails | Do not hand over; roll back the Worker; keep D1 for diagnosis |
| 10 | Custom domain/DNS; wait for TLS; repeat `/health` on the public host | Cert/DNS pending | Smoke via workers.dev; retry; do not issue the URL until TLS is active |
| 11 | Alerts: Worker 5xx > 1%, D1 > 70% of 10 GB, cron failure, `/health` non-200; add uptime check | Alert missing | Handover blocked until the check exists |
| 12 | Write the tenant registry entry (slug, ids, host, app/tenant ids, versions); print handover sheet: admin URL, owner email, one-time password (once), store/terminal ids, support contact | Registry write fails | Deployment works but is undocumented; fix before handover |

Adding a store later: insert `stores`, `store_settings`, `numbering_policy(scope='store')`, a terminal
and `terminal_config`; append `store`, `store_settings`, `numbering_policy` and `terminal_config` change
rows and advance `catalog_seq.last_seq` in the same batch. No Worker/D1 creation. Adding a terminal is
an admin action (`POST /v1/admin/terminals`, then an enrol code), not provisioning.

Decommission: export the D1, revoke all devices, export audit rows, delete the Worker, KV and D1 after
the retention period. Keep the registry entry marked `retired` for a year.

## 5. Shared vs per tenant

| Component | Scope | Blast radius if it breaks |
|---|---|---|
| Worker code + admin SPA build | shared version, per-tenant deploy | A bad release can break sync/admin for every tenant on that version; **offline billing continues** on local SQLite and the outbox. Mitigate with a canary tenant, per-Worker rollback, and never shipping a schema-breaking migration in the same release. |
| Migration files | shared | Highest-risk artifact: a bad migration can damage every tenant's schema. Expand-only, checksum guard, canary tenant, Time Travel restore. Per-tenant D1 bounds the damage to one franchisee if the rollout is sequential. |
| Provisioning/release scripts | shared | A bug can target the wrong D1. The Worker boot check (`schema_meta.tenant_id` vs `TENANT_ID`) and the rendered per-tenant config stop a cross-tenant deploy from serving. |
| Cloudflare account, zone, DNS | shared | Account/zone outage or a bad DNS change takes all hostnames offline. Terminals retry; data stays local. Keep workers.dev smoke URLs and a status page. |
| Control-plane registry (`tenants.json`) | shared | Used only by tooling, never in the request path. A wrong id breaks provisioning/deploys for that tenant, not live traffic. |
| Monitoring/log pipeline | shared | Blind operations; no data loss. Per-tenant alerts still route. |
| JWT/session/enrol secrets | **per tenant** | Never share: a shared key would make tenant A's structurally valid token testable at B. Per-tenant secrets make it invalid before the tenant check. |
| D1, KV, cron, assets, rate limits, hostname | **per tenant** | Failure is contained to one franchisee. No request crosses a tenant boundary. |
| Report/query logic | shared code, per-tenant data | Wrong numbers for all tenants; bills/lines are immutable, so a fix is a redeploy plus a recompute, never a rewrite. |
| `units`/`tax_rates` reference seed | per-tenant data | A bad seed is a per-tenant data fix; no runtime sharing. |

## 6. Versioning and `/health`

```json
{"status":"ok","app_id":"pos-prod-kukatpally","tenant_id":"tnt_kukatpally",
 "api_version":"v1","build":{"version":"1.8.0","commit":"a1b2c3d","deployed_at":1763100000},
 "schema_version":2,"required_schema_version":2,
 "min_terminal_version":"1.4.0","latest_terminal_version":"1.5.0",
 "catalog":{"epoch":1,"head":8431,"alloc_head":8431,"pruned_below":0,"reset_to":0},
 "lease_days":7,"server_time":1763107200}
```

- `schema_version` = `MAX(schema_meta.schema_version)`; `required_schema_version` is a build constant.
  DB older → `503 schema_mismatch` on every route except `/health` (which reports it); DB newer is
  allowed only under the expand-only rule and is reported as `schema_ahead: true`.
- Boot guard: compare `schema_meta.tenant_id`/`app_id` to the env constants. Mismatch →
  `503 tenant_mismatch`; no data route runs.
- `min_terminal_version` is enforced on device routes via `X-Client-Version`; below it → `426
  upgrade_required` (heartbeat and `/health` exempt). Billing continues offline; sync pauses.
  `latest_terminal_version` drives the upgrade banner. Versions are self-reported and are an operational
  gate, not a security control.
- `catalog.epoch` = `schema_meta.sync_epoch`; `head` = `MAX(seq)` over `catalog_changes` (committed,
  not the allocator); `alloc_head` = `catalog_seq.last_seq`; `pruned_below` = the lowest retained seq;
  `reset_to = max(0, pruned_below - 1)`. The `catalog` object on `/health`, `/v1/enroll` and
  `/v1/token` is **diagnostic only**: a new terminal MUST start at `since=0` or `?snapshot=1`, never
  at the returned `head`. Terminals that store an older epoch or `since < reset_to` get `410` and a
  staged snapshot.
- `sync_epoch` changes ONLY on a Time Travel restore, database re-create or destructive migration —
  **not** on routine compaction. Compaction advances `pruned_below`/`catalog_rebuild`; terminals below
  floor reset, terminals at/above it keep pulling deltas. `sync_epoch` is an INTEGER: bump it to the
  pre-restore maximum captured before the restore + 1; never `restored_epoch + 1`, which can collide
  with an epoch a terminal already has and defeat the sticky reset.
- API version is the `/v1` path. Additive fields only within v1; clients ignore unknown fields. A
  breaking change ships `/v2` and the old router stays until every tenant's heartbeats report a
  `/v2`-capable terminal version.
- Admin SPA version = Worker version (same deploy); it reads `/health` and refuses to talk to an API
  whose `app_id` differs from its build config.

## 7. Capacity and operations

- D1: 10 GB/db, one writer, 30 s query, 50 MB result. A busy store writes ~1–2 KB/bill; 10 GB is
  millions of bills. Reports are range-capped (92 days) and multi-day ranges read `sales_daily`.
  Alert at 70% and decide sharding before it is urgent.
- Bill push is one `db.batch()` per bill; 100 bills = 100 batches (well inside the 1000-queries per
  invocation on Paid). Catalog pulls page at ≤500 changes and ≤8 MB; no unbounded response.
- Cron: cleanup only in v1. Catalog compaction is a manual/CLI operation and must run after every
  Time Travel restore. It appends a **complete** re-seed of every catalog document (including inactive
  rows, so the terminal can retire stale aliases/users) at new seqs. If it fits one D1 batch, it also
  advances `pruned_below` in that same transaction. If it is chunked, it writes a `catalog_rebuild`
  row, marks `complete=1` only after the last chunk, and only then publishes the new floor; a terminal
  must never 410 into a half-written generation. Routine compaction does **not** bump `sync_epoch` —
  terminals at/above the new floor keep pulling deltas. A 410 reset returns
  `reset_to = pruned_below - 1`, so `since=reset_to` pulls the first retained row and
  `since=reset_to-1` is stale. Compaction is not a cron job until the rebuild is proven on a copy.
- Secrets rotation: `DEVICE_JWT_SECRET` invalidates device JWTs for ≤5 min; terminals refresh from the
  device credential, no re-enrol. `SESSION_PEPPER` invalidates all admin sessions; users re-login.
  `ENROL_CODE_PEPPER` invalidates unused codes only. Rotate one tenant at a time.
- Restore runbook: Time Travel restore → **before any terminal retries**, bump `schema_meta.sync_epoch`
  (the 410 is sticky even if `head` later catches up) → rebuild `sales_daily` from bills → verify
  `catalog_seq`/`catalog_rebuild` and the seeded catalog → `/health` → confirm a terminal gets `410`
  and completes a staged snapshot. Never restore without the epoch bump.

## 8. Decisions and ambiguities

1. Static assets on the Worker, not a separate Pages project, so the session cookie stays same-origin.
2. One KV namespace per tenant with prefixes; KV is cache-only, never money or auth truth.
3. No Queue in v1; per-bill D1 batches and bounded reports cover the load.
4. Tenant configs are rendered files, not wrangler environments, so a tenant's D1/KV/hostname cannot be
   selected by the wrong `--env`.
5. `0002_must_land.sql` is required before the API contract in `api-routes.md` is fully enforceable
   (`sync_epoch`, `payload_sha256`, header/line tax identity, `sales_daily`, per-store ceilings,
   `store_key`, grant consumption, `staff_sessions`/`staff_session_id`, composite device FKs).
6. Two-step enrolment approval is a deployment option (TRIAGE C), not in the v1 route table; the
   credential is issued at enrol and the code is single-use.
7. Owner TOTP MFA is a launch gate tracked outside this inventory; v1 login has one factor.
