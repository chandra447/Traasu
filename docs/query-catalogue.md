# Query catalogue — the queries the schema must serve

Written independently of the schema agents, as a yardstick. Every query here must be servable from an index
in `docs/d1-schema.sql`; if one is not, the schema has a gap.

`?` marks bound parameters. All money in paise, all quantities in base units.

## 1. Catalog checkpoint pull (the hot path)

```sql
-- one scalar checkpoint, one range scan, ordered. The journal carries references,
-- not payloads: the terminal then fetches the documents by id in batches of 100.
SELECT seq, entity, entity_id, op, version
FROM catalog_changes
WHERE seq > ?1
ORDER BY seq
LIMIT ?2;
```

Terminal applies rows in order and advances its checkpoint to the last `seq` only after the local transaction
commits. Needs `PRIMARY KEY (seq)` and nothing else — the rowid order is the index.

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

Store override wins over the franchise default. Needs `(item_id, qty_base, store_id, valid_from)`.

## 4. All tiers for an item (pricing decomposition runs in Go)

```sql
SELECT qty_base, price_minor, version
FROM item_prices
WHERE item_id = ?1 AND (valid_to IS NULL OR valid_to > ?2)
ORDER BY qty_base;
```

## 5. Bill push dedupe

```sql
INSERT INTO bills (...) VALUES (...)
ON CONFLICT(id) DO NOTHING;
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

Needs `(synced_at, local_seq)` — partial index `WHERE synced_at IS NULL`.

## 7. Day report, per item

```sql
SELECT bl.item_id, bl.item_name,
       SUM(bl.qty_base)         AS qty,
       SUM(bl.line_total_minor) AS amount
FROM bill_lines bl
JOIN bills b ON b.id = bl.bill_id
WHERE b.store_id = ?1 AND b.business_date = ?2 AND b.voided = 0
GROUP BY bl.item_id, bl.item_name;
```

Needs `bills(store_id, business_date)` and `bill_lines(bill_id)`.

## 8. Month report, per item

Same shape with `business_date BETWEEN ?2 AND ?3`. The range scan on `bills` is the driving table; per-item
aggregation happens on the joined rows.

## 9. Average price per item (report column)

```sql
SELECT item_id,
       SUM(line_total_minor) / NULLIF(SUM(qty_base), 0) AS avg_price
FROM bill_lines
WHERE bill_id IN (SELECT id FROM bills WHERE store_id = ?1 AND business_date BETWEEN ?2 AND ?3 AND voided = 0)
GROUP BY item_id;
```

## 10. Override listing (owner review)

```sql
SELECT b.business_date, b.display_no, bl.item_name, bl.list_price_minor, bl.price_minor,
       bl.override_by, bl.override_reason, bl.policy_violation
FROM bill_lines bl
JOIN bills b ON b.id = bl.bill_id
WHERE b.store_id = ?1
  AND bl.price_source IN ('override', 'manual')
  AND b.business_date BETWEEN ?2 AND ?3
ORDER BY b.business_date DESC;
```

Needs a partial index on `bill_lines(price_source)` or a `WHERE price_source <> 'list'` partial index.

## 11. Device credential lookup at token time

```sql
SELECT id, store_id, terminal_id, credential_hash, install_id, revoked_at
FROM devices
WHERE credential_prefix = ?1;
```

Then compare the hash in constant time. Needs `UNIQUE (credential_prefix)`.

## 12. Session lookup

```sql
SELECT user_id, tenant_id, role, expires_at, revoked_at
FROM sessions
WHERE id = ?1;
```

Opaque id, hashed at rest; `PRIMARY KEY (id)`.

## 13. Staff roster for a store (catalog companion)

```sql
-- tenant is the database boundary, not a column
SELECT id, name, role, pin_hash, pin_salt, active
FROM users
WHERE active = 1
  AND EXISTS (SELECT 1 FROM user_stores us WHERE us.user_id = users.id AND us.store_id = ?1);
```

## 14. Pruning the change log

```sql
-- prune below the highest watermark every terminal has passed, minus a safety margin
DELETE FROM catalog_changes WHERE seq < ?1;   -- ?1 = catalog_seq.pruned_below
```

`catalog_seq.pruned_below` is advanced by the cron job from the per-device cursor recorded on each pull.
A terminal whose `since` is below it gets `410 stale_cursor` and a full snapshot.

## 15. Stale price check on push

For each pushed line, compare `price_version` with the version valid at `bills.created_at` **using the same
scope resolution as the price itself** — store row first, franchise default second. Omitting the scope is the
bug the schema review found: a tiered sale resolves to zero rows and is silently never flagged.

```sql
SELECT version FROM item_prices
WHERE item_id = ?1 AND qty_base = ?2
  AND (store_id IS ?3 OR store_id IS NULL)
  AND valid_from <= ?4 AND (valid_to IS NULL OR valid_to > ?4)
ORDER BY store_id IS NULL, valid_from DESC
LIMIT 1;
```

A mismatch sets `stale_price` on the line. Diagnostic only.

## What this catalogue rules out

- `updated_at` as the checkpoint: query 1 could miss a row whose update committed with an earlier timestamp,
  and cannot represent a delete.
- Per-table cursors: the terminal would carry a vector, not a checkpoint.
- Recomputing a bill line from `item_prices` at report time: query 7/8 must read `bill_lines` only.
- `LIKE '%alias%'` resolution: query 2 is an equality hit, or the index is wasted.
