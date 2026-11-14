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

UPDATE schema_meta SET schema_version = 8 WHERE id = 1;
