# Grocery billing POS: design summary

Status: design only. No code written. Diagram: `.archify/architecture-grocery-pos-20261112-120000/grocery-pos.html` (still labels the UI "Wails"; update to v3 / React + shadcn/ui if wanted).

## Requirements

- Grocery store billing. Records every bill; reports per item sold at end of day and end of month. No payments.
- Items have quantities in g, kg, ml, l, pcs. Prices are not linear (1 kg tamarind = 40, 500 g = 25).
- Aliases: a short code (e.g. `T500`) fills in item and quantity.
- Works offline. Terminals write to local SQLite first and sync to the cloud in batches.
- Multiple stores per enterprise. Owner updates a price once and all stores get it. One Worker serves every tenant, routed by Host; one D1 database per enterprise.
- Replaces GoFrugal (Java, MS SQL only, paid, poor packaging).

## Decisions

| Area | Choice | Why |
|---|---|---|
| Desktop UI | Wails (v3.0.0 stable since 2026-10-26; pin exact version) + Go | Owner does not know Rust. Rune rejected: young API, public repo, Rust unfamiliar. |
| Frontend | React + TypeScript + shadcn/ui + Tailwind, TanStack Table and Virtual | Most help available, accessible, code lives in repo. UI SDK (Vercel beta) rejected for now. Mantine is the alternative if admin tables dominate. |
| Local DB | SQLite, WAL, `synchronous=FULL` on the billing connection (Go: `modernc.org/sqlite`) | Embedded, no server, no MS SQL. FULL costs about 30x the write rate of NORMAL (41 vs 1,235 bills/s) and is the only mode where a committed bill survives a power cut. |
| Backend | Cloudflare Worker (Hono, TypeScript), one Worker for every tenant routed by Host + D1, one database per enterprise | Compute pooled, data sharded. Workers Paid is required: Free caps D1 at 500 MB and its daily row quotas were exhausted by the measurement run. Per-tenant Worker only as the contracted exception (D20). |
| Wire protocol | REST + JSON over HTTPS, `/v1/...` | Workers cannot serve native gRPC; traffic is small batches. |
| Bun | Tooling only (installs, scripts). Workers run on workerd, not Bun/Node. | |
| PocketBase/TrailBase/Dexie | Not used | Server-side or browser-only; do not fit offline terminals. |

## Data model

- Quantities are integers in base units: grams, millilitres, pieces. Convert only for display. Item has one dimension (mass, volume, count).
- Money: integer minor units.
- `items`: id (client UUID), name, dimension, pricing_mode, active, updated_at. Never hard-delete.
- `item_prices(item_id, qty_base, price_minor, version, valid_from, valid_to)`: SCD2 history, server clock for `valid_from`.
- Pricing modes per item: `fixed_only` (listed quantities and multiples), `tiered` (greedy decomposition, remainder pro-rated from smallest tier, half-up rounding once), `linear`. Start with `fixed_only` and `linear`. Pricing is one pure function shared by tests.
- `item_aliases(alias PRIMARY KEY, item_id, qty_base NULL, updated_at, active)`. Alias is normalised (trim, uppercase) and unique. Exact alias match wins over name search. Deactivating an item deactivates its aliases. Barcodes could share the table with a `kind` column.
- `bills(id UUIDv7, store_id, terminal_id, user_id, staff_session_id, business_date, business_day_start_minutes, local_seq, created_at, total_minor, rounding_minor, synced_at, voided)`; unique `(store_id, terminal_id, local_seq)` on the server. `business_day_start_minutes` is copied at sale time, so the day a bill belongs to survives a settings change.
- `bill_lines(bill_id, item_id, item_name, dimension, qty_base, price_version, price_breakdown JSON, line_total_minor)`. Name and price are copied onto the line. `line_total_minor = taxable + cgst + sgst + cess + igst`, enforced by trigger, and no grant or flag can bypass it.
- Reports sum `qty_base` and `line_total_minor` from lines for a single day, grouped by `item_id`, filtered by `business_date`, excluding voided. They never recompute from the price table, so stale terminals do not change totals. Multi-day and month reports read `sales_daily`, a per store, day and item rollup that triggers maintain on insert and void.
- Bills are immutable. A correction is one `bill_voids` row, never an edit or a delete; an AFTER INSERT trigger flips `voided` and decrements the rollup. Un-voiding is impossible by trigger.

## Sync

- Bill and outbox row commit in one SQLite transaction. Billing never depends on the network or the token.
- Per-row `synced_at` flag (not a high-water mark). At most 100 bills per request, one batch per bill, lines before the header; the request stays under 1,000 D1 calls because a batch counts as one. Worker inserts `ON CONFLICT(id) DO NOTHING` and returns accepted ids; terminal marks only those. Crash-safe.
- Retry: exponential backoff with jitter (1 s doubling, cap a few minutes). Refused bills are parked (about an hour), not hammered.
- Catalog pull uses a cursor, advanced only after rows are applied.
- Order within a pass: refresh token, push bills, pull catalog. Sync items before bills or drop the server FK.
- Stale price detection: Worker compares each line's `price_version` to the version valid at `created_at` and sets a `stale_price` flag. Diagnostic only, not for money.
- Show UI: unsynced count, age of oldest, last sync time, "prices last updated" warning.
- Implement as an explicit state machine: Unenrolled, Idle, Syncing, Backoff, ReauthNeeded. Pure `step(state, event) -> effects`.

## Identity and auth

- UUIDv7 bill ids generated on the terminal. Store and terminal are columns, taken from the token by the Worker (never trusted from the body). The tenant comes from the request Host, and `schema_meta.tenant_id` must match it.
- Display number like `S1-T2-000431`, local counter incremented in the same transaction.
- Terminals hold only the Worker base URL and a device credential, never a D1 credential.
- Enrol with a one-time code (about 15 minutes), then the owner approves the device in admin; `/v1/token` returns `403 approval_pending` until then. The device credential (long-lived, OS keychain, hashed on server, revocable) exchanges for a 5 min JWT.
- Token expiry pauses sync only. Show a non-blocking banner.
- Worker returns server time on sync; warn on clock skew.
- Cloned disk images: install id in keychain, re-enrol on mismatch.
- Admin web is Better Auth (email/password, Google, TOTP) with its own tables; roles and store scope stay ours. Cashiers have no admin credential at all, only a PIN in `staff_credentials` for offline attribution.
- Endpoints: `POST /v1/enroll`, `/v1/token`, `/v1/bills`, `GET /v1/catalog?since=`, `/v1/reports`, `POST /v1/terminals/:id/revoke`, `/health` (version).

## Operations

- Provisioning per tenant: create the D1, run migrations, insert the first store, terminal and numbering policy with fresh UUIDv7 ids, register the binding and Host in `infra/tenants.json`, deploy. Migrations must be backward compatible; every migration sets `schema_meta.schema_version`.
- Release order is migrate every tenant, then deploy the Worker. A Worker declares the minimum schema it runs against.
- Keep unsynced rows until acknowledged; keep synced rows about 30 days on the terminal.
- Archival is v1: per tenant, a monthly job exports a store-month to R2, verifies row counts and totals against D1, then deletes below `schema_meta.archive_before`. R2 keeps at least 6 years. A realistic 4-line bill is 4,961 bytes on D1, so five stores at 600 bills a day reach 10 GB in about 1.8 years without it.
- Alert at 70% of 10 GB. Workers Paid is required in production.
- Plan code signing (Windows certificate, macOS notarisation) and an update path.

## Measurements (boat.dev Linux VM, cgroup caps; VM now deleted)

- Go core (SQLite + sync against a fake Worker), 20,000 bills: 1 GB / 0.5 CPU, NORMAL: 1,235 bills/s, save p50 73 us, p99 34 ms, peak 23 MB. 8 GB / 2 CPU: 1,099 bills/s, same latency. FULL sync mode: about 41 bills/s, p50 17 ms (2,000 bills). Disk commits, not CPU, are the limit. Database files were contaminated by an earlier killed run, so day-report times (166 to 426 ms) are only indicative.
- Local SQLite size, same bill: 4,573 bytes for a realistic 4-line bill, 9,659 for 10 lines; about 25% less without `inventory_movements` and `idx_bill_lines_report`.
- Real D1, 2026-11-14: 4,961 bytes per realistic 4-line bill (3,000 bills: 0.71 MB to 15.60 MB), about 42 rows written per bill including index entries. Archiving 900 bills freed about 65% of their footprint. The cascade delete without `idx_inventory_movements_line` took 8,048 ms and read 73,463,700 rows; with it, 55 ms and 33,600 rows. `VACUUM` is refused inside a transaction and space comes back on commit. The Free plan's daily quotas were exceeded by the run, so production needs Workers Paid. Benchmark: `docs/capacity-bench.py`.
- Idle UI, 1 GB / 0.5 CPU cap: Wails v2 default template 220 MB total (app 168, WebKit page 152, network 48 RSS; shared pages), under 1% CPU. Rune `hello`: 80 MB, under 1% CPU (software Vulkan). Binaries: Wails 8.8 MB, Rune 39 MB.
- External: Dawloom measured Wails v2.16 on Windows 11 at 205 to 209 MB (7 processes); a Zenn 15-framework benchmark (macOS) puts Wails around 320 to 600 MB on heavy screens.
- Not measured: Wails v3, Windows WebView2 here, a real screen, spinning disks, real network latency to Cloudflare, scrypt login CPU on a Worker.

## Open questions

- Gapless legal invoice numbering per store (tax rules) vs per-terminal sequences.
- Single terminal or several per store; owner reporting in the Wails app or a web dashboard.
- Loose vs packed forms of one product: one item with variants, or separate items.
- Tailwind v4 browser floor vs old WebView2 / WebKitGTK on shop machines; fall back to v3 if needed.
- Unsynced bills are lost if the disk dies; consider local backups.
- Price revisions (when an edit closes a version and opens the next) must be decided before the price editor is built.
- R2 retention: 6 years assumed for GST records; the owner should confirm with the accountant.
- Google's limit on redirect URIs per OAuth client decides whether one client can serve every tenant.

## Next steps

1. Choose project location (suggested: new git repo, e.g. `~/Documents/pos`, outside the public Rune repo).
2. Build the Go core: schema and migrations, units, pricing (with tests), aliases, `create_bill`, outbox, sync client and state machine, tested against a fake Worker.
3. Hono Worker: enrolment, token, bills, catalog, reports, plus provisioning and migration scripts.
4. Wails v3 shell with React and shadcn/ui: sale screen (alias box), item and price admin, reports.
5. Test on the oldest target Windows machine (WebView2) and measure a real screen.
