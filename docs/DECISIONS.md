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
