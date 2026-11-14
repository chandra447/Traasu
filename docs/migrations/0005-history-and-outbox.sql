-- Migration 0005 — two cheap fixes from the schema review (D20, D40).

-- D40. The outbox scan orders by local_seq, so the partial index must be on
-- local_seq, not created_at. The old index forced a temp b-tree on every drain.
DROP INDEX IF EXISTS idx_bills_unsynced;
CREATE INDEX IF NOT EXISTS idx_bills_unsynced_seq
  ON bills(local_seq) WHERE synced_at IS NULL;

-- D20. A price row is history. Editing one in place changes what an old bill
-- meant, so updates are refused and a correction is a new version.
CREATE TRIGGER IF NOT EXISTS trg_item_prices_frozen
BEFORE UPDATE OF item_id, store_id, qty_base, version, price_minor, mrp_minor, valid_from ON item_prices
BEGIN
  SELECT RAISE(ABORT, 'price_row_immutable');
END;

-- Closing a revision is the one legal update: valid_to goes from NULL to a value.
CREATE TRIGGER IF NOT EXISTS trg_item_prices_close_only
BEFORE UPDATE OF valid_to ON item_prices
WHEN OLD.valid_to IS NOT NULL
BEGIN
  SELECT RAISE(ABORT, 'revision_already_closed');
END;

-- D18. A validity window that ends before it starts is never valid.
CREATE TRIGGER IF NOT EXISTS trg_item_prices_window
BEFORE INSERT ON item_prices
WHEN NEW.valid_to IS NOT NULL AND NEW.valid_to <= NEW.valid_from
BEGIN
  SELECT RAISE(ABORT, 'invalid_validity_window');
END;
