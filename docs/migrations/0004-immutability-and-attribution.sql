-- Migration 0004 — immutability, attribution and price basis.
-- Additive only; triggers are the enforcement because SQLite cannot add table
-- constraints by ALTER.

-- ---------------------------------------------------------------------------
-- Attribution: a password shift start is only trustworthy if the server minted
-- something the bill can reference. Without these columns `auth_method` is a
-- claim with nothing behind it.
-- ---------------------------------------------------------------------------
ALTER TABLE bills ADD COLUMN staff_assertion_id TEXT;
ALTER TABLE bills ADD COLUMN unverified_staff INTEGER NOT NULL DEFAULT 0
  CHECK (unverified_staff IN (0, 1));

CREATE TRIGGER IF NOT EXISTS trg_bills_assertion_required
BEFORE INSERT ON bills
WHEN NEW.auth_method = 'password' AND NEW.staff_assertion_id IS NULL
BEGIN
  SELECT RAISE(ABORT, 'password_bill_needs_assertion');
END;

-- ---------------------------------------------------------------------------
-- Price basis: `price_minor` is the rate per `price_basis_qty_base` base units.
-- Without it, "4000" is either 40/kg or 40/g and nothing on the line says which.
-- ---------------------------------------------------------------------------
ALTER TABLE bill_lines ADD COLUMN price_basis_qty_base INTEGER NOT NULL DEFAULT 1
  CHECK (price_basis_qty_base > 0);

-- ---------------------------------------------------------------------------
-- Immutability. A bill's money never changes after insert; only `invoice_no`
-- may be assigned once, and only a void may flip `voided`.
-- ---------------------------------------------------------------------------
CREATE TRIGGER IF NOT EXISTS trg_bills_money_frozen
BEFORE UPDATE OF total_minor, subtotal_minor, tax_minor, rounding_minor,
                 business_date, created_at, user_id, store_id, terminal_id, local_seq
ON bills
BEGIN
  SELECT RAISE(ABORT, 'bill_immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_bills_invoice_no_once
BEFORE UPDATE OF invoice_no ON bills
WHEN OLD.invoice_no IS NOT NULL AND NEW.invoice_no IS NOT OLD.invoice_no
BEGIN
  SELECT RAISE(ABORT, 'invoice_no_already_assigned');
END;

CREATE TRIGGER IF NOT EXISTS trg_bills_void_needs_actor
BEFORE UPDATE OF voided ON bills
WHEN NEW.voided = 1 AND (NEW.voided_by IS NULL OR NEW.voided_at IS NULL)
BEGIN
  SELECT RAISE(ABORT, 'void_needs_actor');
END;

CREATE TRIGGER IF NOT EXISTS trg_lines_frozen
BEFORE UPDATE ON bill_lines
BEGIN
  SELECT RAISE(ABORT, 'bill_line_immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_lines_frozen_delete
BEFORE DELETE ON bill_lines
WHEN EXISTS (SELECT 1 FROM bills WHERE id = OLD.bill_id)
BEGIN
  SELECT RAISE(ABORT, 'bill_line_immutable');
END;

-- ---------------------------------------------------------------------------
-- A void must name its actor and a reason, and the bill must exist.
-- ---------------------------------------------------------------------------
CREATE TRIGGER IF NOT EXISTS trg_bill_voids_actor
BEFORE INSERT ON bill_voids
WHEN NEW.user_id IS NULL OR length(trim(NEW.reason)) = 0
BEGIN
  SELECT RAISE(ABORT, 'void_needs_actor_and_reason');
END;

-- ---------------------------------------------------------------------------
-- Override listing must not read voided bills. Index the predicate it filters on.
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_bill_lines_override_live
  ON bill_lines(bill_id, price_source)
  WHERE price_source IN ('override', 'manual');

UPDATE schema_meta SET schema_version = 4 WHERE id = 1;
