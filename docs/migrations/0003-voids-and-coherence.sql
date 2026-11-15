-- Migration 0003 — holes found while the schema agent finished.
-- Additive only.

-- ---------------------------------------------------------------------------
-- Voids cannot travel through an insert-only push. A void is a new fact about
-- an old bill, so it gets its own append-only stream that syncs like bills.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bill_voids (
  id          TEXT PRIMARY KEY,
  bill_id     TEXT NOT NULL REFERENCES bills(id) ON DELETE RESTRICT,
  store_id    TEXT NOT NULL REFERENCES stores(id) ON DELETE RESTRICT,
  terminal_id TEXT NOT NULL REFERENCES terminals(id) ON DELETE RESTRICT,
  user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  reason      TEXT NOT NULL CHECK (length(reason) BETWEEN 1 AND 200),
  created_at  INTEGER NOT NULL,
  received_at INTEGER,
  synced_at   INTEGER
) STRICT;

CREATE UNIQUE INDEX IF NOT EXISTS uq_bill_voids_bill ON bill_voids(bill_id);
CREATE INDEX IF NOT EXISTS idx_bill_voids_unsynced ON bill_voids(created_at) WHERE synced_at IS NULL;

-- ---------------------------------------------------------------------------
-- A bill must not claim a terminal that belongs to another store. SQLite cannot
-- add a composite FK by ALTER, so the check is a trigger. It runs on D1 and on
-- the terminal, which is what makes it useful.
-- ---------------------------------------------------------------------------
CREATE TRIGGER IF NOT EXISTS trg_bills_terminal_store_ins
BEFORE INSERT ON bills
WHEN NOT EXISTS (
  SELECT 1 FROM terminals t WHERE t.id = NEW.terminal_id AND t.store_id = NEW.store_id
)
BEGIN
  SELECT RAISE(ABORT, 'terminal_store_mismatch');
END;

-- ---------------------------------------------------------------------------
-- Tax: IGST is the inter-state counterpart of CGST+SGST. A line carries one or
-- the other, never both.
-- ---------------------------------------------------------------------------
ALTER TABLE bill_lines ADD COLUMN igst_minor INTEGER NOT NULL DEFAULT 0
  CHECK (igst_minor >= 0);

CREATE TRIGGER IF NOT EXISTS trg_lines_tax_exclusive
BEFORE INSERT ON bill_lines
WHEN NEW.igst_minor > 0 AND (NEW.cgst_minor > 0 OR NEW.sgst_minor > 0)
BEGIN
  SELECT RAISE(ABORT, 'tax_split_conflict');
END;

-- ---------------------------------------------------------------------------
-- A discount must reconcile against the list amount, or a terminal can claim a
-- discount that never existed. list_amount_minor is the pre-discount amount for
-- the line at the applied price source.
-- ---------------------------------------------------------------------------
ALTER TABLE bill_lines ADD COLUMN list_amount_minor INTEGER NOT NULL DEFAULT 0
  CHECK (list_amount_minor >= 0);

CREATE TRIGGER IF NOT EXISTS trg_lines_discount_identity
BEFORE INSERT ON bill_lines
WHEN NEW.list_amount_minor - NEW.discount_minor <> NEW.taxable_minor + NEW.cgst_minor
     + NEW.sgst_minor + NEW.cess_minor + NEW.igst_minor
     AND NEW.list_amount_minor > 0
BEGIN
  SELECT RAISE(ABORT, 'discount_identity');
END;

-- ---------------------------------------------------------------------------
-- Which store's price row was applied. Without it, a line cannot be checked for
-- "store override applied at the wrong store".
-- ---------------------------------------------------------------------------
ALTER TABLE bill_lines ADD COLUMN price_store_id TEXT
  CHECK (price_store_id IS NULL OR length(price_store_id) > 0);

UPDATE schema_meta SET schema_version = 3 WHERE id = 1;
