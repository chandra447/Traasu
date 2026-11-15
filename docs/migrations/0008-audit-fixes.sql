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
-- Parent/child and repack (D29, D30). parent_item_id: a child item holds no
-- stock; a sale takes content_qty_base of the parent's base units per child
-- base unit. content_qty_base on a repack item: bulk base units in one pack.
ALTER TABLE items ADD COLUMN parent_item_id TEXT REFERENCES items(id) ON DELETE RESTRICT;
ALTER TABLE items ADD COLUMN content_qty_base INTEGER CHECK (content_qty_base IS NULL OR content_qty_base > 0);

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
     OR NEW.active IS NOT OLD.active OR NEW.uses_batches IS NOT OLD.uses_batches
     OR NEW.parent_item_id IS NOT OLD.parent_item_id OR NEW.content_qty_base IS NOT OLD.content_qty_base))
BEGIN
  SELECT RAISE(ABORT, 'item_version_not_bumped');
END;

-- ---------------------------------------------------------------------------
-- Books (D28): stock locations inside one store (shop floor, godown, back
-- room), like GoFrugal's godowns. Every batch lives in one book. priority 1 is
-- the main book; priority orders books in admin screens and in the register's
-- "other batches" prompt. It does not choose the batch billed (D28: latest).
-- Provisioning creates a 'Main' book (priority 1) for every store.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS books (
  id          TEXT PRIMARY KEY,
  store_id    TEXT NOT NULL REFERENCES stores(id) ON DELETE RESTRICT,
  name        TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 40),
  priority    INTEGER NOT NULL CHECK (priority > 0),
  active      INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
  version     INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
  created_at  INTEGER NOT NULL,
  updated_at  INTEGER NOT NULL
) STRICT;

CREATE UNIQUE INDEX IF NOT EXISTS uq_books_name ON books(store_id, name);
-- One active book per priority, so the order of books is never ambiguous.
CREATE UNIQUE INDEX IF NOT EXISTS uq_books_priority ON books(store_id, priority) WHERE active = 1;

CREATE TRIGGER IF NOT EXISTS trg_books_version
BEFORE UPDATE ON books
WHEN NEW.version < OLD.version
  OR NEW.store_id IS NOT OLD.store_id
  OR (NEW.version = OLD.version AND (NEW.name IS NOT OLD.name OR NEW.priority IS NOT OLD.priority
                                     OR NEW.active IS NOT OLD.active))
BEGIN
  SELECT RAISE(ABORT, 'book_version_not_bumped');
END;

CREATE TRIGGER IF NOT EXISTS trg_books_no_delete
BEFORE DELETE ON books
BEGIN
  SELECT RAISE(ABORT, 'book_deactivate_only');
END;

-- ---------------------------------------------------------------------------
-- Batches (D27). Stock of one item arrives in batches: soap received in April
-- at MRP 100 and in May at MRP 200 are two batches, both on the shelf. A batch
-- belongs to one store and one book, carries its MRP, an optional expiry, its
-- own selling-price tier set, and a quantity kept as a ledger. The register
-- bills the latest batch and prompts about others (D28).
-- Modelled on GoFrugal's batch-wise billing; see DECISIONS.md D27, D28.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS item_batches (
  id           TEXT PRIMARY KEY,
  item_id      TEXT NOT NULL REFERENCES items(id) ON DELETE RESTRICT,
  store_id     TEXT NOT NULL REFERENCES stores(id) ON DELETE RESTRICT,
  book_id      TEXT NOT NULL REFERENCES books(id) ON DELETE RESTRICT,
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

CREATE TRIGGER IF NOT EXISTS trg_item_batches_book_store
BEFORE INSERT ON item_batches
WHEN NOT EXISTS (SELECT 1 FROM books k WHERE k.id = NEW.book_id AND k.store_id = NEW.store_id)
BEGIN
  SELECT RAISE(ABORT, 'batch_book_store_mismatch');
END;

-- Same versioning rule as items (D26): a change bumps the version; an equal
-- version is only a replay. The item, store and book never change: moving
-- stock between books is a transfer, not an edit. Batches are retired, never
-- deleted, because bill lines name them.
CREATE TRIGGER IF NOT EXISTS trg_item_batches_version
BEFORE UPDATE ON item_batches
WHEN NEW.version < OLD.version
  OR NEW.item_id IS NOT OLD.item_id OR NEW.store_id IS NOT OLD.store_id OR NEW.book_id IS NOT OLD.book_id
  OR (NEW.version = OLD.version AND (
        NEW.batch_no IS NOT OLD.batch_no OR NEW.mrp_minor IS NOT OLD.mrp_minor
     OR NEW.expiry_date IS NOT OLD.expiry_date OR NEW.retired_at IS NOT OLD.retired_at))
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
  CHECK (reason IN ('receipt', 'adjustment', 'sale', 'void', 'repack'));
ALTER TABLE inventory_movements ADD COLUMN note TEXT;
ALTER TABLE inventory_movements ADD COLUMN created_by TEXT;

-- A purchase entry (D28): the admin picks a book, then enters lines; every
-- receipt movement of the entry lands in that book. No supplier or cost: purchases
-- stay a v1 non-goal, this only records stock coming in.
CREATE TABLE IF NOT EXISTS stock_receipts (
  id           TEXT PRIMARY KEY,
  store_id     TEXT NOT NULL REFERENCES stores(id) ON DELETE RESTRICT,
  book_id      TEXT NOT NULL REFERENCES books(id) ON DELETE RESTRICT,
  reference    TEXT,                       -- the supplier's bill number, free text
  received_at  INTEGER NOT NULL,
  created_by   TEXT REFERENCES users(id) ON DELETE RESTRICT,
  note         TEXT
) STRICT;

CREATE INDEX IF NOT EXISTS idx_stock_receipts_store ON stock_receipts(store_id, received_at);

ALTER TABLE inventory_movements ADD COLUMN receipt_id TEXT REFERENCES stock_receipts(id) ON DELETE RESTRICT;

CREATE TRIGGER IF NOT EXISTS trg_movements_receipt_book
BEFORE INSERT ON inventory_movements
WHEN NEW.receipt_id IS NOT NULL AND NOT EXISTS (
  SELECT 1 FROM stock_receipts r JOIN item_batches b ON b.book_id = r.book_id AND b.store_id = r.store_id
  WHERE r.id = NEW.receipt_id AND b.id = NEW.batch_id AND NEW.reason = 'receipt')
BEGIN
  SELECT RAISE(ABORT, 'receipt_book_mismatch');
END;

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

-- ---------------------------------------------------------------------------
-- Parent/child items (D29), GoFrugal's "Parent"/"Child" preparation status.
-- A child (Coffee 1 kg, Biscuit pack of 10) is its own item: own name, prices,
-- tax and barcodes, but no stock. Selling one takes content_qty_base of the
-- parent's base units out of a parent batch. A parent may have many children
-- (piece, pack of 10, box of 200); a child is never a parent, a repack item or
-- a bulk item, so stock never chains. Parents are batched: stock lives in batches.
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_items_parent ON items(parent_item_id) WHERE parent_item_id IS NOT NULL;

-- Repack (D30), GoFrugal's "Bulk"/"Repackage". A repack item (Rice 1 kg) has
-- its own batches and stock, filled from one of its mapped bulk items (Rice 25 kg
-- sack). content_qty_base on the repack item = bulk base units in one pack;
-- every bulk of one repack item has the same base unit, so that number holds for all.
CREATE TABLE IF NOT EXISTS item_repack_sources (
  item_id       TEXT NOT NULL REFERENCES items(id) ON DELETE RESTRICT,   -- the repack item
  bulk_item_id  TEXT NOT NULL REFERENCES items(id) ON DELETE RESTRICT,
  created_at    INTEGER NOT NULL,
  PRIMARY KEY (item_id, bulk_item_id),
  CHECK (item_id <> bulk_item_id)
) STRICT, WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_item_repack_sources_bulk ON item_repack_sources(bulk_item_id);

-- One level: a repack item is batched, has a content, is no child, parent or
-- bulk; a bulk item is batched, no child and no repack item.
CREATE TRIGGER IF NOT EXISTS trg_item_repack_sources_shape
BEFORE INSERT ON item_repack_sources
WHEN NOT EXISTS (SELECT 1 FROM items i, items b
                 WHERE i.id = NEW.item_id AND b.id = NEW.bulk_item_id
                   AND i.parent_item_id IS NULL AND i.uses_batches = 1 AND i.content_qty_base IS NOT NULL
                   AND b.parent_item_id IS NULL AND b.uses_batches = 1)
  OR EXISTS (SELECT 1 FROM item_repack_sources s
             WHERE s.item_id = NEW.bulk_item_id OR s.bulk_item_id = NEW.item_id)
  OR EXISTS (SELECT 1 FROM items c WHERE c.parent_item_id = NEW.item_id)
  OR EXISTS (SELECT 1 FROM item_repack_sources s JOIN items b2 ON b2.id = s.bulk_item_id
             WHERE s.item_id = NEW.item_id
               AND b2.base_unit IS NOT (SELECT base_unit FROM items WHERE id = NEW.bulk_item_id))
BEGIN
  SELECT RAISE(ABORT, 'repack_source_invalid');
END;

CREATE TRIGGER IF NOT EXISTS trg_item_repack_sources_frozen
BEFORE UPDATE ON item_repack_sources
BEGIN
  SELECT RAISE(ABORT, 'repack_source_immutable');
END;

-- The child side of the same rule, on the item row.
CREATE TRIGGER IF NOT EXISTS trg_items_stock_link_ins
BEFORE INSERT ON items
WHEN NEW.parent_item_id IS NOT NULL AND (
       NEW.content_qty_base IS NULL OR NEW.uses_batches = 1 OR NEW.parent_item_id = NEW.id
    OR NOT EXISTS (SELECT 1 FROM items p
                   WHERE p.id = NEW.parent_item_id AND p.parent_item_id IS NULL AND p.uses_batches = 1
                     AND NOT EXISTS (SELECT 1 FROM item_repack_sources s WHERE s.item_id = p.id)))
BEGIN
  SELECT RAISE(ABORT, 'item_stock_link_invalid');
END;

CREATE TRIGGER IF NOT EXISTS trg_items_stock_link_upd
BEFORE UPDATE OF parent_item_id, content_qty_base, uses_batches ON items
WHEN (NEW.parent_item_id IS NOT NULL AND (
        NEW.content_qty_base IS NULL OR NEW.uses_batches = 1 OR NEW.parent_item_id = NEW.id
     OR NOT EXISTS (SELECT 1 FROM items p
                    WHERE p.id = NEW.parent_item_id AND p.parent_item_id IS NULL AND p.uses_batches = 1
                      AND NOT EXISTS (SELECT 1 FROM item_repack_sources s WHERE s.item_id = p.id))
     OR EXISTS (SELECT 1 FROM items c WHERE c.parent_item_id = NEW.id)
     OR EXISTS (SELECT 1 FROM item_repack_sources s WHERE s.item_id = NEW.id OR s.bulk_item_id = NEW.id)))
  OR (NEW.uses_batches = 0 AND (
        EXISTS (SELECT 1 FROM items c WHERE c.parent_item_id = NEW.id)
     OR EXISTS (SELECT 1 FROM item_repack_sources s WHERE s.item_id = NEW.id OR s.bulk_item_id = NEW.id)))
  OR (NEW.content_qty_base IS NULL AND EXISTS (SELECT 1 FROM item_repack_sources s WHERE s.item_id = NEW.id))
BEGIN
  SELECT RAISE(ABORT, 'item_stock_link_invalid');
END;

-- A child line copies where its stock came from (D29). Plain TEXT like
-- batch_id: an unknown parent batch is flagged by the Worker, never rejected.
-- batch_id stays NULL on a child line: the child is priced from its own
-- item-level prices, so the D26 price check must not look in the parent batch.
ALTER TABLE bill_lines ADD COLUMN stock_item_id TEXT;
ALTER TABLE bill_lines ADD COLUMN stock_batch_id TEXT;
ALTER TABLE bill_lines ADD COLUMN stock_qty_base INTEGER
  CHECK ((stock_qty_base IS NULL) = (stock_item_id IS NULL) AND (stock_qty_base IS NULL OR stock_qty_base > 0));

-- The sale movement of a child line moves the parent's stock, exactly as the
-- line recorded it (lines are inserted before movements, D12).
CREATE TRIGGER IF NOT EXISTS trg_movements_child_line
BEFORE INSERT ON inventory_movements
WHEN NEW.reason = 'sale' AND NEW.bill_id IS NOT NULL AND EXISTS (
  SELECT 1 FROM bill_lines l
  WHERE l.bill_id = NEW.bill_id AND l.line_no = NEW.bill_line_no AND l.stock_item_id IS NOT NULL
    AND (NEW.item_id IS NOT l.stock_item_id OR NEW.qty_base IS NOT l.stock_qty_base
         OR NEW.batch_id IS NOT l.stock_batch_id))
BEGIN
  SELECT RAISE(ABORT, 'child_movement_mismatch');
END;

-- A repack entry (D30), entered in admin like a purchase entry. kind 'pack'
-- takes stock out of one bulk batch and makes one new batch per line in book_id;
-- 'unpack' (GoFrugal ReUse) puts packs from book_id back into the bulk batch
-- they were packed from. loss_qty_base leaves the bulk batch either way
-- (spillage, the 100 g left over from 1 kg in 300 g packs). Never edited:
-- a mistake is undone by an unpack or an adjustment.
CREATE TABLE IF NOT EXISTS repacks (
  id             TEXT PRIMARY KEY,
  store_id       TEXT NOT NULL REFERENCES stores(id) ON DELETE RESTRICT,
  book_id        TEXT NOT NULL REFERENCES books(id) ON DELETE RESTRICT,
  kind           TEXT NOT NULL CHECK (kind IN ('pack', 'unpack')),
  bulk_batch_id  TEXT NOT NULL REFERENCES item_batches(id) ON DELETE RESTRICT,
  loss_qty_base  INTEGER NOT NULL DEFAULT 0 CHECK (loss_qty_base >= 0),
  reference      TEXT,
  packed_at      INTEGER NOT NULL,
  created_by     TEXT REFERENCES users(id) ON DELETE RESTRICT,
  created_at     INTEGER NOT NULL,
  note           TEXT
) STRICT;

CREATE INDEX IF NOT EXISTS idx_repacks_store ON repacks(store_id, packed_at);
CREATE INDEX IF NOT EXISTS idx_repacks_bulk ON repacks(bulk_batch_id);

CREATE TABLE IF NOT EXISTS repack_lines (
  repack_id         TEXT NOT NULL REFERENCES repacks(id) ON DELETE RESTRICT,
  line_no           INTEGER NOT NULL CHECK (line_no > 0),
  item_id           TEXT NOT NULL REFERENCES items(id) ON DELETE RESTRICT,         -- the repack item
  batch_id          TEXT NOT NULL REFERENCES item_batches(id) ON DELETE RESTRICT,  -- pack: the new batch
  qty_base          INTEGER NOT NULL CHECK (qty_base > 0),                         -- packs, in its base unit
  content_qty_base  INTEGER NOT NULL CHECK (content_qty_base > 0),                 -- copied at entry
  PRIMARY KEY (repack_id, line_no)
) STRICT, WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_repack_lines_batch ON repack_lines(batch_id);

-- The batch a pack line made; NULL for batches from a purchase entry.
ALTER TABLE item_batches ADD COLUMN repack_id TEXT REFERENCES repacks(id) ON DELETE RESTRICT;
ALTER TABLE inventory_movements ADD COLUMN repack_id TEXT REFERENCES repacks(id) ON DELETE RESTRICT;

CREATE TRIGGER IF NOT EXISTS trg_item_batches_repack
BEFORE INSERT ON item_batches
WHEN NEW.repack_id IS NOT NULL AND NOT EXISTS (
  SELECT 1 FROM repacks r
  WHERE r.id = NEW.repack_id AND r.kind = 'pack' AND r.store_id = NEW.store_id AND r.book_id = NEW.book_id)
BEGIN
  SELECT RAISE(ABORT, 'repack_batch_scope');
END;

CREATE TRIGGER IF NOT EXISTS trg_item_batches_repack_frozen
BEFORE UPDATE OF repack_id ON item_batches
WHEN NEW.repack_id IS NOT OLD.repack_id
BEGIN
  SELECT RAISE(ABORT, 'batch_version_not_bumped');
END;

CREATE TRIGGER IF NOT EXISTS trg_repacks_scope
BEFORE INSERT ON repacks
WHEN NOT EXISTS (SELECT 1 FROM item_batches b
                 WHERE b.id = NEW.bulk_batch_id AND b.store_id = NEW.store_id
                   AND EXISTS (SELECT 1 FROM item_repack_sources s WHERE s.bulk_item_id = b.item_id))
  OR NOT EXISTS (SELECT 1 FROM books k WHERE k.id = NEW.book_id AND k.store_id = NEW.store_id)
BEGIN
  SELECT RAISE(ABORT, 'repack_scope_invalid');
END;

-- A pack line: the item is mapped to the bulk item, the content is the item's,
-- the batch is new (made by this repack, in its book) and carries an MRP. An
-- unpack line: a batch packed from this same bulk batch, at the content it was packed at.
CREATE TRIGGER IF NOT EXISTS trg_repack_lines_valid
BEFORE INSERT ON repack_lines
WHEN NOT EXISTS (
  SELECT 1 FROM repacks r
  JOIN item_batches bb ON bb.id = r.bulk_batch_id
  JOIN item_repack_sources s ON s.item_id = NEW.item_id AND s.bulk_item_id = bb.item_id
  JOIN item_batches lb ON lb.id = NEW.batch_id AND lb.item_id = NEW.item_id
                      AND lb.store_id = r.store_id AND lb.book_id = r.book_id
  WHERE r.id = NEW.repack_id
    AND ((r.kind = 'pack' AND lb.repack_id = r.id AND lb.mrp_minor IS NOT NULL
          AND NEW.content_qty_base = (SELECT content_qty_base FROM items WHERE id = NEW.item_id)
          AND NOT EXISTS (SELECT 1 FROM repack_lines x WHERE x.batch_id = NEW.batch_id))
      OR (r.kind = 'unpack'
          AND (SELECT bulk_batch_id FROM repacks WHERE id = lb.repack_id) = r.bulk_batch_id
          AND NEW.content_qty_base = (SELECT x.content_qty_base FROM repack_lines x
                                      WHERE x.repack_id = lb.repack_id AND x.batch_id = lb.id))))
BEGIN
  SELECT RAISE(ABORT, 'repack_line_invalid');
END;

-- The ledger is written by the DDL, so a repack can never move one side only:
-- per line, packs x content out of the bulk batch and packs into the pack batch
-- (reversed for an unpack); the loss once, out of the bulk batch.
CREATE TRIGGER IF NOT EXISTS trg_repack_lines_move
AFTER INSERT ON repack_lines
BEGIN
  INSERT INTO inventory_movements (id, item_id, direction, qty_base, occurred_at, created_at, batch_id,
                                   store_id, reason, created_by, repack_id)
  SELECT NEW.repack_id || ':' || NEW.line_no || ':bulk', bb.item_id,
         CASE r.kind WHEN 'pack' THEN 'out' ELSE 'in' END, NEW.qty_base * NEW.content_qty_base,
         r.packed_at, r.created_at, bb.id, r.store_id, 'repack', r.created_by, r.id
  FROM repacks r JOIN item_batches bb ON bb.id = r.bulk_batch_id WHERE r.id = NEW.repack_id
  UNION ALL
  SELECT NEW.repack_id || ':' || NEW.line_no || ':pack', NEW.item_id,
         CASE r.kind WHEN 'pack' THEN 'in' ELSE 'out' END, NEW.qty_base,
         r.packed_at, r.created_at, NEW.batch_id, r.store_id, 'repack', r.created_by, r.id
  FROM repacks r WHERE r.id = NEW.repack_id;
END;

CREATE TRIGGER IF NOT EXISTS trg_repacks_loss
AFTER INSERT ON repacks
WHEN NEW.loss_qty_base > 0
BEGIN
  INSERT INTO inventory_movements (id, item_id, direction, qty_base, occurred_at, created_at, batch_id,
                                   store_id, reason, created_by, repack_id)
  SELECT NEW.id || ':loss', b.item_id, 'out', NEW.loss_qty_base, NEW.packed_at, NEW.created_at, b.id,
         NEW.store_id, 'repack', NEW.created_by, NEW.id
  FROM item_batches b WHERE b.id = NEW.bulk_batch_id;
END;

CREATE TRIGGER IF NOT EXISTS trg_repacks_frozen
BEFORE UPDATE ON repacks
BEGIN
  SELECT RAISE(ABORT, 'repack_immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_repacks_no_delete
BEFORE DELETE ON repacks
BEGIN
  SELECT RAISE(ABORT, 'repack_immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_repack_lines_frozen
BEFORE UPDATE ON repack_lines
BEGIN
  SELECT RAISE(ABORT, 'repack_immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_repack_lines_no_delete
BEFORE DELETE ON repack_lines
BEGIN
  SELECT RAISE(ABORT, 'repack_immutable');
END;

UPDATE schema_meta SET schema_version = 8 WHERE id = 1;
