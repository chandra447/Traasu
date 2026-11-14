-- Migration 0002 — fixes from the adversarial reviews.
-- Additive only: no table rebuild, no dropped column, safe to run on a live tenant.
-- Apply after 0001 (docs/d1-schema.sql) on both D1 and every terminal's SQLite.

-- ---------------------------------------------------------------------------
-- B2. Sync epoch: a Time Travel restore or destructive migration rewinds
-- catalog_changes.seq. Terminals must be able to tell "I am behind" from
-- "the world was rewound". Bump this on any restore; terminals full-resync.
-- ---------------------------------------------------------------------------
ALTER TABLE schema_meta ADD COLUMN sync_epoch INTEGER NOT NULL DEFAULT 1
  CHECK (sync_epoch > 0);

-- ---------------------------------------------------------------------------
-- B1. Per-bill idempotency. A replay with the same id but a different body is
-- a bug worth detecting, not a silent no-op.
-- ---------------------------------------------------------------------------
ALTER TABLE bills ADD COLUMN payload_sha256 TEXT
  CHECK (payload_sha256 IS NULL OR length(payload_sha256) = 64);

-- ---------------------------------------------------------------------------
-- B4. Money identity. line_total_minor means "tax-inclusive amount charged".
-- The header must reconcile against its lines, and the tax reading of a line
-- must not change when the item master changes later.
-- ---------------------------------------------------------------------------
ALTER TABLE bills ADD COLUMN subtotal_minor INTEGER NOT NULL DEFAULT 0
  CHECK (subtotal_minor >= 0);
ALTER TABLE bills ADD COLUMN tax_minor INTEGER NOT NULL DEFAULT 0
  CHECK (tax_minor >= 0);

ALTER TABLE bill_lines ADD COLUMN taxable_minor INTEGER NOT NULL DEFAULT 0
  CHECK (taxable_minor >= 0);
ALTER TABLE bill_lines ADD COLUMN price_includes_tax INTEGER NOT NULL DEFAULT 1
  CHECK (price_includes_tax IN (0, 1));
ALTER TABLE bill_lines ADD COLUMN hsn TEXT;

-- SQLite cannot add a table-level CHECK by ALTER, so the two identities are
-- enforced by triggers. They fire on every insert and update, on D1 and on the
-- terminal, which is the point: the terminal must not be able to write a bill
-- its own report would not reconcile.
CREATE TRIGGER IF NOT EXISTS trg_bills_identity_ins
BEFORE INSERT ON bills
WHEN NEW.total_minor <> NEW.subtotal_minor + NEW.tax_minor + NEW.rounding_minor
BEGIN
  SELECT RAISE(ABORT, 'bill_total_identity');
END;

CREATE TRIGGER IF NOT EXISTS trg_bills_identity_upd
BEFORE UPDATE OF total_minor, subtotal_minor, tax_minor, rounding_minor ON bills
WHEN NEW.total_minor <> NEW.subtotal_minor + NEW.tax_minor + NEW.rounding_minor
BEGIN
  SELECT RAISE(ABORT, 'bill_total_identity');
END;

CREATE TRIGGER IF NOT EXISTS trg_lines_identity_ins
BEFORE INSERT ON bill_lines
WHEN NEW.line_total_minor <> NEW.taxable_minor + NEW.cgst_minor + NEW.sgst_minor + NEW.cess_minor
BEGIN
  SELECT RAISE(ABORT, 'line_total_identity');
END;

CREATE TRIGGER IF NOT EXISTS trg_lines_identity_upd
BEFORE UPDATE OF line_total_minor, taxable_minor, cgst_minor, sgst_minor, cess_minor ON bill_lines
WHEN NEW.line_total_minor <> NEW.taxable_minor + NEW.cgst_minor + NEW.sgst_minor + NEW.cess_minor
BEGIN
  SELECT RAISE(ABORT, 'line_total_identity');
END;

-- ---------------------------------------------------------------------------
-- B5. Sales rollup. A month report must not scan bill_lines on a single-threaded
-- D1: it blocks every terminal's push and approaches the 30 s query cap.
-- Upserted in the same batch as the bill. Rebuildable from bill_lines.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sales_daily (
  store_id      TEXT NOT NULL,
  business_date TEXT NOT NULL
                CHECK (business_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  item_id       TEXT NOT NULL,
  qty_base      INTEGER NOT NULL CHECK (qty_base >= 0),
  amount_minor  INTEGER NOT NULL CHECK (amount_minor >= 0),
  bill_count    INTEGER NOT NULL DEFAULT 0 CHECK (bill_count >= 0),
  PRIMARY KEY (store_id, business_date, item_id)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_sales_daily_item
  ON sales_daily(item_id, business_date);

-- Rebuild after a restore, a void, or a rollup bug:
--   DELETE FROM sales_daily WHERE store_id = ?1 AND business_date BETWEEN ?2 AND ?3;
--   INSERT INTO sales_daily (store_id, business_date, item_id, qty_base, amount_minor, bill_count)
--   SELECT b.store_id, b.business_date, bl.item_id, SUM(bl.qty_base),
--          SUM(bl.line_total_minor), COUNT(DISTINCT b.id)
--   FROM bill_lines bl JOIN bills b ON b.id = bl.bill_id
--   WHERE b.store_id = ?1 AND b.business_date BETWEEN ?2 AND ?3 AND b.voided = 0
--   GROUP BY b.store_id, b.business_date, bl.item_id;

-- ---------------------------------------------------------------------------
-- B6. Offline lease is policy, but the terminal needs the value. Keep it in
-- terminal_config (already catalogued); no schema change here.
-- ---------------------------------------------------------------------------

-- ---------------------------------------------------------------------------
-- B8. Reports must be able to say "provisional". received_at already exists on
-- bills; this index makes the watermark query cheap.
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_bills_received_at ON bills(received_at);
