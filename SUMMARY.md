# Grocery billing POS: design summary

Status: design only. No code written. Diagram: `.archify/architecture-grocery-pos-20261112-120000/grocery-pos.html` (still labels the UI "Wails"; update to v3 / React + shadcn/ui if wanted).

## Requirements

- Grocery store billing. Records every bill; reports per item sold at end of day and end of month. No payments.
- Items have quantities in g, kg, ml, l, pcs. Prices are not linear (1 kg tamarind = 40, 500 g = 25).
- Aliases: a short code (e.g. `T500`) fills in item and quantity.
- Works offline. Terminals write to local SQLite first and sync to the cloud in batches.
- Multiple stores per franchisee. Owner updates a price once and all stores get it. Each franchisee gets their own D1 and Worker.
- Replaces GoFrugal (Java, MS SQL only, paid, poor packaging).

## Decisions

| Area | Choice | Why |
|---|---|---|
| Desktop UI | Wails (v3.0.0 stable since 2026-10-26; pin exact version) + Go | Owner does not know Rust. Rune rejected: young API, public repo, Rust unfamiliar. |
| Frontend | React + TypeScript + shadcn/ui + Tailwind, TanStack Table and Virtual | Most help available, accessible, code lives in repo. UI SDK (Vercel beta) rejected for now. Mantine is the alternative if admin tables dominate. |
| Local DB | SQLite, WAL, `synchronous=NORMAL` (Go: `modernc.org/sqlite`) | Embedded, no server, no MS SQL. |
| Backend | Cloudflare Worker (Hono, TypeScript) + D1, one Worker and one D1 per franchisee | Hard isolation, per-tenant secrets, no routing code. Keep handlers tenant-agnostic so a shared Worker is possible later. |
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
- `bills(id UUIDv7, store_id, terminal_id, business_date, local_seq, created_at, total_minor, synced_at, voided)`; unique `(store_id, terminal_id, local_seq)` on the server.
- `bill_lines(bill_id, item_id, item_name, dimension, qty_base, price_version, price_breakdown JSON, line_total_minor)`. Name and price are copied onto the line.
- Reports sum `qty_base` and `line_total_minor` from lines, grouped by `item_id`, filtered by `business_date`, excluding voided. They never recompute from the price table, so stale terminals do not change totals.
- Bills are immutable. Corrections are void records.

## Sync

- Bill and outbox row commit in one SQLite transaction. Billing never depends on the network or the token.
- Per-row `synced_at` flag (not a high-water mark). Batches of about 100. Worker inserts `ON CONFLICT(id) DO NOTHING` and returns accepted ids; terminal marks only those. Crash-safe.
- Retry: exponential backoff with jitter (1 s doubling, cap a few minutes). Refused bills are parked (about an hour), not hammered.
- Catalog pull uses a cursor, advanced only after rows are applied.
- Order within a pass: refresh token, push bills, pull catalog. Sync items before bills or drop the server FK.
- Stale price detection: Worker compares each line's `price_version` to the version valid at `created_at` and sets a `stale_price` flag. Diagnostic only, not for money.
- Show UI: unsynced count, age of oldest, last sync time, "prices last updated" warning.
- Implement as an explicit state machine: Unenrolled, Idle, Syncing, Backoff, ReauthNeeded. Pure `step(state, event) -> effects`.

## Identity and auth

- UUIDv7 bill ids generated on the terminal. Store and terminal are columns, taken from the token by the Worker (never trusted from the body).
- Display number like `S1-T2-000431`, local counter incremented in the same transaction.
- Terminals hold only the Worker base URL and a device credential, never a D1 credential.
- Enrol with a one-time code (valid about 15 minutes), or owner login. Device credential (long-lived, OS keychain, hashed on server, revocable) exchanges for a short-lived access token (JWT).
- Token expiry pauses sync only. Show a non-blocking banner.
- Worker returns server time on sync; warn on clock skew.
- Cloned disk images: install id in keychain, re-enrol on mismatch.
- Owner phone needs a real login, separate from device tokens.
- Endpoints: `POST /v1/enroll`, `/v1/token`, `/v1/bills`, `GET /v1/catalog?since=`, `/v1/reports`, `POST /v1/terminals/:id/revoke`, `/health` (version).

## Operations

- Provisioning script per franchisee: create D1, run migrations, set secrets, deploy Worker. Migrations must be backward compatible; record schema version.
- Keep unsynced rows until acknowledged; keep synced rows about 30 days.
- Plan code signing (Windows certificate, macOS notarisation) and an update path.

## Measurements (boat.dev Linux VM, cgroup caps; VM now deleted)

- Go core (SQLite + sync against a fake Worker), 20,000 bills: 1 GB / 0.5 CPU, NORMAL: 1,235 bills/s, save p50 73 us, p99 34 ms, peak 23 MB. 8 GB / 2 CPU: 1,099 bills/s, same latency. FULL sync mode: about 41 bills/s, p50 17 ms (2,000 bills). Disk commits, not CPU, are the limit. Database files were contaminated by an earlier killed run, so day-report times (166 to 426 ms) are only indicative.
- Idle UI, 1 GB / 0.5 CPU cap: Wails v2 default template 220 MB total (app 168, WebKit page 152, network 48 RSS; shared pages), under 1% CPU. Rune `hello`: 80 MB, under 1% CPU (software Vulkan). Binaries: Wails 8.8 MB, Rune 39 MB.
- External: Dawloom measured Wails v2.16 on Windows 11 at 205 to 209 MB (7 processes); a Zenn 15-framework benchmark (macOS) puts Wails around 320 to 600 MB on heavy screens.
- Not measured: Wails v3, Windows WebView2 here, a real screen, spinning disks, real network latency to Cloudflare.

## Open questions

- Gapless legal invoice numbering per store (tax rules) vs per-terminal sequences.
- Per-store price overrides: nullable `store_id` on price rows.
- Single terminal or several per store; owner reporting in the Rune/Wails app or a web dashboard.
- Loose vs packed forms of one product: one item with variants, or separate items.
- Tailwind v4 browser floor vs old WebView2 / WebKitGTK on shop machines; fall back to v3 if needed.
- Unsynced bills are lost if the disk dies; consider local backups.
- Whether to void bills and how reports subtract them.

## Next steps

1. Choose project location (suggested: new git repo, e.g. `~/Documents/pos`, outside the public Rune repo).
2. Build the Go core: schema and migrations, units, pricing (with tests), aliases, `create_bill`, outbox, sync client and state machine, tested against a fake Worker.
3. Hono Worker: enrolment, token, bills, catalog, reports, plus provisioning and migration scripts.
4. Wails v3 shell with React and shadcn/ui: sale screen (alias box), item and price admin, reports.
5. Test on the oldest target Windows machine (WebView2) and measure a real screen.
