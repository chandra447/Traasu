# Adversarial review: sync and D1 limits

**Verdict: ISSUES — 13 blockers, 12 major, 1 minor.**

I reviewed `docs/DESIGN-BRIEF.md` and Obsidian notes 03, 04, 05 and 09 as a specification. There is no
implementation to inspect, so every defect below names the most natural implementation of the text and the
exact sequence that breaks it. A defect is closed only by changing the text or the schema, not by assuming a
careful coder.

The short version: the design has a good offline instinct, but the checkpoint is not actually defined. The
brief asks for a cursor that is "monotonic, gap-free, and cheap to index" and never says who allocates it.
The notes then offer three mutually incompatible candidates — `items.updated_at`, `item_prices.version`, and a
`catalog_version` bumped by "every write" — and the schema has no column that can carry the third. Everything
downstream (tombstones, SCD2, partial batch acceptance, reports) inherits that hole. In addition, three
independent bugs can corrupt money or stop the cloud copy from ever completing: duplicate `bill_lines` on
retry, one bad bill rolling back a 100-bill batch, and a store override that a terminal cannot see being
removed.

The D1 arithmetic is the other hard stop: at the stated one-database-per-franchisee shape, a five-store
franchisee reaches the unraisable 10 GB cap in roughly 6–12 months.

## Defect index

| # | Area | Severity | One line |
|---|---|---|---|
| 1 | Cursor | blocker | No allocator; every candidate key can lose a committed change |
| 2 | Cursor | blocker | Read-modify-write allocation races; two writers get the same value |
| 3 | Cursor | blocker | Catalog read is not a snapshot; a change can commit behind the cursor |
| 4 | Cursor | blocker | Time Travel restore rewinds the sequence; terminals go permanently stale |
| 5 | Cursor | blocker | No retention bound or full-resync path for a 90-day-offline terminal |
| 6 | Gaps | blocker | Seq 10 can arrive before 9; page boundaries split a price version; behaviour undefined |
| 7 | Tombstones | blocker | Deletes have no tombstone; a removed override/alias/price is invisible |
| 8 | Tombstones | major | Alias reassignment and item deactivation depend on apply order |
| 9 | Price SCD2 | blocker | `store_id NULL` defeats the unique key; duplicate franchise defaults |
| 10 | Price SCD2 | major | Future-dated `valid_from` vs terminal clock; stale check uses the same skew |
| 11 | Price SCD2 | blocker | Payload omits `store_id`/validity; store overrides leak and precedence is undefined |
| 12 | Batch push | blocker | `bill_lines` has no identity; a retry duplicates every line |
| 13 | Batch push | blocker | One `db.batch()` for 100 bills; one bad bill rolls back all 100 |
| 14 | Batch push | blocker | `ON CONFLICT(id)` vs the `local_seq` index; accepted list lies; reuse after restore |
| 15 | Batch push | major | 409/parking is undefined; invoice allocation is not idempotent |
| 16 | Batch push | major | Batch sizing ignores 100-parameter and 100 KB statement limits |
| 17 | D1 | blocker | 10 GB hard cap vs one database per franchisee; no growth path |
| 18 | D1 | major | Single writer; a month report blocks all pushes |
| 19 | D1 | major | Read replication without a session breaks read-after-write |
| 20 | D1 | major | Time Travel 30 days, 5 GB import, 50 MB result; no restore/export plan |
| 21 | Reports | major | Month report scans `bill_lines`; 30 s limit and write blocking |
| 22 | Reports | minor | `GROUP BY item_name` splits renamed items; integer division truncates |
| 23 | Clock | major | `business_date` from the terminal clock; midnight/month-end bills land on the wrong day |
| 24 | Clock | major | Bills arriving after a report make it silently stale |
| 25 | Clock | major | `store_settings`/day-start is not in the catalog contract |
| 26 | Clock | major | `synchronous=NORMAL` can lose the last committed bill on power loss |

---

## 1. Cursor correctness

### 1. The checkpoint has no allocator, and every available key loses changes — BLOCKER

**Scenario.** An owner creates a new item and a price tier for it in the same second. A terminal polls
`GET /v1/catalog?since=<cursor>` after both commits. The handler uses `WHERE updated_at > :since` on
`items` and `item_prices`, or `WHERE version > :since` on prices. Depending on the comparison (`>` or
`>=`) and the tie-breaker, a row is either returned twice forever or never returned.

**Why it fails.** The design never says what the integer cursor is. The artifacts imply three different
things, and none is monotonic across the catalog:

- `03 D1 schema impact` indexes `items(updated_at)` and `item_prices(version)` under the comment "catalog
  pull cursor"; `04 Hono API impact` returns `cursor: 8412`; `09 Route map` says "every write bumps a
  monotonic `catalog_version`; terminals pull on `since`." There is no `catalog_version` column or table
  anywhere.
- `updated_at` is a wall clock. Two changes can share a millisecond. With `> cursor`, whichever row is not
  the max timestamp in the response is skipped; with `>= cursor`, the max row is re-sent forever unless a
  tie-breaker is used, and a tie-breaker by `id` still loses a row that commits later with the same
  timestamp and a smaller id.
- If `updated_at` is written by the admin client rather than the server, a browser or phone with a clock
  three days slow writes a change in the past; every terminal whose cursor is already past that date never
  sees it.
- `item_identifiers`, `units`, `tax_rates` and `store_settings` have no `updated_at` or `version` column at
  all. `item_identifiers` and `tax_rates` have `active`, but a flag is not a cursor; `units` and
  `store_settings` have neither. A timestamp/version cursor cannot see an alias insertion or a tax-rate edit.
- `item_prices.version` is per item, not global. Item A at version 7 and item B at version 7 are different
  changes. A terminal that advances its cursor to 7 after reading A will never read B's version 7 if B
  commits later. `valid_from` is worse: a backdated correction row can have a `valid_from` older than the
  cursor and be invisible forever.

**Worked example.** At 10:00:00.500 the server commits an item rename; at 10:00:00.700 it commits a price
change. A pull whose item query sees the first commit but whose price query runs after the second returns
both, cursor = max(timestamps) = .700. A later backfill inserts a price version with `valid_from` =
09:59:00; the next pull asks for `valid_from > .700` and never returns it. The terminal keeps the old tier
set indefinitely, and no error is raised.

**Smallest fix.** Add one table, `change_log(seq INTEGER PRIMARY KEY AUTOINCREMENT, change_set_id TEXT NOT
NULL, table_name TEXT, row_key TEXT, op TEXT, payload TEXT NOT NULL, created_at INTEGER NOT NULL)` and write
one row per logical catalog change inside the same D1 transaction as the data change. The payload is the
full row (or full tier set) after the change, plus old keys for deletes. The cursor is `MAX(seq)` of the
change log, not a timestamp and not `item_prices.version`. Because D1 is a single writer and a `db.batch()`
is one transaction, committed `seq` order is commit order. "Gap-free" should be restated as "no committed
change may ever appear at or below a cursor the server has already returned"; a gap left by a rolled-back
`AUTOINCREMENT` row is harmless because no row was ever committed at that value. If any catalog table is
written outside this path, the change is invisible; enforce the path with an `AFTER INSERT/UPDATE/DELETE`
trigger or a single repository function plus a CI check. The terminal keeps billing from its old catalog
while any of this is being fixed; nothing here requires a network on the sale path.

### 2. Two writers can allocate the same counter or price version — BLOCKER

**Scenario.** Two owner tabs open the same item. Both read `MAX(version) = 7`. Tab A writes version 8 with
the 1 kg price; tab B writes version 8 with the 500 g price. D1 serializes the two writes, but it does not
serialize the two reads: they were separate statements. Both inserts succeed (no unique constraint on the
key as defined, or the key contains NULL — see defect 9). The terminal receives two different "version 8"
rows and picks one by `ORDER BY version DESC LIMIT 1`, which is arbitrary.

The same sequence applies to the global cursor if it is allocated with `SELECT counter; UPDATE counter` or
`SELECT MAX(catalog_version)+1` in application code: both requests read the same value and write the same
`seq`, and the terminal's `> cursor` misses one of the two changes.

**Why it fails.** D1's single writer serializes *queries*, not a read followed by a write issued as two
queries. The D1 foreign-key documentation states the relevant rule: "D1 runs every query inside an implicit
transaction" — each statement commits independently, so another request can interleave between the
`SELECT` and the `UPDATE`. A `db.batch()` is a transaction and is serialized as a unit; a JavaScript
read-then-write is not.

There is a second trap in the obvious fix. If the sequence is allocated in its own statement before the
data write, a rollback or a crash burns the value, and a later writer can commit a higher value while the
first writer is still in flight. The terminal then sees `seq 101` and advances its cursor; `seq 100`
commits afterwards and is never returned. SQLite's `AUTOINCREMENT` does not save you here either: "ROWID
values that were rolled back are ignored and can be reused" ([SQLite autoinc](https://sqlite.org/autoinc.html)).
Reuse is only harmless when the log row and the data row commit in the same transaction, so that a
rolled-back change leaves no committed row at that value.

**Worked example.** A: `SELECT MAX(version)` → 7. B: `SELECT MAX(version)` → 7. A: insert 8 (1 kg, ₹40).
B: insert 8 (500 g, ₹25). A terminal priced 750 g uses the 1 kg row at version 8 and cannot find the 500 g
row at version 8; its greedy decomposition falls back to the 1 kg tier and charges a different total than a
terminal that happened to receive B's row last. Reports copy the line totals, so the two terminals disagree
about the same basket.

**Smallest fix.** Make allocation and use one serialized unit:

- For the global cursor, let the `change_log` insert assign `seq` via `AUTOINCREMENT`, in the same
  transaction as the data change. A rollback removes both; a commit publishes both. Do not pre-allocate in
  a separate statement.
- For a price version, add `UNIQUE(item_id, store_key, qty_base, version)` and make the admin `PUT`
  conditional on the version the editor read (`If-Match: 7`). Allocate the new version as
  `MAX(version) + 1` and insert the whole tier set in one `db.batch()` with a guard
  (`INSERT ... SELECT ... WHERE NOT EXISTS (SELECT 1 FROM item_prices WHERE item_id = ? AND
  store_key = ? AND version = :new_version)`). Two editors both read 7 and both try 8; the first commits,
  the second's guard inserts zero rows, so it gets a 409 and re-reads. There is no separate counter, so a
  crash cannot burn a version or leave a gap, and the unique key is the backstop.
- If a global counter table is used for something else, `UPDATE ... RETURNING` in the same batch as the
  write, never a separate `SELECT` then `UPDATE` in JavaScript.

### 3. The catalog read is not a snapshot; a change can commit behind the cursor — BLOCKER

**Scenario.** The catalog handler issues the natural five queries: items, then prices, then identifiers,
then units, then config. Between the items query and the prices query, an admin renames an item and bumps
its `updated_at`/`change seq`. The items query saw the old name; the prices query (and the cursor) is
computed from the later state. The response's cursor is at or past the rename, so the terminal never asks
for the rename again.

**Why it fails.** D1 gives no cross-statement snapshot unless the statements are in one transaction. The
notes say the *client* applies the five arrays "in one SQLite transaction", but say nothing about the
*server* reading them in one. `04 Hono API impact` draws `select items, prices, identifiers, units, tax,
config` as a single arrow; a single arrow is not a single D1 query. If the handler uses `Promise.all` of
six prepared statements, that is six independent implicit transactions (the D1 FK page's wording is
explicit: every query runs in an implicit transaction). The result is a torn catalog: old item metadata
with new prices, or a new alias pointing at an item row that is not in the same response.

**Worked example.** Item `Tamarind` is renamed to `Tamarind Seedless` at seq 105. A terminal pulls with
cursor 104. The items SELECT runs just before seq 105 commits and returns the old name. The prices SELECT
runs after and returns a price change at seq 109. The response cursor is 109. The terminal applies the old
name and the new price; the rename at 105 is now permanently behind the cursor. No later pull returns it.
The cashier searches "Tamarind Seedless" and finds nothing; the item is effectively unsearchable under its
new name until a full refresh.

**Smallest fix.** Build the entire response from one `db.batch()` of SELECTs (a transaction), or from a
single SELECT over `change_log` where each row carries the full payload. If the response is built from base
tables, wrap every SELECT in the same batch; do not issue them as independent queries. Put the cursor read
in the same transaction as the data reads, so the cursor cannot describe a state the payload did not see.

### 4. Restore rewinds the sequence and leaves terminals permanently stale — BLOCKER

**Scenario.** On 14 November an owner publishes a bad price at 09:00; every terminal pulls it. At 10:00 an
operator restores the database to 13 November 10:00 with D1 Time Travel. The restore overwrites the
database in place; the change log, the `seq` counter, and the bad price all revert. Terminals hold a cursor
from 14 November 09:30. After the restore, new changes are allocated at values below that cursor, so
`WHERE seq > cursor` returns nothing. The terminals keep the bad price forever. Worse, the restored-away
price change has no compensating event at all: a restore is not an `UPDATE`, so the change log does not
record "this price went back to ₹40."

**Why it fails.** The design treats the checkpoint as a property of the data, but a point-in-time restore
replaces the data *and* the cursor allocator with an older state. D1 Time Travel is a destructive in-place
restore, not a branch: "Restoring a database to a specific point-in-time is a destructive operation, and
overwrites the database in place"; in-flight queries are cancelled; history is 30 days on Workers Paid
([D1 Time Travel](https://developers.cloudflare.com/d1/reference/time-travel/)). Nothing in the design
detects that the database's identity/generation changed.

A second variant: if the cursor is a wall-clock timestamp, the restore does not rewind the clock. New
changes get current timestamps and are visible, but the *reverted* changes are not events, so terminals
that already pulled the bad price never learn that the server went back. A timestamp cursor makes this
worse, not better.

**Worked example.** Terminal T2 cursor = 9,500. Restore to a point where the highest seq was 8,000. Owner
edits an item; the change log assigns 8,001. T2 asks for `> 9,500`; it gets an empty page and a cursor of
9,500. The item edit never reaches T2. The only symptom is a price that differs between two terminals.

**Smallest fix.** Put a `sync_epoch` in the cursor: `cursor = (epoch, seq)`. A restore is followed by a
mandatory post-restore step that writes a new epoch (outside the restored data; the runbook must run it
after every restore and after any database re-create). Any request whose epoch differs gets
`409 resync_required` plus a full snapshot. The full snapshot is applied in one local transaction (shadow
tables + short swap, so billing is not blocked for the whole load). Add a weekly catalog checksum/full pull
to catch drift that no event describes. The terminal keeps billing from its old catalog while the resync is
pending; the resync is best-effort and never gates a sale.

### 5. No retention bound or full-resync path for a 90-day-offline terminal — BLOCKER

**Scenario.** A terminal is offline for 90 days. Its cursor is seq 1,000. The change log has been pruned to
the last 30 days (or the database was restored), so the oldest retained row is seq 50,000. The terminal
asks for `> 1,000`; the server returns only the retained rows, starting at 50,000. Everything between 1,000
and 50,000 — price changes, new items, deactivated aliases — is silently missing. The terminal applies the
delta, advances to the max seq, and is now confidently wrong.

**Why it fails.** The brief says "advance the checkpoint only after the transaction commits", which handles
a crash during apply, but says nothing about the *server* not having the rows the cursor asks for. There
is no `resync_required` response, no minimum-retained-seq check, and no full-snapshot endpoint. Pruning is
inevitable: the change log competes with bills for the same 10 GB (defect 17). A 90-day offline window is
explicitly in scope, so the retention period must be at least 90 days or the server must detect the
underflow.

**Worked example.** T3 was last synced on 15 August. On 13 November it reconnects. The owner changed the
price of rice three times and deactivated two aliases in September. The pruned log returns only October
changes. T3 bills rice at the August price for the rest of the day, and scans an alias that now belongs to
a different item. The bills are accepted (prices are copied), so the server has no way to reject them; the
`stale_price` flag is a diagnostic, not a correction.

**Smallest fix.** Store `min_retained_seq` (or compute `MIN(seq)` from the log) and return
`409 resync_required` whenever `cursor < min_retained_seq - 1`, with a full-snapshot URL. A full snapshot
must be a consistent read and must replace the local catalog atomically. Build the new catalog in shadow
tables (`items_new`, `prices_new`, …) and swap them in one short transaction; a 100k-row delete/insert held
for the whole load would block the billing writer. Keep the change log for at least the maximum supported
offline window plus a margin, and document that a longer offline period triggers a full pull. This does not
break offline billing: the terminal can keep selling from the old catalog while the full pull is pending,
and a failed pull simply retries.

---

## 2. Gaps and reordering

### 6. A terminal can observe seq 10 before seq 9; `ORDER BY seq` is not enough — BLOCKER

**Scenario A — a page boundary splits a price version.** A price version consists of several tier rows
(1 kg, 500 g). The change log assigns seq 100 to the 1 kg row and seq 101 to the 500 g row. The terminal's
pull has a `LIMIT`/page size of 1 (or the response is cut at a 50 MB result limit, or the client applies
pages as they arrive). It applies seq 100, advances its cursor to 100, and bills a 750 g sale before seq
101 arrives. The pricing function has only the 1 kg tier; the greedy decomposition pro-rates the remainder
from the smallest *available* tier, which is now 1 kg, so the total differs from a terminal that received
both rows.

**Scenario B — a gap.** A change at seq 9 is allocated and its transaction is still in flight while seq 10
commits. A replica or a separate allocator returns 10 first. The terminal's behaviour is undefined: if it
advances to 10, it skips 9; if it demands contiguity, it waits for 9 forever (which is correct if 9 was
never committed, and wrong if it was).

**Why it fails.** The design says only "ORDER BY seq" and "advance after commit". Ordering gives you a
sequence, not isolation and not atomic change sets. Two separate problems are conflated:

1. **Delivery order.** D1's primary serializes writes, so committed seq order equals commit order *on the
   primary*. A read replica, or any allocator that is not in the same transaction as the data, breaks
   that. The design never pins catalog reads to the primary or to a bookmark.
2. **Change-set atomicity.** A logical change (a tier set, an item plus its aliases) is multiple rows. If a
   page boundary can fall between them, the terminal can bill with half a change set.

`ORDER BY seq` is sufficient only if (a) every change is one log row carrying the whole logical change, and
(b) the response is read from the primary (or a session at least as new as the cursor). Otherwise a
snapshot boundary is required: all statements in one `db.batch()`, and a page boundary only at a change-set
boundary.

**Worked example.** A 750 g sale of tamarind: correct price is 1 × 500 g (₹25) + 250 g pro-rated from the
500 g tier (₹12.50), total ₹37.50. With only the 1 kg tier (₹40) loaded, the remainder is pro-rated from
1 kg: 750 g × ₹40/kg = ₹30.00, or the fixed-only decomposition refuses the sale. Either way the bill is
wrong, and it is copied into `bill_lines` and reports.

**Smallest fix.** One `change_log` row per logical change, with a `change_set_id` and a JSON payload that
contains every row of the set (tier rows, item + alias deactivations). Order by `seq`; never split a change
set across pages. Read the catalog from the primary instance, or use `withSession(bookmark)` and carry the
bookmark; do not read the catalog from an unconstrained replica. On the client, apply each page in one
local transaction and advance the cursor in the same transaction; if a page contains a gap in the change
set, discard it and re-request from the previous cursor, do not advance. A gap in the *id space* left by a
rolled-back `AUTOINCREMENT` is not a gap in committed rows; document that the server may return
non-contiguous seq values and the client must use `> cursor`, not `cursor + 1`.

---

## 3. Deletes and tombstones

### 7. Deletes have no tombstone; a removed override, alias or price row is invisible — BLOCKER

**Scenario A — store override removed.** Store S1 has a local promo: rice at ₹40/kg, stored as a
`store_id = 'S1'` row. The owner removes the override; the server either deletes the row or closes it with
`valid_to`. If it deletes it, the delta has no row to return — a change feed built from "rows that changed"
cannot represent an absent row. S1's terminal keeps applying ₹40 while S2 and the franchise default charge
₹45. If the server closes it with `valid_to` but the change log only records inserts (or the payload omits
`valid_to` — defect 11), the terminal also keeps the override.

**Scenario B — alias moved.** Alias `T500` is reassigned from item A to item B. The server deactivates A's
`item_identifiers` row and inserts B's. A terminal that missed the deactivation still resolves `T500` to A;
the bill copies A's name and price. A terminal that received the insert but not the deactivation may have
two active rows for the same alias and must pick one.

**Scenario C — price row replaced.** `PUT /v1/admin/items/:id/prices` "replace tier set". If the server
deletes the old 500 g tier instead of closing it, a terminal that missed the delete keeps selling 500 g at
the old price. The new version may not include a 500 g row at all, so `MAX(version)` does not help.

**Why it fails.** The brief only commits to soft-deleting *items* ("Never hard-delete"). `item_prices` and
`item_identifiers` have no such rule; the admin route is "replace tier set", which naturally means
`DELETE` + `INSERT`. A delta feed of current rows cannot express a deletion; the terminal has no way to
distinguish "this row is gone" from "this row was not in the page."

**Worked example.** S1's override is removed at seq 200. T1's cursor is 199. The catalog handler selects
`item_prices WHERE store_id IN ('', 'S1')` and returns the default row at ₹45 plus the *absence* of the S1
row. T1's local table still has the S1 row from last week; its selection prefers the store row (correctly)
and charges ₹40. The bill is accepted; `stale_price` compares versions, and the store row's version may
still be the latest the terminal knows, so no warning is raised. The loss is silent and per-terminal.

**Smallest fix.** Never `DELETE` a catalog row. Use `active = 0` for identifiers and `valid_to` for price
rows; closing an interval is an `UPDATE` whose full row goes into the change log. For anything that truly
must disappear, write an explicit tombstone row (`op = 'delete'`, primary key + old values) and have the
terminal delete by key. The catalog payload must include `active`, `valid_from` and `valid_to` (defect 11)
so the client can prune. Add a periodic full pull/checksum as the backstop (defect 4/5). A terminal that is
offline cannot know about the removal, and that is acceptable: it keeps billing at the last known price
until it syncs; the fix does not require a network on the sale path.

### 8. Alias reassignment and item deactivation depend on apply order — MAJOR

**Scenario A — alias keyed by `(store_id, alias)`.** The change set contains: seq 300, update `T500`
`active = 0` (old item A); seq 301, insert `T500` `active = 1` (new item B). A retry or a page applies 301
first and then 300. If the local table is keyed by `(store_id, alias)` and applies a deactivation by alias,
it deactivates the *new* mapping. The cashier scans `T500` and gets "inactive" or, worse, the old item.

**Scenario B — item deactivation not atomic with its aliases.** `DELETE /v1/admin/items/:id` is soft
(`active = 0`) and "deactivates identifiers". If that is two statements in two transactions (item at seq
400, aliases at seq 401–403), a partial page or a crash between them leaves an active alias pointing at an
inactive item. The terminal's resolution query only checks the alias's `active` flag, so it resolves the
inactive item and bills it.

**Why it fails.** The schema has no stable identifier id; the only key is the alias string, which is
exactly the value being reassigned. The design also never states that item deactivation and alias
deactivation are one change set. Apply order is then load-bearing, and retries/pagination make apply order
non-deterministic.

**Worked example.** Owner deactivates "Tamarind" (item A) and immediately creates "Tamarind Seedless" (item
B) with the same `T500` alias. T1 pulls a page containing the item deactivation but not the alias rows.
Cashier scans `T500`; the terminal joins only `item_identifiers.active` and resolves A; the bill records
A's name and old price. The owner sees a sale of a deleted item.

**Smallest fix.** Give `item_identifiers` a stable `identifier_id` (UUID) and key the local table by it;
`UNIQUE(store_id, alias) WHERE active = 1` stays as the business rule. Emit the item deactivation and all
its alias deactivations in one change set. Make resolution join `items.active = 1` as well as the alias
flag, so even a stale alias cannot resolve to an inactive item. If adding an id is too much for v1, send a
single coalesced row per alias ("T500 now maps to B, active") and have the client `UPSERT` by
`(store_id, alias)`, never send two rows that the client must order.

---

## 4. Price SCD2 vs checkpoint

### 9. `store_id NULL` defeats the unique key; duplicate franchise defaults are accepted — BLOCKER

**Scenario.** The franchise default price is stored with `store_id = NULL`, as the brief specifies. The key
is "`(item_id, store_id, qty_base, version)`". Two price imports (or two admin requests, or a retry after a
lost response) both insert a default row for item `rice`, `qty_base = 1000`, `version = 7`. Both succeed.
The terminal pulls both rows with different `price_minor` values and no way to choose.

**Why it fails.** SQLite's unique indexes treat NULLs as distinct: "For the purposes of unique indices, all
NULL values are considered different from all other NULL values and are thus unique"
([SQLite CREATE INDEX](https://sqlite.org/lang_createindex.html)). A `UNIQUE(item_id, store_id, qty_base,
version)` index therefore does **not** constrain the franchise-default rows, because every one of them has
`store_id = NULL`. The same applies to an `ON CONFLICT` target on that index: the conflict never fires for
the default scope. If the schema instead makes the key a `WITHOUT ROWID` primary key, NULL is rejected and
the franchise default cannot be stored at all; either way the model is broken.

**Worked example.** `item_prices` contains two rows for `(rice, NULL, 1000, 7)`, ₹40 and ₹45, because a
price import retried. `GET /v1/catalog` returns both. A terminal does
`SELECT price_minor FROM item_prices WHERE item_id = ? AND qty_base = ? AND (store_id IS NULL OR store_id = ?)
ORDER BY version DESC LIMIT 1`; SQLite returns whichever row it reads first (rowid order in practice, but
not guaranteed). Two terminals can charge different prices for the same item on the same day, and both bills
are accepted.

**Smallest fix.** Replace `store_id NULL` with a NOT NULL sentinel: `store_key TEXT NOT NULL DEFAULT ''`
where `''` is the franchise default, and `UNIQUE(item_id, store_key, qty_base, version)`. Queries use
`store_key IN ('', :store)` and prefer the non-empty row. A generated column
`COALESCE(store_id, '')` also works if you do not want to migrate every writer. If you keep both columns,
add `CHECK((store_key = '' AND store_id IS NULL) OR store_key = store_id)`. This is a one-line schema
decision now and a data-repair migration later.

### 10. Future-dated `valid_from` and clock skew; the stale check uses the same skew — MAJOR

**Scenario A — early adoption.** The owner schedules a price change for 00:00 server time. A terminal has
already pulled the row. Its clock is 10 minutes fast, so at 23:50 real time it believes it is 00:00 and
starts charging the new price. A terminal with a slow clock keeps the old price after midnight. The
`valid_from` check is evaluated against the terminal's wall clock, so both behaviours are "correct" from
the terminal's point of view.

**Scenario B — the stale-price diagnostic is wrong.** The Worker compares each line's `price_version` to
the version valid at `created_at` (terminal clock). A terminal that is two days fast bills with an old
version; the server computes the version valid at a *future* `created_at` and may report "no stale price"
or flag the wrong version. A terminal that is two hours slow gets flagged for a price that was valid at the
true sale time. The diagnostic is only a flag, but it is the operator's only signal for price drift, so a
noisy or silent flag is not harmless.

**Why it fails.** `valid_from` is server time, but the only clock available on an offline terminal is its
own. The design says "server clock for `valid_from`" and then never provides a server-time reference in the
catalog response; `server_time` is returned by `/v1/token` and the heartbeat, not by the catalog or bill
responses, so a terminal that has been offline has no fresh reference at the moment it prices a sale. A
wall-clock comparison across two unsynchronised clocks cannot be correct.

**Worked example.** Price valid_from = 2026-11-15 00:00:00 UTC. Terminal T1 clock = +10 min. At 23:50 UTC
on 14 November T1 charges the new price. The bill's `created_at` is 00:00:05 on 15 November; the server sees
the new version as valid and does not flag it. The early charge is invisible. The reverse (a slow clock)
leaves the old price active for 10 minutes; if the old price was lower, the store loses money on every bill
in that window.

**Smallest fix.** Treat `valid_from` as *publication* time for v1 and apply a pulled price immediately;
drop future-dated pricing until a server-time mechanism exists. If scheduling is required, persist a
server-time offset from every response (`server_time` on `/v1/catalog`, not just `/v1/token`), compute a
corrected now from a monotonic clock + offset, and store the offset's age. Do not use local wall time for
`valid_from` or for stale detection. For the stale diagnostic, store the `catalog_seq` the terminal was
priced against on the bill and compare versions/seq, not wall clocks; the server cannot recover the true
sale time from a skewed `created_at`. Flag a bill whose `created_at` is after `received_at` (fast clock) or
whose offset is stale; never block the sale.

### 11. The payload omits `store_id` and validity; store overrides leak and precedence is undefined — BLOCKER

**Scenario A — cross-store leak.** Store S1 has a local promo on rice. S2's terminal pulls the catalog. The
`prices` array in `04 Hono API impact` is `{item_id, qty_base, price_minor, version}` — no `store_id`, no
`valid_from`, no `valid_to`. If the server does not filter by the terminal's store (the design never says
it does), S2 applies S1's ₹40 promo to its own bills. If the server does filter, the terminal still cannot
tell an override from a default, so it cannot implement "store row wins, default falls back" per tier.

**Scenario B — precedence between a store override and a newer default.** S1's override is stored at
version 2; the franchise default is later raised to version 5. The terminal has both rows. If it selects
`MAX(version)` across scopes, it picks the default 5 and silently drops S1's override. If it selects "any
store row" and applies it to every tier, it drops default tiers that the override does not cover (for
example, S1 overrides only 1 kg; a 750 g sale still needs the default 500 g row). The schema's
`(item_id, store_id, qty_base, version)` does not say whether versions are per scope or per item across
scopes, so neither implementation is provably right.

**Why it fails.** The terminal cannot apply SCD2 from a payload that has no validity columns and cannot
enforce store scoping from a payload that has no store column. The brief's domain shape has the columns;
the documented wire contract drops them. That is a contract bug, not an implementation bug.

**Worked example.** S1 sets `rice, 1000 g, ₹40, version 2` (override). The franchise later sets
`rice, 1000 g, ₹45, version 5` (default). S1's terminal picks version 5 and charges ₹45; the owner's promo
is silently dead. The owner sees the override in the admin UI and cannot understand why S1's bills use the
default.

**Smallest fix.** Include `store_key` (or `scope`), `valid_from` and `valid_to` on every price row (plus
`active` on identifiers), and filter server-side by the store from the JWT:
`WHERE store_key IN ('', :store)`. Define the merge rule explicitly: for each `(item_id, qty_base)`, the
active store-specific row wins; otherwise the active default row. Version each scope independently, or use
one version per item and an explicit `scope` precedence; do not use `MAX(version)` across scopes. The
terminal applies rows by `(item_id, store_key, qty_base, version)` and selects the current row by
`valid_from <= corrected_now < valid_to` (or by the published set if v1 drops scheduling).

---

## 5. Batch push

### 12. `bill_lines` has no identity; a retry duplicates every line — BLOCKER

**Scenario.** Terminal sends a 20-line bill. The Worker's batch commits bill + lines. The HTTP response is
lost (network drop, Worker timeout after commit, laptop sleep). The terminal does not mark the bill synced,
so it retries. The bill insert hits `ON CONFLICT(id) DO NOTHING` and no-ops. The line inserts have no
unique constraint, so all 20 lines are inserted again. The bill row still has the original `total_minor`,
but `bill_lines` now has 40 rows. The day/month report sums `qty_base` and `line_total_minor` from the
lines and doubles the sale.

**Why it fails.** The `bill_lines` schema in the brief has no primary key, no `line_no`, and no line id. A
child table with no uniqueness cannot be idempotent under retry. The design's "per-row `synced_at`" only
protects the parent bill; the terminal marks the parent synced after `accepted`, but a response lost after
commit means the parent is retried, and the children are re-inserted. This is the single most likely
money-corrupting bug in the design because lost responses are normal, not exceptional.

**Worked example.** Bill id `0192f3d1`, 20 lines, total ₹650.00. First attempt commits; response lost.
Retry inserts 20 more lines. Report for the day shows ₹1,300.00 for that bill's items; the receipt and the
`bills.total_minor` say ₹650.00. Nothing in the system notices: `stale_price` is about versions, not line
counts; the unique `(store_id, terminal_id, local_seq)` is on `bills`, not lines.

**Smallest fix.** Add `line_no INTEGER NOT NULL` to `bill_lines` and `PRIMARY KEY(bill_id, line_no)` (or a
line UUID). In the per-bill transaction, first check whether the bill id already exists; if it does, insert
nothing at all (and compare a content hash, defect 14); if it does not, insert the bill and all lines. The
`line_no` is stable because the bill is immutable. A second belt: `UNIQUE(bill_id, line_no)` and
`ON CONFLICT(bill_id, line_no) DO NOTHING` on line inserts, but only as a backstop; the primary rule is
"skip the whole bill if the id exists."

### 13. One `db.batch()` for 100 bills; one bad bill rolls back all 100 — BLOCKER

**Scenario.** A terminal pushes a full batch of 100 bills. One bill has a duplicate
`(store_id, terminal_id, local_seq)` (it was created after a terminal restore, defect 14), or a malformed
`price_breakdown`, or a line referencing an item the server does not have. The Worker wraps all 100 bills
and their lines in one `db.batch()`. D1 aborts the sequence and rolls back the entire batch. The terminal
receives an error, backs off, retries the same 100 bills oldest-first, and hits the same bad bill. The 99
good bills never sync. The outbox head is a poison pill; the UI shows "retrying" forever.

**Why it fails.** D1's batch is explicitly all-or-nothing: "Batched statements are SQL transactions. If a
statement in the sequence fails, then an error is returned for that specific statement, and it aborts or
rolls back the entire sequence" ([D1 `batch()`](https://developers.cloudflare.com/d1/worker-api/d1-database/#batch)).
The API contract in the design returns `{accepted[], rejected[]}` — a partial-acceptance shape — but a
single atomic batch cannot produce it on failure. The design is internally inconsistent about the unit of
atomicity.

**Worked example.** 99 bills are valid; bill #57 has `local_seq` 431, which already exists on the server
from before a terminal restore. The 100-bill batch throws `SQLITE_CONSTRAINT_UNIQUE`; none of the 99 are
inserted. On every retry, #57 is still in the batch (it is not parked because the error is a batch-level
500, not a per-bill 409). The terminal never makes progress. If the outbox is ordered oldest-first and the
loop stops on the first error, every bill after #57 is blocked too.

**Smallest fix.** Make one D1 `db.batch()` per bill: select the bill id, select the local_seq, insert the
bill, insert its lines, update the rollup (defect 21), all in one transaction. Return a per-bill status.
The push loop marks each accepted bill and parks only the failing one; later bills continue. 100 bills
means 100 D1 calls per push; each is tiny, and the current D1 limit is 1,000 queries per Worker invocation
(a batch counts as one), so this is well within limits. The extra round trips are the price of partial
acceptance; if they are too slow at the primary's distance, use a Durable Object/Queue to serialize the
push, not a single all-or-nothing batch.

### 14. `ON CONFLICT(id) DO NOTHING` vs the `local_seq` index; the accepted list lies; restore reuses `local_seq` — BLOCKER

**Scenario A — targeted conflict aborts the batch.** The Worker writes `INSERT ... ON CONFLICT(id) DO
NOTHING`. A new bill has a fresh id but a `(store_id, terminal_id, local_seq)` that already exists. SQLite's
conflict target only covers the id index: "The conflict target specifies a uniqueness constraint that will
trigger the upsert"; a violation of a different unique index is a normal constraint error
([SQLite UPSERT](https://sqlite.org/lang_upsert.html)). The statement throws; in a 100-bill batch, all 100
roll back (defect 13).

**Scenario B — untargeted conflict silently drops the bill.** If the Worker writes `ON CONFLICT DO NOTHING`
with no target, the `local_seq` violation is swallowed: the bill row is not inserted. If the Worker then
inserts the lines (separate statements, and the line FK to `bills` passes only if a bill with that id
exists — it does not, so D1's enforced FK fails the batch), the batch rolls back; if the FK were absent, the
lines would be orphaned. Either way the outcome is not "accepted".

**Scenario C — same id, different payload.** A terminal bug or a malicious replay sends bill id N with a
different total than the stored N. `DO NOTHING` treats it as a successful retry. The terminal marks it
synced; the divergence is never surfaced. Bills are supposed to be immutable, but the server has no way to
detect that the immutable content changed.

**Scenario D — restore reuses `local_seq`.** The local SQLite file on an installed terminal is restored from a
nightly backup taken at 18:00 (same machine, keychain and enrolment intact). The backup's counter is at 430;
bills 431–500 were created and synced after the backup. After the restore the counter is 430 again. New
bills 431+ have new UUIDs but the same `(store, terminal, local_seq)` values as bills already on the server.
This is not a rare edge case: restoring a local database from backup is the normal recovery for a corrupted
file. The unique index fires on the first new bill.

**Why it fails.** The design calls the unique index "the second line of defence" but never says what
happens when it fires. A conflict is not an idempotent retry, and `ON CONFLICT DO NOTHING` cannot tell the
two apart. The terminal has no `terminal_epoch` or other way to renumber after a restore, and no way to
distinguish "already synced" from "different bill, same local_seq".

**Worked example.** Server has bill M = `(T2, 431)`, total ₹500, synced. Restore T2 from backup; counter
431. Cashier creates bill N with id `0192...`, local_seq 431, total ₹300. Push: `INSERT ... ON CONFLICT(id)
DO NOTHING` → targeted `local_seq` violation → 500 + batch rollback. If the Worker retries with an
untargeted `DO NOTHING`, the bill row is skipped; the terminal gets a 200 and marks N synced although the
server has no N. The ₹300 sale exists only on the terminal's disk (until it is pruned) and never appears in
reports.

**Smallest fix.** Add `terminal_epoch INTEGER NOT NULL DEFAULT 0` to `bills` and include it in the unique
key: `UNIQUE(store_id, terminal_id, terminal_epoch, local_seq)`. The server returns a per-terminal
`ack_seq`/`ack_epoch` on every push; if the terminal's counter is at or below the ack on startup, it bumps
its epoch, retags every still-unsynced bill with the new epoch (safe: the server has not seen them), and
uses the new epoch for new bills. A `local_seq` conflict returns a machine-readable `409
local_seq_conflict` with the existing bill id; the terminal parks it and surfaces an operator action, never
marks it synced. Store a `payload_sha256` on the bill; on a duplicate id, compare it and return `409
id_payload_mismatch` if it differs. Pre-check by id and by the unique key inside the per-bill transaction;
do not infer acceptance from the request. For invoice numbers, allocate `invoice_no` only in the same
transaction as the successful insert, never before validation.

### 15. 409 handling and parked bills are undefined — MAJOR

**Scenario.** A bill gets a 409 (payload mismatch, local_seq conflict, invalid item). The design says
"refused bills are parked, not hammered" and "parked rows retry after ~1h". There is no dead-letter, no
per-code action, and no operator resolution. The row retries every hour, gets the same 409, and stays in
the outbox forever. The attention list grows; the cashier/owner has no button that fixes it. If the parked
row is at the head of the outbox and the loop stops on the first failure, it also blocks every later bill.

**Why it fails.** "Parked" is a retry policy, not a resolution. Permanent 4xx errors (validation, payload
mismatch) must never be retried; a local_seq conflict needs a terminal-epoch bump or a renumber; a revoked
device needs re-enrolment. Treating all of them as "retry in an hour" is a guaranteed infinite loop. The
design also never says whether a parked bill blocks the rest of the outbox; note 05's test plan says it
should not, but the state machine doesn't encode "skip parked rows".

**Worked example.** Bill P is rejected with `id_payload_mismatch` (a terminal bug produced a different
total on retry). It is parked. Every hour the terminal retries, the server returns 409, and the row stays.
The daily "unsynced count" grows by one forever; the owner learns to ignore the banner. A later network
outage creates 300 more unsynced bills; nobody notices the one poisoned row among them.

**Smallest fix.** Classify outcomes: 2xx accepted; 409/422 permanent, park once, surface a `resolution`
object (`code`, `bill_id`, `existing_bill`, `suggested_action: renumber | void | re-enrol`); 401 refresh and
resume; 429/5xx/network retry with backoff. After N attempts or on a permanent code, move the bill to a
dead-letter list that the UI can export; never auto-retry a permanent code. The push loop must skip parked
rows and keep sending later bills. The bill is never deleted from the terminal until the operator resolves
it or the retention policy explicitly expires it; a rejected sale must remain visible.

### 16. Batch sizing ignores D1's 100-parameter and 100 KB statement limits — MAJOR

**Scenario.** A straightforward implementation builds one multi-row insert per table: 100 bills × 12 columns
= 1,200 bound parameters in one statement, or 100 lines × 12 columns = 1,200. D1 rejects the statement:
"Maximum bound parameters per query | 100" and "Maximum SQL statement length | 100,000 bytes"
([D1 limits](https://developers.cloudflare.com/d1/platform/limits/)). A single bill with many lines and long
`price_breakdown` JSON can also exceed 100 KB even if the parameter count is split. The first real push
fails; the terminal backs off and retries the same payload.

**Why it fails.** The design talks about "batches of about 100" as if the batch size were a protocol choice;
it is also a per-statement limit. The note's payload has 20 lines per bill and a JSON breakdown, so a
100-bill single statement is far over both limits. The 30-second limit applies to the entire batch call as
well, so a naive 2,000-statement batch can time out under load.

**Worked example.** `INSERT INTO bill_lines (bill_id, item_id, ..., price_breakdown) VALUES (...), (...),
...` with 100 lines and 12 columns → `D1_ERROR: too many SQL variables`. Rewriting to one statement per
line fixes the parameter count but leaves 100 bills × 20 lines = 2,000 statements in one batch; at a few
milliseconds each plus JSON parsing, the batch is at risk of the 30-second ceiling, and any one bad
statement rolls back all 2,000.

**Smallest fix.** One row per statement; each statement stays well under 100 parameters and 100 KB. Chunk
any multi-row insert to at most `floor(100 / columns)` rows (for 12 columns, 8 rows). Use one `db.batch()`
per bill (defect 13) and one statement per line. Cap the JSON payload size per bill and reject a bill with
more lines than the terminal's own UI can produce. Measure a 100-bill push against the 30-second batch limit
before assuming "100" is safe.

---

## 6. D1 specifics

### 17. The 10 GB cap versus one database per franchisee — BLOCKER

**Scenario.** A five-store franchisee does 600 bills/day/store (3,000/day). At an estimated 12 KB per bill
on disk, the database reaches 10 GB in about 290 days — before the first year is archived. At 1,000
bills/day/store (5,000/day), it reaches 10 GB in about 175 days. Writes then fail; terminals keep billing
offline, but every push fails; reports stop updating. There is no in-place growth: the limit "cannot be
further increased" ([D1 limits](https://developers.cloudflare.com/d1/platform/limits/)).

**Why it fails.** D1 is designed to scale out across "multiple, smaller (10 GB) databases, such as
per-user, per-tenant or per-entity databases" (same page). The brief chooses *one database per tenant*
(franchisee), not per store, and the tenant is explicitly multi-store. The cap is per database, not per
store; one noisy store's bills consume the whole tenant's headroom. Catalog, indexes, `audit_events`, and
the change log all compete for the same 10 GB, so the real number is worse than the estimate.

The design has no archival or sharding plan. When the cap is hit, there is no safe in-place deletion:
deleting bills breaks the historical reports and the legal record. Migrating to a new database requires a
cutover, a cursor epoch bump (defect 4), and a re-point of every terminal.

**Worked example (planning arithmetic).**

| Stores | Bills/day/store | Bills/day total | Days to 10 GB at 12 KB/bill |
|---|---:|---:|---:|
| 1 | 1,000 | 1,000 | ~875 |
| 5 | 600 | 3,000 | ~292 |
| 5 | 1,000 | 5,000 | ~175 |
| 10 | 1,000 | 10,000 | ~88 |

12 KB/bill is deliberately a planning estimate: a 400 B `bills` row + 20 lines × 350 B ≈ 7.4 KB of raw
row data; two `bills` indexes and two `bill_lines` indexes plus page overhead take it to roughly 10–12 KB.
A grocery bill with overrides, long names, or a large `price_breakdown` is larger.

**Smallest fix.** Decide the shard key before the first tenant: one D1 per store (or per store × fiscal
year) behind the existing one-Worker-per-franchisee API, with the store from the JWT selecting the binding.
The handlers are already tenant-agnostic, so this is a routing/registry change, not a handler rewrite.
Cross-store reports read a small per-store rollup (defect 21), not raw lines. Closed years move to an
archive database or R2 export; keep a `store_db_registry(store_id, db_binding, from_date, to_date)` row.
Plan the restore/export path at the same time (defect 20). This preserves offline billing: the terminal's
URL and credential do not change; only the Worker's internal binding lookup does.

### 18. Single writer: a month report blocks every push — MAJOR

**Scenario.** At month-end, an owner runs the per-item month report. It scans 1.8M `bill_lines` (five
stores × 600 bills/day × 20 lines × 30 days) and takes 3–8 seconds. During those seconds, every terminal
push and every token/report query queues behind it because each D1 database "is inherently single-threaded,
and processes queries one at a time"; throughput is "directly related to the duration of your queries"
([D1 limits](https://developers.cloudflare.com/d1/platform/limits/)). Some requests hit the overloaded
queue and fail; terminals back off for seconds to minutes. At 10 stores, the report can approach the
30-second query limit and the whole tenant's sync stalls.

**Why it fails.** Reports and writes share one database. The design treats reports as a read-only side
show, but on D1 a long read is a write-path outage. The existing indexes (`idx_bills_store_date`,
`idx_lines_bill`, `idx_lines_item`) help find rows; none of them makes a month-scale aggregation cheap,
because the query must still visit every line in the range.

**Worked example.** 21:00 on 31 October. Ten terminals (one per store) push their final 100 bills. The owner
opens the October report. If those ten stores did 1,000 bills/day with 20 lines, the report scans 6M lines
(10 × 1,000 × 20 × 30) and takes 10–15 seconds; the ten pushes queue; three time out and enter backoff. The
owner concludes the sync is broken; the terminals eventually recover, but the day's close is delayed and the
"last sync" dashboard looks wrong.

**Smallest fix.** Pre-aggregate on write into `sales_daily(store_id, business_date, item_id, qty_base,
amount_minor, bill_count)` with `PRIMARY KEY(store_id, business_date, item_id)`, updated by an upsert in the
same per-bill transaction (defect 13). The month report becomes 30 × distinct items rows (roughly 30k–60k
rows per store), no join, no line scan, well under a second. Keep the raw query for audit with a hard range
cap and a separate endpoint. If you also enable read replication for historical reports, pin the read to
`first-primary` for the day report after a push (defect 19). A rollup rebuild job handles corrections and
post-restore repair.

### 19. Read replication without a session breaks read-after-write — MAJOR

**Scenario.** The team enables D1 read replication to make reports faster and uses `withSession()` in the
report handler. A terminal pushes its final 15 bills and immediately requests the day report. The report
reads a replica that has not yet received the writes; it returns yesterday's total (or today's total minus
15 bills). The owner exports it. A minute later the same report shows the correct number.

**Why it fails.** Writes always go to the primary; replicas lag asynchronously. The docs are explicit:
"All write queries are still forwarded to the primary database instance"; the Sessions API gives sequential
consistency; `withSession("first-primary")` starts at the latest version; a bookmark starts from a known
version ([D1 read replication](https://developers.cloudflare.com/d1/best-practices/read-replication/)). An
unconstrained session (`withSession()` / `first-unconstrained`) may start on any replica. The same applies
to the catalog: a terminal that pushes a bill and then pulls a catalog after an admin price change may read
an older catalog and advance its cursor past the change (defect 3/6).

Without read replication, D1 sends all reads to the primary, so this defect is dormant; it appears when
someone enables the feature and routes reports through an unconstrained session to fix defect 18.

**Worked example.** Push commits 15 bills at 14:00:00. Report request at 14:00:01 hits a replica whose lag
is 2 seconds. The report sums 1,985 bills instead of 2,000. The CSV is sent to the accountant. The next
request at 14:00:04 is correct. There is no marker on the response saying which version it read.

**Smallest fix.** Any read that must observe the tenant's own writes uses `withSession("first-primary")` or
a bookmark returned by the write path; historical reports over closed dates may use a replica. Put the
choice in the route, not in the caller's memory: a `readPrimary` wrapper for `/v1/reports` when `to` is
today, and for `/v1/catalog` when the terminal just pushed. Return `data_through` (the bookmark or
`received_at` watermark) in the report response so the UI can say "as of 14:00:00". This does not affect
offline billing; it only changes which D1 instance serves a read.

### 20. Time Travel, export and result limits leave no restore plan — MAJOR

**Scenario.** The database is 9.5 GB and a bad migration corrupts `item_prices`. Time Travel can restore to
any minute in the last 30 days (Workers Paid), which covers the migration. But a restore also rewinds the
change sequence (defect 4). A month later, the tenant needs a copy for an audit or a new franchisee; `d1
export` produces a file larger than the 5 GB import limit, so it cannot be re-imported in one command
([D1 limits](https://developers.cloudflare.com/d1/platform/limits/)). A raw-line CSV export over a year can
exceed the 50 MB query result limit (effective 2026-11-04), so the export endpoint fails on the largest
tenant.

**Why it fails.** The design's operations section says "provisioning script per franchisee: create D1, run
migrations, set secrets, deploy Worker" and "keep synced rows about 30 days" but has no backup, export,
restore rehearsal, or archive plan. Time Travel is point-in-time recovery, not a backup: it is 30 days,
destructive, and the bookmark can advance without a write (internal maintenance), so a bookmark is not a
change cursor ([D1 Time Travel](https://developers.cloudflare.com/d1/reference/time-travel/)). The 5 GB
import limit is a hard ceiling on a single-file restore of a near-10 GB database.

**Worked example.** A franchisee leaves and wants their data. `d1 export` writes a 6 GB SQL file. The new
tenant's database import fails at the 5 GB limit; splitting the file by table is possible but the foreign
keys and ordering make it a manual job, and no one has tested it. A year-end CSV requests 13 months of
lines and exceeds 50 MB; the Worker returns an error after doing the scan.

**Smallest fix.** Schedule a monthly export to R2, split into files under 5 GB (by month or by table), and
rehearse a restore into a fresh D1 every quarter. Paginate every report/export and cap the date range; a
CSV stream can be generated in pages, but D1 does not stream a 50 MB result, so the route must page by
item/day. Keep Time Travel for the 30-day undo, and document that it is not the archive. Pair this with the
per-store shard (defect 17) so each export file is smaller and a single store can be restored independently.
The Free plan is 500 MB / 50 queries per invocation / 7-day Time Travel; provisioning must fail closed
unless the account is on Workers Paid.

---

## 7. Report cost

### 21. The per-item month query stops being servable from D1 — MAJOR

**Scenario.** The documented query joins `bill_lines` to `bills`, filters by store and business date, and
groups by item. For one store and one day it is fine. For five stores and a month it scans 1.8M line rows;
for ten stores, 3.6M; for a year, 40M. The D1 query duration limit is 30 seconds, and a single-threaded
database serves one query at a time, so a 10-second report is also a 10-second write outage (defect 18).
At some tenant size the query returns an error instead of a number; the owner has no smaller cut because
the report endpoint's `from`/`to` are free-form.

**Why it fails.** The cost is proportional to the number of lines in the date range, not the number of
items reported. Indexes help the join but cannot avoid visiting each line: `idx_bills_store_date` finds the
bills, `idx_lines_bill` finds their lines, and the aggregation still reads every line row (including the
`price_breakdown` JSON and copied names). A covering index would reduce page reads but not the row count.
The 50 MB result limit is a separate ceiling for CSV exports.

**Worked example.** Five stores × 600 bills/day × 20 lines = 60,000 lines/day, 1.8M/month. A 1.8M-row scan
at roughly 1–3M rows/s of SQLite work is 0.6–1.8 s of pure scan, plus D1 serialization and JSON column
overhead; call it 2–5 s. That is already 0.2–0.5 QPS on the tenant's only database. At 10 stores it is
4–10 s; at 20 stores or a 12-month range it is at risk of crossing 30 s. The failure is not gradual: the
report times out and the same database is unavailable to every terminal for the duration.

**Smallest fix.** The `sales_daily` rollup from defect 18. Write it per bill line (or per distinct item in
the bill) inside the per-bill transaction; the report reads 30 days × items, not lines. Add a rebuild
endpoint/CLI that recomputes a date range from `bill_lines` for audits and after a restore. If a raw-line
report is still needed, cap the range (one day per store, or one month with a hard row budget), page by
item, and run it on a read replica with `first-primary` only for today. No fix here touches the terminal's
offline path; the rollup is a server-side write in the same push transaction.

### 22. Report grouping and integer average are wrong at the edges — MINOR

**Scenario A — renamed item.** `GROUP BY bl.item_id, bl.item_name` splits a month into two rows when an item
is renamed mid-month: "Tamarind" and "Tamarind Seedless" each get a partial quantity and amount. The owner
sees two items, each with part of the sales.

**Scenario B — integer division.** `SUM(line_total_minor) / NULLIF(SUM(qty_base), 0)` is integer division in
SQLite. A bill of 1,500 g for ₹65.00 returns `6500 / 1500 = 4` (paise per gram), not 4.33; the UI's
₹/kg conversion shows ₹40.00 instead of ₹43.33. The truncation is silent.

**Why it fails.** The line name is a historical copy, which is right for the bill but wrong as a grouping
key; the item id is the identity. The average price is a derived decimal but both operands are integers.
There is also an unstated question: does `line_total_minor` include tax? If `bills.total_minor` includes
tax and the line totals do not, the report's "amount" will not reconcile to the bill total minus rounding.

**Worked example.** 1,000 g sold for ₹43.33 (4,333 paise). `4333 / 1000 = 4`; the UI's ₹/kg conversion
shows 4,000 paise/kg = ₹40.00/kg instead of ₹43.33/kg, a 7.7% understatement. A month report that sums to
less than the day-summary totals sends the owner looking for missing bills.

**Smallest fix.** Group by `item_id` only; choose a display name deterministically (the current `items.name`,
or the name on the latest line via a window function). Compute the average as
`CAST(SUM(line_total_minor) AS REAL) / NULLIF(SUM(qty_base), 0)` and round once at display; state the unit.
Define `line_total_minor` as tax-inclusive or exclusive in the schema comment and add a reconciliation test:
`SUM(line_total_minor) + rounding_minor == bills.total_minor` for every bill (or the documented difference).

---

## 8. Clock and business date

### 23. `business_date` comes from the terminal clock; midnight and month-end bills land on the wrong day — MAJOR

**Scenario.** A bill is created at 2026-03-31 23:59:30 local. The terminal clock is 90 seconds fast, so the
terminal writes `business_date = 2026-04-01`. The March month report excludes the sale; the April report
includes it. If the store uses a 06:00 business-day start (note 06's `business_day_start_minutes`), the
boundary is at 05:59/06:00 and a two-minute skew flips a sale just before the cutover into the previous day.
Bills are immutable; correcting the date requires a void and a re-bill, which changes the invoice and the
audit trail.

**Why it fails.** The terminal computes `business_date` locally (note 06 says so explicitly) and the only
clock it has is the OS clock. `server_time` is returned by `/v1/token` and the heartbeat, not by the catalog
or bill responses, so a terminal that has been offline for hours or days has no fresh reference. The server
"validates" the date, but it cannot know the true sale time from a skewed `created_at`; a bill that arrives
90 days later has a `received_at` that is useless for the day boundary.

**Worked example.** March month-end. One terminal is 2 minutes fast. A ₹4,500 sale at 23:59:30 lands on
1 April. The March per-item report is short by that basket; the April report is over. The owner compares the
two months, sees a one-day shift, and cannot tell which terminal caused it. If the sale was the last of the
day, the day report and the cash drawer (not in v1, but the paper receipt) disagree.

**Smallest fix.** Persist a server-time offset from every response (add `server_time` to `/v1/catalog`,
`/v1/bills`, and the heartbeat), compute `corrected_now = monotonic_now + offset`, and use it for
`business_date` and `created_at`. Stamp the bill with the offset's age and the clock source
(`ntp|server_offset|os`). If the offset is older than a configurable window (say 12 hours) or the skew
exceeds 2 minutes, set a `clock_unverified` flag and show a non-blocking banner; never refuse a sale. The
server flags a bill whose `created_at` is after `received_at` (fast clock) and reports the discrepancy in the
attention list. For a "day close", require a fresh server time; a terminal that cannot reach the server can
still bill, but the day is marked provisional (defect 24). This preserves offline billing and makes the
error visible instead of silent.

### 24. Bills arriving after a report make it silently stale — MAJOR

**Scenario.** The owner runs the October report on 1 November at 09:00 and exports it. Terminal T3 has been
offline since 30 October 18:00. On 3 November T3 reconnects and pushes 40 October bills. The raw query
re-run would include them; the exported CSV does not. The accountant files the short number. The owner has
no way to know the report was provisional.

**Why it fails.** Reports are queries, not snapshots. The design has no per-store sync watermark, no
"provisional" flag, and no day-close record. The terminal's outbox is per-row `synced_at`, so there is no
single "this store is caught up to date D" value. A report generated at any time may be missing any
terminal's backlog, and a later re-run silently changes the number.

**Worked example.** October has 100,000 bills across five stores. T3's 40 bills are 0.04% of the volume, so
the total looks plausible. One of them is a ₹12,000 wholesale-sized basket; the month's item mix and revenue
are short by that basket. The owner only discovers it when T3's cashier asks why the report doesn't include
the last two days of October.

**Smallest fix.** Maintain `store_sync_state(store_id, terminal_id, last_received_at, last_business_date,
last_local_seq)` updated on every accepted bill. A report response includes `provisional: true`,
`missing_terminals: [...]`, and `data_through` (the minimum `last_received_at` across active terminals for
the requested range). A `day_close(store_id, business_date)` record is written only when every active
terminal has synced through the close time with no unsynced bills for that date; until then the report is
labelled provisional. The UI shows the banner and lets the owner re-run. The rollup (defect 21) must accept
late upserts for past dates; do not freeze a closed month. Offline billing is unaffected: a terminal that
cannot reach the server simply keeps its day provisional.

### 25. `store_settings` and the day-start rule are not in the catalog contract — MAJOR

**Scenario.** The owner changes `business_day_start_minutes` from 06:00 to 04:00, or changes the rounding
mode. The terminal catalog payload in note 04 contains items, prices, identifiers, units and `config`
(terminal UI config); `store_settings` is not in the payload. Terminals keep the old rule. Two terminals in
the same store compute different `business_date` values for a 05:00 sale; the day report splits. A terminal
with an old rounding mode writes a different `rounding_minor`, and the report's line totals no longer
reconcile to `bills.total_minor`.

**Why it fails.** The business-day boundary and rounding are money/date policy, not UI preferences, but
they are treated as admin-only settings. The terminal must know them offline, so they have to be pulled,
versioned and stored with the catalog. `terminal_config` has a `version`; `store_settings` does not, and
the change log (defect 1) does not mention it.

**Worked example.** Owner sets day start to 04:00 on 1 December. Terminal T1 pulls the new config; T2 is
offline and keeps 06:00. A sale at 05:30 on 2 December gets business_date 2 December on T1 and 1 December on
T2. The store's day report is split across two days depending on which terminal rang the sale. The bills are
immutable; the only fix is a void/re-bill, which changes the invoice.

**Smallest fix.** Add `store_settings` (business day start, rounding mode, numbering policy version) to the
catalog payload and the change log, with an `effective_from` timestamp. The terminal stores the version
used on each bill (`settings_version`) so a later report can explain a boundary difference. Apply the new
setting at the next business-day boundary, not mid-day. Include `tax_rates` and `units` in the same
versioned pull; a tax-rate change is copied onto the line, but the terminal still needs the current rate to
price a new bill.

### 26. `synchronous=NORMAL` can lose the last committed bill on power loss — MAJOR

**Scenario.** A shop's power cuts immediately after a bill commits. The local database is WAL with
`synchronous=NORMAL` (the chosen configuration). SQLite does not fsync the WAL on every commit in NORMAL;
the commit can be rolled back on the next open. The cashier has already printed a receipt and handed over
the goods; the bill row and its outbox row are gone. This is not a sync bug, but it defeats the offline
guarantee that is the whole point of the terminal.

**Why it fails.** SQLite's WAL documentation is direct: "Writers sync the WAL on every transaction commit if
PRAGMA synchronous is set to FULL but omit this sync if PRAGMA synchronous is set to NORMAL"; the downside
is that "transactions are no longer durable and might rollback following a power failure or hard reset"
([SQLite WAL](https://sqlite.org/wal.html)). The summary's own measurement shows FULL still sustains ~41
bills/s (p50 17 ms), far above a grocery checkout's rate. The choice of NORMAL buys throughput the POS does
not need at the cost of the only copy of a sale.

**Worked example.** Bill 431, ₹650, committed at 14:02:11. The UPS is absent or the power supply fails at
14:02:12. On restart, SQLite recovers to the last fsynced commit; bill 431 is not there. The customer's
receipt exists; the sale does not. The 30-day "keep synced rows" policy does not help, because the row never
reached the disk or the server.

**Smallest fix.** Use `synchronous=FULL` on the billing connection (the one that commits the bill +
lines + outbox + counter + rollup) and keep NORMAL on the sync/read connection if desired. SQLite's
`synchronous` is per-connection, so the Go app can have two connections with different settings. With
FULL's measured 41 bills/s, the checkout path is still an order of magnitude faster than a cashier. Add a
post-commit check that the outbox row exists before printing the receipt, or print after commit; never
print before the commit. This does not break offline billing; it makes the offline record durable.

---

## Top five fixes, ordered by damage avoided per unit of work

1. **Make the per-bill push idempotent and atomic** (defects 12, 13, 14, 16, part of 15).
   Add `line_no`/PK to `bill_lines`, one `db.batch()` per bill (bill + lines + rollup), pre-check by id and
   `(store, terminal, epoch, local_seq)`, a `payload_sha256` on the bill, and a terminal epoch with a
   machine-readable 409. This is the cheapest fix that stops doubled sales and the 100-bill poison pill.
   The terminal still bills offline; the push only changes how acceptance is reported.

2. **Replace the undefined cursor with a commit-ordered change log, an epoch, and a full-resync path**
   (defects 1, 2, 3, 4, 5, 6, 7). One `change_log` row per logical change with the full payload, written in
   the same transaction as the data; `seq` is the cursor; `sync_epoch` changes after any restore; a
   `resync_required` full snapshot is applied atomically. This fixes permanent catalog drift, the torn
   read, tombstones, and the 90-day offline case in one mechanism. It is the highest-damage fix after
   idempotency because every wrong price/alias is silent.

3. **Make the price contract store-scoped and version-addressed** (defects 9, 10, 11).
   Replace `store_id NULL` with a NOT NULL sentinel and a real unique key; include `store_key`,
   `valid_from` and `valid_to` in the price payload; filter by the JWT's store; define "store row wins
   per `(item, qty_base)`, default falls back"; allocate versions with optimistic concurrency. This is a
   small schema/contract change that prevents systematic cross-store wrong prices and ambiguous current
   prices.

4. **Pre-aggregate sales on write** (defects 21, 22, and the report half of 18). Add `sales_daily` and
   upsert it in the per-bill transaction; the month report reads 30 × items, not millions of lines. This
   keeps month-end reports under the 30-second limit and stops them blocking every terminal's push. A
   rebuild job handles corrections and restores.

5. **Shard D1 by store (or store × fiscal year) before the first tenant** (defects 17, 20, part of 18).
   The 10 GB limit cannot be raised; a five-store franchisee at 600 bills/day/store reaches it in about
   10 months. Keep one Worker per franchisee and route by the store in the JWT; add a per-store registry,
   a monthly export to R2 split under 5 GB, and a rehearsed restore. This is the largest unit of work and
   the only one that is a one-way door; deciding it after launch means a migration and an epoch reset.

**Also cheap, do now:** the terminal epoch and 409 handling (defect 15), the business-date server offset and
provisional report watermark (defects 23, 24), `store_settings` in the catalog (defect 25), and
`synchronous=FULL` on the billing connection (defect 26).

## Sources

- Cloudflare D1 limits (10 GB, single-threaded, 30 s, 100 KB, 100 parameters, 50 MB result, 1,000 queries,
  Time Travel 30 days, 5 GB import): <https://developers.cloudflare.com/d1/platform/limits/>
- D1 `batch()` transaction and rollback semantics:
  <https://developers.cloudflare.com/d1/worker-api/d1-database/#batch>
- D1 read replication, primary vs replica, Sessions API, `first-primary`, bookmarks:
  <https://developers.cloudflare.com/d1/best-practices/read-replication/>
- D1 Time Travel (destructive in-place restore, 30 days, bookmarks can advance without writes):
  <https://developers.cloudflare.com/d1/reference/time-travel/>
- D1 foreign keys (always enforced; every query runs in an implicit transaction):
  <https://developers.cloudflare.com/d1/sql-api/foreign-keys/>
- SQLite UPSERT conflict target:
  <https://sqlite.org/lang_upsert.html>
- SQLite AUTOINCREMENT (rolled-back ROWIDs can be reused; increasing but not sequential):
  <https://sqlite.org/autoinc.html>
- SQLite unique indexes and NULLs:
  <https://sqlite.org/lang_createindex.html>
- SQLite WAL and `synchronous=NORMAL` durability:
  <https://sqlite.org/wal.html>
