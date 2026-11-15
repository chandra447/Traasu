-- Migration 0008 — fixes from the architecture audit and the D1 experiments (2026-11-14).
-- See docs/review-architecture.md for the evidence behind each block and DECISIONS.md D12–D25.
-- Additive except for trigger replacements. Pre-code: no tenant has applied 0001–0007 yet.

-- ---------------------------------------------------------------------------
-- Archival delete. A bill delete cascades to bill_lines and then to
-- inventory_movements, whose FK had no index: measured on D1, deleting 900
-- bills took 8,048 ms and read 73,463,700 rows; with this index, 55 ms and
-- 33,600 rows. Every FK child that a cascade reaches needs an index on the key.
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_inventory_movements_line
  ON inventory_movements(bill_id, bill_line_no);

-- The archival export reads every bill, voided or not, by store and date. The
-- 0001 index was partial (voided = 0) and so unusable there: the export fell
-- back to scanning the store's whole history. Same columns plus voided, no
-- extra index; the day report still seeks on (store_id, business_date, 0).
DROP INDEX IF EXISTS idx_bills_store_date;
CREATE INDEX IF NOT EXISTS idx_bills_store_date
  ON bills(store_id, business_date, voided, id, total_minor);

-- Retention (D22). Bills older than archive_before may be deleted after export
-- to R2; nothing newer can be. '0000-01-01' means nothing is archivable.
ALTER TABLE schema_meta ADD COLUMN archive_before TEXT NOT NULL DEFAULT '0000-01-01'
  CHECK (archive_before GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]');

CREATE TRIGGER IF NOT EXISTS trg_bills_delete_archived_only
BEFORE DELETE ON bills
WHEN OLD.business_date >= (SELECT archive_before FROM schema_meta WHERE id = 1)
BEGIN
  SELECT RAISE(ABORT, 'bill_not_archivable');
END;

-- Bills' parents are deactivated, never deleted (D25, schema review D30):
-- devices and shifts are referenced with ON DELETE SET NULL, which would
-- silently erase attribution on immutable money rows.
CREATE TRIGGER IF NOT EXISTS trg_devices_no_delete
BEFORE DELETE ON devices
BEGIN
  SELECT RAISE(ABORT, 'device_deactivate_only');
END;

CREATE TRIGGER IF NOT EXISTS trg_shifts_no_delete
BEFORE DELETE ON shifts
BEGIN
  SELECT RAISE(ABORT, 'shift_close_only');
END;

-- ---------------------------------------------------------------------------
-- Money identity (D23). Lines first, header last, in one transaction with
-- PRAGMA defer_foreign_keys = on, so the header can be checked against them.
-- ---------------------------------------------------------------------------

-- 0002's line identity omitted IGST, so an IGST line could only be stored with
-- line_total excluding the tax. Lines are frozen (trg_lines_frozen), so the
-- update twin is dropped rather than replaced.
DROP TRIGGER IF EXISTS trg_lines_identity_ins;
DROP TRIGGER IF EXISTS trg_lines_identity_upd;
CREATE TRIGGER IF NOT EXISTS trg_lines_identity_ins
BEFORE INSERT ON bill_lines
WHEN NEW.line_total_minor <> NEW.taxable_minor + NEW.cgst_minor + NEW.sgst_minor
                             + NEW.cess_minor + NEW.igst_minor
BEGIN
  SELECT RAISE(ABORT, 'line_total_identity');
END;

-- 0003's discount identity was skipped whenever list_amount_minor = 0, the
-- column default, so any discount passed. No bypass now.
DROP TRIGGER IF EXISTS trg_lines_discount_identity;
CREATE TRIGGER IF NOT EXISTS trg_lines_discount_identity
BEFORE INSERT ON bill_lines
WHEN NEW.line_total_minor <> NEW.list_amount_minor - NEW.discount_minor
BEGIN
  SELECT RAISE(ABORT, 'discount_identity');
END;

-- One split rule, so Go and TypeScript cannot disagree on an odd paisa:
-- cgst = floor(tax / 2), sgst = tax - cgst.
CREATE TRIGGER IF NOT EXISTS trg_lines_tax_split
BEFORE INSERT ON bill_lines
WHEN NEW.sgst_minor - NEW.cgst_minor NOT IN (0, 1)
BEGIN
  SELECT RAISE(ABORT, 'tax_split_rule');
END;

-- The header must equal its lines. Fires on a replay too, where the lines
-- already exist and match; ON CONFLICT DO NOTHING then skips the insert.
CREATE TRIGGER IF NOT EXISTS trg_bills_reconcile_lines
BEFORE INSERT ON bills
WHEN NOT EXISTS (SELECT 1 FROM bill_lines WHERE bill_id = NEW.id)
  OR NEW.subtotal_minor <> (SELECT SUM(taxable_minor) FROM bill_lines WHERE bill_id = NEW.id)
  OR NEW.tax_minor <> (SELECT SUM(cgst_minor + sgst_minor + cess_minor + igst_minor)
                       FROM bill_lines WHERE bill_id = NEW.id)
  OR NEW.total_minor <> (SELECT SUM(line_total_minor) FROM bill_lines WHERE bill_id = NEW.id)
                        + NEW.rounding_minor
BEGIN
  SELECT RAISE(ABORT, 'bill_lines_mismatch');
END;

-- Which grant authorised an override (schema review D33). Plain TEXT, not an
-- FK: expired grants are purged and the line must survive that.
ALTER TABLE bill_lines ADD COLUMN approval_grant_id TEXT;

-- ---------------------------------------------------------------------------
-- Sales rollup (D13) is maintained here, not by the Worker. A replayed or
-- concurrent push inserts nothing (ON CONFLICT DO NOTHING) and so fires
-- nothing; the rollup cannot double-count.
-- ---------------------------------------------------------------------------
CREATE TRIGGER IF NOT EXISTS trg_bills_rollup
AFTER INSERT ON bills
BEGIN
  INSERT INTO sales_daily (store_id, business_date, item_id, qty_base, amount_minor, bill_count)
  SELECT NEW.store_id, NEW.business_date, item_id, SUM(qty_base), SUM(line_total_minor), 1
  FROM bill_lines WHERE bill_id = NEW.id
  GROUP BY item_id
  ON CONFLICT (store_id, business_date, item_id) DO UPDATE SET
    qty_base     = qty_base + excluded.qty_base,
    amount_minor = amount_minor + excluded.amount_minor,
    bill_count   = bill_count + 1;
END;

-- ---------------------------------------------------------------------------
-- Voids (D25). bill_voids is the record; the flag and the rollup follow from
-- it in the same statement. A bill arrives unvoided and is never un-voided.
-- ---------------------------------------------------------------------------
CREATE TRIGGER IF NOT EXISTS trg_bills_insert_unvoided
BEFORE INSERT ON bills
WHEN NEW.voided <> 0
BEGIN
  SELECT RAISE(ABORT, 'void_via_bill_voids');
END;

CREATE TRIGGER IF NOT EXISTS trg_bills_void_once
BEFORE UPDATE OF voided, voided_at, voided_by, void_reason ON bills
WHEN OLD.voided = 1
BEGIN
  SELECT RAISE(ABORT, 'void_immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_bill_voids_same_store
BEFORE INSERT ON bill_voids
WHEN NOT EXISTS (SELECT 1 FROM bills WHERE id = NEW.bill_id AND store_id = NEW.store_id)
BEGIN
  SELECT RAISE(ABORT, 'void_store_mismatch');
END;

CREATE TRIGGER IF NOT EXISTS trg_bill_voids_apply
AFTER INSERT ON bill_voids
BEGIN
  UPDATE sales_daily SET
    qty_base     = qty_base - (SELECT SUM(l.qty_base) FROM bill_lines l
                               WHERE l.bill_id = NEW.bill_id AND l.item_id = sales_daily.item_id),
    amount_minor = amount_minor - (SELECT SUM(l.line_total_minor) FROM bill_lines l
                                   WHERE l.bill_id = NEW.bill_id AND l.item_id = sales_daily.item_id),
    bill_count   = bill_count - 1
  WHERE store_id = NEW.store_id
    AND business_date = (SELECT business_date FROM bills WHERE id = NEW.bill_id)
    AND item_id IN (SELECT item_id FROM bill_lines WHERE bill_id = NEW.bill_id)
    AND (SELECT voided FROM bills WHERE id = NEW.bill_id) = 0;
  UPDATE bills SET voided = 1, voided_at = NEW.created_at, voided_by = NEW.user_id,
                   void_reason = NEW.reason
  WHERE id = NEW.bill_id AND voided = 0;
END;

-- ---------------------------------------------------------------------------
-- Staff sessions (D16). A completed sale is accepted and flagged, never
-- aborted: on the terminal these triggers rejected a bill after a 2-second
-- clock step and an open cart after switch-user. The Worker sets
-- unverified_staff instead.
-- ---------------------------------------------------------------------------
DROP TRIGGER IF EXISTS trg_bills_session_window;
DROP TRIGGER IF EXISTS trg_bills_password_needs_session;

ALTER TABLE staff_sessions ADD COLUMN assertion_verified_at INTEGER;
-- An unknown shift is kept, not silently nulled (api-routes §3.4).
ALTER TABLE bills ADD COLUMN unverified_shift_id TEXT;

-- The business day a bill belongs to must be derivable from the row (schema
-- review D42): copy the store's cutover at sale time.
ALTER TABLE bills ADD COLUMN business_day_start_minutes INTEGER NOT NULL DEFAULT 0
  CHECK (business_day_start_minutes BETWEEN 0 AND 1439);

-- ---------------------------------------------------------------------------
-- Devices: two-step enrolment (D5), catalog prune floor (D1), lookup integrity.
-- ---------------------------------------------------------------------------
ALTER TABLE devices ADD COLUMN approved_at INTEGER;
ALTER TABLE devices ADD COLUMN approved_by TEXT REFERENCES users(id) ON DELETE RESTRICT;
ALTER TABLE devices ADD COLUMN last_catalog_seq INTEGER CHECK (last_catalog_seq IS NULL OR last_catalog_seq >= 0);
ALTER TABLE devices ADD COLUMN last_catalog_at INTEGER;

DROP INDEX IF EXISTS idx_devices_prefix;
CREATE UNIQUE INDEX IF NOT EXISTS uq_devices_prefix ON devices(credential_prefix);
CREATE UNIQUE INDEX IF NOT EXISTS uq_devices_install_live ON devices(install_id) WHERE revoked_at IS NULL;

-- Per-store override ceiling; the Worker's tenant defaults apply when NULL.
ALTER TABLE store_settings ADD COLUMN override_ceiling_pct INTEGER
  CHECK (override_ceiling_pct IS NULL OR override_ceiling_pct BETWEEN 0 AND 100);
ALTER TABLE store_settings ADD COLUMN override_ceiling_minor INTEGER
  CHECK (override_ceiling_minor IS NULL OR override_ceiling_minor >= 0);

-- Chunked catalog re-seed is invisible until complete (deploy-inventory §7).
CREATE TABLE IF NOT EXISTS catalog_rebuild (
  generation  INTEGER PRIMARY KEY,
  start_seq   INTEGER NOT NULL CHECK (start_seq > 0),
  end_seq     INTEGER CHECK (end_seq IS NULL OR end_seq >= start_seq),
  complete    INTEGER NOT NULL DEFAULT 0 CHECK (complete IN (0, 1)),
  started_at  INTEGER NOT NULL
) STRICT;

-- ---------------------------------------------------------------------------
-- Versions a bill line is billed on (D26). The terminal's local catalog is the
-- source of truth for a sale; the line records which item and price versions
-- it used, so the server can identify them exactly instead of guessing by clock.
-- ---------------------------------------------------------------------------

-- Item document version: every admin edit bumps it; catalog_changes carries it.
ALTER TABLE items ADD COLUMN version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0);
ALTER TABLE bill_lines ADD COLUMN item_version INTEGER NOT NULL DEFAULT 1 CHECK (item_version > 0);
-- 1 = the item is sold from batches (D27); 0 = one item-level price set (loose goods).
ALTER TABLE items ADD COLUMN uses_batches INTEGER NOT NULL DEFAULT 0 CHECK (uses_batches IN (0, 1));

-- A version never goes backwards, and the same version never means two
-- documents. Equal version with equal content is allowed: that is a terminal
-- replaying a catalog page.
CREATE TRIGGER IF NOT EXISTS trg_items_version
BEFORE UPDATE ON items
WHEN NEW.version < OLD.version
  OR (NEW.version = OLD.version AND (
        NEW.name IS NOT OLD.name OR NEW.name_local IS NOT OLD.name_local
     OR NEW.dimension IS NOT OLD.dimension OR NEW.pricing_mode IS NOT OLD.pricing_mode
     OR NEW.base_unit IS NOT OLD.base_unit OR NEW.default_sales_unit IS NOT OLD.default_sales_unit
     OR NEW.tax_rate_id IS NOT OLD.tax_rate_id OR NEW.hsn IS NOT OLD.hsn
     OR NEW.price_includes_tax IS NOT OLD.price_includes_tax OR NEW.mrp_minor IS NOT OLD.mrp_minor
     OR NEW.allow_discount_pct IS NOT OLD.allow_discount_pct
     OR NEW.restrict_price_edit IS NOT OLD.restrict_price_edit
     OR NEW.prompt_qty IS NOT OLD.prompt_qty OR NEW.scale_qty IS NOT OLD.scale_qty
     OR NEW.active IS NOT OLD.active OR NEW.uses_batches IS NOT OLD.uses_batches))
BEGIN
  SELECT RAISE(ABORT, 'item_version_not_bumped');
END;

-- ---------------------------------------------------------------------------
-- Batches (D27). Stock of one item arrives in batches: soap received in April
-- at MRP 100 and in May at MRP 200 are two batches, both on the shelf. A batch
-- belongs to one store (stock is physical), carries its MRP, an optional
-- expiry, its own selling-price tier set, and a quantity kept as a ledger.
-- Modelled on GoFrugal's batch-wise billing; see DECISIONS.md D27.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS item_batches (
  id           TEXT PRIMARY KEY,
  item_id      TEXT NOT NULL REFERENCES items(id) ON DELETE RESTRICT,
  store_id     TEXT NOT NULL REFERENCES stores(id) ON DELETE RESTRICT,
  batch_no     TEXT NOT NULL CHECK (length(batch_no) BETWEEN 1 AND 40),
  mrp_minor    INTEGER CHECK (mrp_minor IS NULL OR mrp_minor >= 0),
  -- NULL = no expiry (most grocery). A past date makes the batch unsellable.
  expiry_date  TEXT CHECK (expiry_date IS NULL
                          OR expiry_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  received_at  INTEGER NOT NULL,
  retired_at   INTEGER,
  version      INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
  created_by   TEXT REFERENCES users(id) ON DELETE RESTRICT,
  updated_at   INTEGER NOT NULL
) STRICT;

CREATE UNIQUE INDEX IF NOT EXISTS uq_item_batches_no ON item_batches(store_id, item_id, batch_no);
CREATE INDEX IF NOT EXISTS idx_item_batches_live
  ON item_batches(item_id, store_id, received_at) WHERE retired_at IS NULL;

-- Same versioning rule as items (D26): a change bumps the version; an equal
-- version is only a replay. Batches are retired, never deleted: bill lines name them.
CREATE TRIGGER IF NOT EXISTS trg_item_batches_version
BEFORE UPDATE ON item_batches
WHEN NEW.version < OLD.version
  OR (NEW.version = OLD.version AND (
        NEW.batch_no IS NOT OLD.batch_no OR NEW.mrp_minor IS NOT OLD.mrp_minor
     OR NEW.expiry_date IS NOT OLD.expiry_date OR NEW.retired_at IS NOT OLD.retired_at
     OR NEW.item_id IS NOT OLD.item_id OR NEW.store_id IS NOT OLD.store_id))
BEGIN
  SELECT RAISE(ABORT, 'batch_version_not_bumped');
END;

CREATE TRIGGER IF NOT EXISTS trg_item_batches_no_delete
BEFORE DELETE ON item_batches
BEGIN
  SELECT RAISE(ABORT, 'batch_retire_only');
END;

-- A batch's selling prices are item_prices rows tagged with the batch, in the
-- batch's store. batch_id NULL = the item-level price set, as before.
ALTER TABLE item_prices ADD COLUMN batch_id TEXT;

CREATE TRIGGER IF NOT EXISTS trg_item_prices_batch_scope
BEFORE INSERT ON item_prices
WHEN NEW.batch_id IS NOT NULL AND NOT EXISTS (
  SELECT 1 FROM item_batches b
  WHERE b.id = NEW.batch_id AND b.item_id = NEW.item_id AND b.store_id IS NEW.store_id)
BEGIN
  SELECT RAISE(ABORT, 'batch_price_scope');
END;

-- Several batches can have open prices at once; within one batch, one open
-- price per tier.
DROP INDEX IF EXISTS uq_item_prices_open;
CREATE UNIQUE INDEX IF NOT EXISTS uq_item_prices_open
  ON item_prices(item_id, IFNULL(store_id, ''), qty_base, IFNULL(batch_id, ''))
  WHERE valid_to IS NULL;

-- A price version names one revision of one scope and one batch (schema
-- review D17): versions are per item across all scopes and batches and only
-- grow, so (item_id, price_version) identifies the exact tier set a line used.
CREATE TRIGGER IF NOT EXISTS trg_item_prices_version_scope
BEFORE INSERT ON item_prices
WHEN EXISTS (SELECT 1 FROM item_prices p
             WHERE p.item_id = NEW.item_id
               AND (p.version > NEW.version
                    OR (p.version = NEW.version
                        AND (IFNULL(p.store_id, '') <> IFNULL(NEW.store_id, '')
                             OR IFNULL(p.batch_id, '') <> IFNULL(NEW.batch_id, '')))))
BEGIN
  SELECT RAISE(ABORT, 'price_version_scope');
END;

-- No half-open revision (schema review D16): within one scope and one batch,
-- the writer closes every open row, then inserts the whole new tier set at one
-- version and one valid_from. Other batches stay open.
CREATE TRIGGER IF NOT EXISTS trg_item_prices_revision_coherent
BEFORE INSERT ON item_prices
WHEN EXISTS (SELECT 1 FROM item_prices p
             WHERE p.item_id = NEW.item_id
               AND IFNULL(p.store_id, '') = IFNULL(NEW.store_id, '')
               AND IFNULL(p.batch_id, '') = IFNULL(NEW.batch_id, '')
               AND p.valid_to IS NULL
               AND (p.version <> NEW.version OR p.valid_from <> NEW.valid_from))
BEGIN
  SELECT RAISE(ABORT, 'price_revision_mixed');
END;

-- What the cashier billed, copied onto the immutable line. batch_id is plain
-- TEXT, not an FK: an unknown batch is flagged by the Worker, never rejected.
ALTER TABLE bill_lines ADD COLUMN batch_id TEXT;
ALTER TABLE bill_lines ADD COLUMN batch_no TEXT;
ALTER TABLE bill_lines ADD COLUMN mrp_minor INTEGER CHECK (mrp_minor IS NULL OR mrp_minor >= 0);

-- ---------------------------------------------------------------------------
-- Batch quantity. inventory_movements is the ledger: receipt and adjustment
-- rows come from admin, sale rows from bills, void rows from voids. batch_stock
-- is the running balance, kept by trigger; it may go negative, because a
-- register sells offline and v1 never blocks a sale on stock.
-- ---------------------------------------------------------------------------
ALTER TABLE inventory_movements ADD COLUMN batch_id TEXT;
ALTER TABLE inventory_movements ADD COLUMN store_id TEXT;
ALTER TABLE inventory_movements ADD COLUMN reason TEXT NOT NULL DEFAULT 'sale'
  CHECK (reason IN ('receipt', 'adjustment', 'sale', 'void'));
ALTER TABLE inventory_movements ADD COLUMN note TEXT;
ALTER TABLE inventory_movements ADD COLUMN created_by TEXT;

CREATE INDEX IF NOT EXISTS idx_inventory_movements_batch
  ON inventory_movements(batch_id, occurred_at) WHERE batch_id IS NOT NULL;

-- Stock cursor for GET /v1/stock: one allocator, so commit order is seq order
-- (the same argument as catalog_seq, D1). Kept out of catalog_changes, which
-- would otherwise gain a row for every line of every sale.
CREATE TABLE IF NOT EXISTS stock_seq (
  id        INTEGER PRIMARY KEY CHECK (id = 1),
  last_seq  INTEGER NOT NULL CHECK (last_seq >= 0)
) STRICT;
INSERT OR IGNORE INTO stock_seq (id, last_seq) VALUES (1, 0);

CREATE TABLE IF NOT EXISTS batch_stock (
  batch_id          TEXT PRIMARY KEY REFERENCES item_batches(id) ON DELETE RESTRICT,
  store_id          TEXT NOT NULL,
  on_hand_qty_base  INTEGER NOT NULL,
  seq               INTEGER NOT NULL,
  updated_at        INTEGER NOT NULL
) STRICT;

CREATE INDEX IF NOT EXISTS idx_batch_stock_store_seq ON batch_stock(store_id, seq);

-- The balance moves on INSERT only. Archival deletes old sale movements by
-- cascade and must not change what is on the shelf.
CREATE TRIGGER IF NOT EXISTS trg_movements_batch_stock
AFTER INSERT ON inventory_movements
WHEN NEW.batch_id IS NOT NULL AND EXISTS (SELECT 1 FROM item_batches WHERE id = NEW.batch_id)
BEGIN
  UPDATE stock_seq SET last_seq = last_seq + 1 WHERE id = 1;
  INSERT INTO batch_stock (batch_id, store_id, on_hand_qty_base, seq, updated_at)
  SELECT b.id, b.store_id,
         CASE NEW.direction WHEN 'in' THEN NEW.qty_base ELSE -NEW.qty_base END,
         (SELECT last_seq FROM stock_seq WHERE id = 1), NEW.created_at
  FROM item_batches b WHERE b.id = NEW.batch_id
  ON CONFLICT (batch_id) DO UPDATE SET
    on_hand_qty_base = on_hand_qty_base + excluded.on_hand_qty_base,
    seq              = excluded.seq,
    updated_at       = excluded.updated_at;
END;

-- A void puts the goods back: one 'in' movement per sale movement of the bill.
-- The id is derived from the void and the line, so it is deterministic.
CREATE TRIGGER IF NOT EXISTS trg_bill_voids_restock
AFTER INSERT ON bill_voids
BEGIN
  INSERT INTO inventory_movements (id, item_id, bill_id, bill_line_no, direction, qty_base, occurred_at,
                                   created_at, batch_id, store_id, reason, created_by)
  SELECT NEW.id || ':' || m.bill_line_no, m.item_id, m.bill_id, m.bill_line_no, 'in', m.qty_base,
         NEW.created_at, NEW.created_at, m.batch_id, m.store_id, 'void', NEW.user_id
  FROM inventory_movements m
  WHERE m.bill_id = NEW.bill_id AND m.reason = 'sale';
END;

UPDATE schema_meta SET schema_version = 8 WHERE id = 1;
