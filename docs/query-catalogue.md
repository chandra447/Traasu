# Query catalogue — the queries the schema must serve

Written independently of the schema agents, as a yardstick. Every query here must be servable from an index
in `docs/d1-schema.sql` plus `docs/migrations/0008-audit-fixes.sql`; if one is not, the schema has a gap.

`?` marks bound parameters. All money in paise, all quantities in base units. One D1 per enterprise
(D20): `tenant_id` is the database boundary, not a column, and every billing query is store-scoped from
the device token.

## 1. Catalog checkpoint pull (the hot path)

```sql
-- One scalar checkpoint, one range scan, ordered. The journal carries references; the delta
-- response also carries each row's document, loaded at current state in the same db.batch()
-- (api-routes.md §3.3 wins over any fetch-by-id step here).
SELECT seq, entity, entity_id, op, version
FROM catalog_changes
WHERE seq > ?1
ORDER BY seq
LIMIT ?2;
```

The terminal applies rows in order and advances its checkpoint to the last `seq` only after the local
transaction commits. Needs `PRIMARY KEY (seq)` and nothing else: the rowid order is the index.

There is no `GET /v1/catalog/documents?ids=...` and no second round trip. The documents for one page
are loaded as extra statements inside the same `db.batch()`, which D1 counts as one call no matter how
many statements it holds, so embedding them costs no extra invocation budget. The page limit (`limit`,
cap 500, 8 MB byte bound) is what keeps the assembled documents inside one response.

## 2. Alias / barcode resolution

```sql
SELECT item_id, qty_base
FROM item_identifiers
WHERE store_id = ?1 AND alias = ?2 AND active = 1;
```

Exact match, unique per store. Needs `UNIQUE (store_id, alias) WHERE active = 1`.

## 3. Price for a quantity at a point in time

```sql
SELECT price_minor, version
FROM item_prices
WHERE item_id = ?1
  AND qty_base = ?2
  AND (store_id IS ?3 OR store_id IS NULL)
  AND valid_from <= ?4
  AND (valid_to IS NULL OR valid_to > ?4)
ORDER BY store_id IS NULL, valid_from DESC
LIMIT 1;
```

Store override wins over the franchise default; `?3` is the token's store and `NULL` never matches `IS
?3`, so a default row can only arrive through the second disjunct. The scope filter is part of the key,
never an afterthought: dropping it is how a tiered sale resolves to zero rows and is silently never
flagged.

Needs `(item_id, store_id, valid_from)` (`idx_item_prices_validity`). The index bounds the scan to the
candidates for this item and tier; `EXPLAIN QUERY PLAN` still shows `USE TEMP B-TREE FOR ORDER BY`
because the ordering key is `store_id IS NULL`, which no index stores. That sort is bounded by the
number of open rows per scope and MRP variant, so the temp b-tree is over a handful of rows and is not
worth an expression index. Measured plans: `docs/explain-queries.py`.

For a batched item (D27) the server adds `AND batch_id IS ?5`, where `?5` is the line's `batch_id`, so
it prices the batch the cashier billed. The register's scan lookup lists the batches to offer instead of
taking one price:

```sql
-- ?1 item, ?2 store, ?3 business date. First row is the default the register preselects (D28).
WITH live AS (
  SELECT b.id, b.batch_no, b.mrp_minor, b.expiry_date, b.received_at, b.is_primary,
         k.priority, k.name AS book, IFNULL(s.on_hand_qty_base, 0) AS on_hand
  FROM item_batches b
  JOIN books k ON k.id = b.book_id AND k.active = 1
  LEFT JOIN batch_stock s ON s.batch_id = b.id
  WHERE b.item_id = ?1 AND b.store_id = ?2 AND b.retired_at IS NULL
    AND (b.expiry_date IS NULL OR b.expiry_date >= ?3)),
shown AS (                                   -- stocked batches, plus the newest always
  SELECT * FROM live
  WHERE on_hand > 0 OR id = (SELECT id FROM live ORDER BY received_at DESC, id DESC LIMIT 1)),
pick AS (                                    -- main book first, primary first, then newest
  SELECT id FROM shown
  ORDER BY on_hand > 0 DESC, priority, is_primary DESC, received_at DESC, id DESC LIMIT 1)
SELECT shown.*, id = (SELECT id FROM pick) AS is_default
FROM shown
ORDER BY is_default DESC, priority, is_primary DESC, received_at DESC, id DESC;
```

More than one row means the cashier sees the list with the default preselected and can change it; the
chosen batch's prices are `SELECT ... FROM item_prices WHERE item_id = ?1 AND batch_id = ?4 AND valid_to IS
NULL`. Plans on `idx_item_batches_live`, the `books` and `batch_stock` primary keys, and
`uq_item_prices_open` (`explain-queries.py`).

Stock pull (`GET /v1/stock?since=`), store-scoped, by the `stock_seq` cursor:

```sql
SELECT batch_id, on_hand_qty_base, seq FROM batch_stock
WHERE store_id = ?1 AND seq > ?2 ORDER BY seq LIMIT 500;
```

Seeks `idx_batch_stock_store_seq`. The register stores `on_hand = server value − its unsynced sales of
that batch`, so its own pending sales are not counted twice.

## 4. All tiers for an item (pricing decomposition runs in Go)

```sql
SELECT qty_base, price_minor, version
FROM item_prices
WHERE item_id = ?1
  AND (store_id IS ?2 OR store_id IS NULL)
  AND (valid_to IS NULL OR valid_to > ?3)
ORDER BY qty_base;
```

The scope filter is not optional here either. The decomposition walks the open tiers of one scope plus
the defaults, and a query without `store_id` mixes another store's override into the bill, which then
disagrees with the price the terminal charged. Same index as §3, and the same bounded temp b-tree for
`ORDER BY qty_base`.

## 5. Bill push

```text
PRAGMA defer_foreign_keys = on
INSERT INTO bill_lines   ... ON CONFLICT(bill_id, line_no) DO NOTHING
INSERT INTO inventory_movements ...
INSERT INTO bills        ... ON CONFLICT(id) DO NOTHING
```

Lines first so the header's AFTER INSERT trigger can write `sales_daily` from them; header last so a
duplicate (or a concurrent replay) fires no trigger and cannot double-count the rollup. The trigger, not
the Worker, maintains `sales_daily`; `DO NOTHING` is what makes the replay free.

```sql
-- terminal side, for the record
INSERT INTO bills (...) VALUES (...) ON CONFLICT(id) DO NOTHING;
```

plus `UNIQUE (store_id, terminal_id, local_seq)` so a replayed batch with a new id but the same sequence is
rejected rather than duplicated.

## 6. Unsynced bill scan (terminal, local SQLite)

```sql
SELECT id FROM bills
WHERE synced_at IS NULL
ORDER BY local_seq
LIMIT 100;
```

Needs `(local_seq) WHERE synced_at IS NULL` (`idx_bills_unsynced_seq`, `0005-history-and-outbox.sql`).
The predicate column is not in the key: the scan filters the partial index and walks it in `local_seq`
order, which is the drain order. An index on `(synced_at, local_seq)` would force a temp b-tree and is
the mistake that migration removed.

## 7. Day report, per item

```sql
SELECT bl.item_id, SUM(bl.qty_base) AS qty, SUM(bl.line_total_minor) AS amount
FROM bill_lines bl
JOIN bills b ON b.id = bl.bill_id
WHERE b.store_id = ?1 AND b.business_date = ?2 AND b.voided = 0
GROUP BY bl.item_id;
```

A single day is the only range that reads `bill_lines`. Needs `bills(store_id, business_date, voided, id,
total_minor)` (`idx_bills_store_date`, rebuilt non-partial by 0008 so the archival export can use it too)
and `bill_lines(bill_id, item_id, qty_base, line_total_minor)` (`idx_bill_lines_report`), so the join is
two index ranges. The response's
`item_name` is a separate lookup by `item_id` against `items`, not a group key: the name a bill line
carries is the name at sale time, and grouping by it would split one item after a rename.

## 8. Month / multi-day report, per item

```sql
SELECT item_id, SUM(qty_base) AS qty, SUM(amount_minor) AS amount
FROM sales_daily
WHERE store_id = ?1 AND business_date BETWEEN ?2 AND ?3
GROUP BY item_id;
```

Multi-day ranges read the rollup, primary key `(store_id, business_date, item_id)`, so the range scan is
the aggregate. Caps at 92 days (`413 range_too_large`). `sales_daily` is maintained by the AFTER INSERT
triggers on `bills` and `bill_voids`, so a void is already subtracted and the query needs no `voided`
filter. It is also what keeps a month report cheap after archival (D22): the bills and lines may be in
R2, the rollup is still in D1.

## 9. Average price per item (report column)

```sql
SELECT item_id,
       SUM(line_total_minor) / NULLIF(SUM(qty_base), 0) AS avg_price
FROM bill_lines
WHERE bill_id IN (SELECT id FROM bills WHERE store_id = ?1 AND business_date = ?2 AND voided = 0)
GROUP BY item_id;
```

Single day only, for the same reason as §7. A multi-day average comes from `sales_daily`
(`SUM(amount_minor) / NULLIF(SUM(qty_base),0)`), which is exact because both sums are additive.

## 10. Override listing (owner review)

```sql
SELECT b.business_date, b.display_no, bl.item_name, bl.list_price_minor, bl.price_minor,
       bl.override_by, bl.override_reason, bl.policy_violation
FROM bill_lines bl
JOIN bills b ON b.id = bl.bill_id
WHERE b.store_id = ?1
  AND bl.price_source IN ('override', 'manual')
  AND b.business_date BETWEEN ?2 AND ?3
  AND b.voided = 0
ORDER BY b.business_date DESC;
```

The date range seeks `idx_bills_store_date`; each bill's lines come by primary key. The override lines of
live bills are also covered by `idx_bill_lines_override_live` (0004), which the owner's per-bill review uses.

## 11. Device credential lookup at token time

```sql
SELECT id, store_id, terminal_id, credential_hash, install_id, revoked_at, last_catalog_seq
FROM devices
WHERE credential_prefix = ?1;
```

Then compare the hash in constant time. Needs `UNIQUE (credential_prefix)`, which `0008-audit-fixes.sql`
adds; before it the column had a plain index and two rows could share a prefix. The same row carries the
approval state, so a credential that exists but is still pending returns `403 approval_pending` (T13)
and no JWT is issued.

## 12. Session lookup

```sql
SELECT id, user_id, expires_at, created_at
FROM auth_session
WHERE token = ?1;
```

The admin session is Better Auth's: the cookie value is a signed token, the middleware verifies the
signature and then reads `auth_session.token` (D17 revised). `created_at` is the base for the 30-day
absolute cap; Better Auth's own `expires_at` carries the 12 h sliding window. The custom `sessions`
table and its `token_prefix`/`token_hash` columns are gone, so nothing in the Worker may look a session
up by prefix.

## 13. Staff roster for a store (catalog companion)

```sql
-- tenant is the database boundary, not a column
SELECT u.id, u.name, c.pin_hash, c.pin_salt
FROM user_stores us
JOIN users u ON u.id = us.user_id
LEFT JOIN staff_credentials c ON c.user_id = u.id
WHERE us.store_id = ?1 AND u.active = 1;
```

The PIN hash moved to `staff_credentials` (`0007-better-auth.sql`), so `users` no longer carries a
credential; the LEFT JOIN keeps an activated-but-unset user in the roster. Plans on
`user_stores(store_id)` and the `staff_credentials` primary key, and it is the exact query in
`docs/explain-queries.py`. Only the roster the terminal needs reaches a device; `pin_hash` never leaves
the store it belongs to.

## 14. Pruning the change log

```sql
-- floor: the slowest active device decides how much history is still needed
SELECT MIN(last_catalog_seq) FROM devices
WHERE revoked_at IS NULL AND last_catalog_seq IS NOT NULL;

DELETE FROM catalog_changes WHERE seq < ?1;   -- ?1 = catalog_seq.pruned_below, minus a margin
```

`catalog_seq.pruned_below` is advanced by the cron from `MIN(devices.last_catalog_seq)` over active
devices, minus a safety margin. `last_catalog_seq` is written on every successful pull
(`0008-audit-fixes.sql`), which is the only per-device cursor the server keeps; a device that stops
pulling holds the floor down, so revoke it rather than ignoring it. A terminal whose `since` is below the
floor gets `410 stale_cursor` and a full snapshot.

## 15. Stale price check on push

Each line names its tier set exactly (D26): `price_version` and `price_store_id` (`NULL` = franchise
default). The check looks that set up by key, then asks whether it was still open when the bill was made.
No clock comparison decides *which* version the register used; the line says so.

```sql
SELECT qty_base, price_minor, valid_from, valid_to FROM item_prices
WHERE item_id = ?1
  AND IFNULL(store_id, '') = IFNULL(?2, '')    -- ?2 = bill_lines.price_store_id
  AND version = ?3;                            -- ?3 = bill_lines.price_version
```

No rows: `price_mismatch` (a version the server never issued). Rows closed (`valid_to <= created_at`):
`stale_price`, the register had not synced the change. Claimed tier prices that differ from these rows:
`price_mismatch`. `computed_list_price_minor` still comes from the §3 query at `created_at`. Diagnostic only;
the money is never rewritten. Seeks `uq_item_prices_key (item_id, IFNULL(store_id,''), qty_base, version)`
on its first two columns and filters version. It runs inside the bill's batch, so it costs no extra D1 call.

## 16. Archival delete (monthly job, per store-month)

Export first, verify, advance `schema_meta.archive_before` past the month, then delete in bounded id
batches. The delete is the part that needs an index.

```sql
-- 1. the batch to export and delete (range seek on idx_bills_store_date)
SELECT id FROM bills
WHERE store_id = ?1 AND business_date < ?2
ORDER BY business_date, id
LIMIT 500;

-- 2. one db.batch(): voids first, because bill_voids references bills ON DELETE RESTRICT
DELETE FROM bill_voids WHERE bill_id IN (...500 ids...);
DELETE FROM bills      WHERE id      IN (...500 ids...);   -- cascades bill_lines and inventory_movements
```

`DELETE FROM bills WHERE ... LIMIT` is not SQLite syntax, hence the id list: the job selects at most 500
ids and deletes exactly those. `bill_lines` and `inventory_movements` go by FK cascade; `sales_daily`
stays, so reports keep answering (§8), and the exported JSONL in R2 holds the bills, lines and voids.

The cascade reads `inventory_movements` once per deleted line, so it needs
`idx_inventory_movements_line ON inventory_movements(bill_id, bill_line_no)`. Measured on 900 bills:
without the index the delete took 8,048 ms and read 73,463,700 rows; with it, 55 ms and 33,600 rows.
`0008-audit-fixes.sql` adds it.

Space comes back at DELETE time on D1: 300 one-megabyte rows moved the database to 301 MB and deleting
half brought it to 151 MB. `VACUUM` is refused inside a transaction and is unnecessary. In the measured
run 900 bills took the database from 15.60 MB to 12.67 MB, about 65% of their footprint.

A `BEFORE DELETE ON bills` trigger refuses any bill with `business_date >= schema_meta.archive_before`
(`RAISE bill_not_archivable`), so a mis-ordered job cannot delete a bill the export never saw. Bills a
terminal later voids after the row is gone are parked `bill_archived` (api-routes.md §3.4).

## What this catalogue rules out

- `updated_at` as the checkpoint: query 1 could miss a row whose update committed with an earlier timestamp,
  and cannot represent a delete.
- Per-table cursors: the terminal would carry a vector, not a checkpoint.
- A fetch-by-id step after the delta: query 1's page already carries its documents (api-routes.md §3.3),
  and a second call per page is a failure mode with no benefit.
- Recomputing a bill line from `item_prices` at report time: queries 7 and 9 read `bill_lines`; queries 8
  and the multi-day average read `sales_daily`.
- `LIKE '%alias%'` resolution: query 2 is an equality hit, or the index is wasted.
- Archival without `idx_inventory_movements_line`: the FK cascade degrades from 55 ms to 8,048 ms per
  900 bills, which is the difference between a nightly job and an outage.
