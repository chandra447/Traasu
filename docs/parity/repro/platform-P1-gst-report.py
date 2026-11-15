#!/usr/bin/env python3
"""Platform slice probe P1: can GSTR-1 style output be produced from bill_lines as designed?

Runs the real DDL (docs/d1-schema.sql + migrations 0002..0008) in memory, exactly as
docs/check-invariants.py does, then tries to answer:
  A. rate-wise B2C(S) summary          (GSTR-1 table 7 / B2C small)
  B. HSN-wise summary                  (GSTR-1 table 12) incl. UQC + quantity
  C. supplier GSTIN per row            (GSTR-1 identity block)
  D. the same summaries for an archived month
  E. cancelled-invoice register         (GSTR-1 table 13)
"""
import pathlib, sqlite3, sys

DOCS = pathlib.Path(__file__).resolve().parents[2]
con = sqlite3.connect(":memory:", isolation_level=None)
con.execute("PRAGMA foreign_keys = ON")
con.executescript((DOCS / "d1-schema.sql").read_text())
for m in sorted((DOCS / "migrations").glob("*.sql")):
    con.executescript(m.read_text())


def rule(t):
    print("\n" + "=" * 78 + "\n" + t + "\n" + "=" * 78)


def run(sql, *a):
    return con.execute(sql, a).fetchall()


def cols(table):
    return [r[1] for r in run(f"PRAGMA table_info({table})")]


con.executescript("""
INSERT INTO stores (id, name, created_at) VALUES ('s1', 'Kukatpally', 0), ('s2', 'Secunderabad', 0);
INSERT INTO terminals (id, store_id, name, created_at) VALUES ('t1', 's1', 'Counter 1', 0), ('t2','s2','C1',0);
INSERT INTO users (id, name, role, created_at) VALUES ('u1', 'Cashier', 'cashier', 0);
INSERT INTO staff_sessions (id, user_id, store_id, terminal_id, auth_method, started_at)
VALUES ('ss1', 'u1', 's1', 't1', 'pin_offline', 1000), ('ss2','u1','s2','t2','pin_offline',1000);
-- tax rates as seeded: 5% = ...0002, 12% = ...0003, 18% = ...0004
INSERT INTO items (id, name, dimension, pricing_mode, base_unit, default_sales_unit, tax_rate_id, hsn, updated_at)
VALUES ('i_dal', 'Toor Dal',   'mass', 'fixed_only', 'g',  'kg', '0192f3c0-0000-7000-8100-000000000002', '0713', 0),
       ('i_oil', 'Sunflower Oil','volume','fixed_only','ml','l',  '0192f3c0-0000-7000-8100-000000000002', '1512', 0),
       ('i_soap','Bath Soap',  'count','fixed_only','pcs','pcs','0192f3c0-0000-7000-8100-000000000004', '3401', 0),
       ('i_nohsn','Loose Atta','mass', 'fixed_only', 'g',  'kg', '0192f3c0-0000-7000-8100-000000000001', NULL,   0);
""")

seq = [0]


def push(bill, store, term, sess, lines, date, created=1500):
    """Exactly what the Worker batches: lines, then header, one transaction."""
    seq[0] += 1
    sub = sum(l["taxable"] for l in lines)
    tax = sum(l["cgst"] + l["sgst"] + l["cess"] + l["igst"] for l in lines)
    tot = sum(l["total"] for l in lines)
    con.execute("BEGIN")
    con.execute("PRAGMA defer_foreign_keys = on")
    for n, l in enumerate(lines, 1):
        con.execute(
            """INSERT INTO bill_lines (bill_id, line_no, item_id, item_name, dimension, qty_base, price_version,
                 price_source, list_price_minor, price_minor, price_basis_qty_base, discount_minor,
                 list_amount_minor, price_breakdown, line_total_minor, taxable_minor, tax_rate_bp,
                 cgst_minor, sgst_minor, cess_minor, igst_minor, hsn)
               VALUES (?,?,?,'x','mass',?,1,'list',?,?,1000,0,?,'{}',?,?,?,?,?,?,?,?)""",
            (bill, n, l["item"], l["qty"], l["list"], l["list"], l["list"],
             l["total"], l["taxable"], l["rate"], l["cgst"], l["sgst"], l["cess"], l["igst"], l["hsn"]))
    con.execute(
        """INSERT INTO bills (id, store_id, terminal_id, user_id, auth_method, business_date, local_seq,
             display_no, total_minor, subtotal_minor, tax_minor, rounding_minor, created_at, staff_session_id)
           VALUES (?,?,?,'u1','pin_offline',?,?,?,?,?,?,0,?,?)""",
        (bill, store, term, date, seq[0], f"D{seq[0]}", tot, sub, tax, created, sess))
    con.execute("COMMIT")


def L(item, qty, list_, total, taxable, rate, cgst, sgst, igst, cess, hsn):
    return dict(item=item, qty=qty, list=list_, total=total, taxable=taxable, rate=rate,
                cgst=cgst, sgst=sgst, igst=igst, cess=cess, hsn=hsn)


# --- one trading day, Kukatpally (intra-state: CGST+SGST) and Secunderabad (an inter-state
# --- counter, IGST), plus a bill with no HSN, plus a voided bill.
push("b1", "s1", "t1", "ss1", [
    L("i_dal", 1000, 16000, 16000, 15238, 500, 381, 381, 0, 0, "0713"),
    L("i_soap", 3, 13500, 13500, 11441, 1800, 1029, 1030, 0, 0, "3401"),
], "2026-11-14")
push("b2", "s1", "t1", "ss1", [
    L("i_oil", 1000, 14000, 14000, 13333, 500, 333, 334, 0, 0, "1512"),
    L("i_nohsn", 2000, 9000, 9000, 9000, 0, 0, 0, 0, 0, None),
], "2026-11-12")
push("b3", "s2", "t2", "ss2", [
    L("i_soap", 10, 45000, 45000, 45000, 0, 0, 0, 0, 0, "3401"),      # inter-state, 0-rated slab here
    L("i_dal", 5000, 80000, 80000, 67797, 1200, 0, 0, 12203, 0, "0713"),
], "2026-11-14")
push("b4", "s1", "t1", "ss1", [
    L("i_soap", 100, 450000, 450000, 381356, 1800, 34322, 34322, 0, 0, "3401"),
], "2026-11-13")
# b4 is voided the same evening
run("""INSERT INTO bill_voids (id, bill_id, store_id, terminal_id, user_id, reason, created_at)
       VALUES ('v1','b4','s1','t1','u1','customer changed mind', 1700)""")

# ---------------------------------------------------------------- A
rule("A. rate-wise summary (GSTR-1 table 7, B2C small) — from bill_lines")
qA = """
SELECT bl.tax_rate_bp AS rate_bp,
       SUM(bl.taxable_minor) AS taxable,
       SUM(bl.cgst_minor)    AS cgst,
       SUM(bl.sgst_minor)    AS sgst,
       SUM(bl.igst_minor)    AS igst,
       SUM(bl.cess_minor)    AS cess,
       SUM(bl.line_total_minor) AS invoice_value,
       COUNT(DISTINCT b.id)  AS invoices
FROM bill_lines bl JOIN bills b ON b.id = bl.bill_id
WHERE b.store_id = 's1' AND b.business_date BETWEEN '2026-11-13' AND '2026-11-14' AND b.voided = 0
GROUP BY bl.tax_rate_bp
ORDER BY bl.tax_rate_bp"""
print("store s1 (intra-state), 2026-11-13..14, voided excluded")
print(f"{'rate':>6} {'taxable':>9} {'cgst':>7} {'sgst':>7} {'igst':>6} {'cess':>5} {'value':>9} {'invs':>5}")
for r in run(qA):
    print(f"{r[0]/100:>6.0f} {r[1]:>9} {r[2]:>7} {r[3]:>7} {r[4]:>6} {r[5]:>5} {r[6]:>9} {r[7]:>5}")
print("VERDICT A: derivable from bill_lines. b4 (voided) is correctly excluded.")

# ---------------------------------------------------------------- B
rule("B. HSN-wise summary (GSTR-1 table 12) — needs UQC + total quantity")
print("bill_lines columns:", ", ".join(cols("bill_lines")))
print()
print("items.base_unit is the only unit; 'units' carries unit_group only:")
print("units rows for the groups in play:", run(
    "SELECT unit, unit_group FROM units WHERE unit IN ('g','ml','pcs')"))
qB = """
SELECT IFNULL(bl.hsn,'(none)') AS hsn, bl.tax_rate_bp,
       SUM(bl.qty_base) AS qty, SUM(bl.taxable_minor) AS taxable,
       SUM(bl.cgst_minor+bl.sgst_minor+bl.igst_minor+bl.cess_minor) AS tax,
       COUNT(*) AS lines
FROM bill_lines bl JOIN bills b ON b.id = bl.bill_id
WHERE b.store_id='s1' AND b.business_date BETWEEN '2026-11-13' AND '2026-11-14' AND b.voided=0
GROUP BY IFNULL(bl.hsn,'(none)'), bl.tax_rate_bp ORDER BY hsn, bl.tax_rate_bp"""
print()
print("HSN block with qty in BASE UNITS (g / ml / pcs), which is not a GST UQC:")
for r in run(qB):
    print(f"  hsn={r[0]:<8} rate={r[1]/100:>5.0f}% qty={r[2]:>7} (base)  taxable={r[3]:>7} tax={r[4]:>6} lines={r[5]}")
print()
print("Same block re-derived through items.base_unit (the only way to name a unit):")
qB2 = """
SELECT IFNULL(bl.hsn,'(none)'), i.base_unit, SUM(bl.qty_base)
FROM bill_lines bl JOIN bills b ON b.id=bl.bill_id JOIN items i ON i.id=bl.item_id
WHERE b.store_id='s1' AND b.business_date BETWEEN '2026-11-13' AND '2026-11-14' AND b.voided=0
GROUP BY 1,2 ORDER BY 1,2"""
for r in run(qB2):
    print(f"  hsn={r[0]:<8} base_unit={r[1]:<4} qty={r[2]}")
print()
print("Now change one item's base_unit the way a PATCH does today (version bumped, DDL allows it):")
print("  ", end="")
try:
    con.execute("UPDATE items SET base_unit='kg', version=2 WHERE id='i_dal'")
    print("accepted: items.base_unit is NOT frozen by the DDL, only by a Worker rule")
except sqlite3.Error as e:
    print("refused:", e)
print("  historical HSN block for i_dal now:", run(
    """SELECT IFNULL(bl.hsn,'(none)'), i.base_unit, SUM(bl.qty_base)
       FROM bill_lines bl JOIN bills b ON b.id=bl.bill_id JOIN items i ON i.id=bl.item_id
       WHERE bl.item_id='i_dal' AND b.voided=0 GROUP BY 1,2"""))
print("VERDICT B: taxable value and tax are derivable; UQC is not stored on the line and")
print("           a later items.base_unit edit silently rewrites the quantity unit of past sales.")

# ---------------------------------------------------------------- C
rule("C. supplier GSTIN / place of supply — GSTR-1 is filed per GSTIN")
print("stores columns:", ", ".join(cols("stores")))
print("any gstin/state/address column anywhere in the schema:",
      [r for r in con.execute("""
        SELECT m.name || '.' || p.name FROM sqlite_master m
        JOIN pragma_table_info(m.name) p
        WHERE m.type='table' AND (p.name LIKE '%gstin%' OR p.name LIKE '%state%'
                                  OR p.name LIKE '%address%' OR p.name LIKE '%pin%')""")])
print("VERDICT C: no supplier GSTIN, no place of supply, no buyer identity. A GSTR-1 header")
print("           cannot be produced, and a per-GSTIN split cannot be expressed at all.")

# ---------------------------------------------------------------- D
rule("D. an archived month: what survives in D1")
print("sales_daily columns:", ", ".join(cols("sales_daily")))
print("before archive, s1 2026-11-12 totals:",
      run("SELECT SUM(taxable_minor), SUM(cgst_minor+sgst_minor+igst_minor+cess_minor) "
          "FROM bill_lines bl JOIN bills b ON b.id=bl.bill_id WHERE b.business_date='2026-11-12'"))
con.execute("UPDATE schema_meta SET archive_before='2026-11-13' WHERE id=1")
print("archive_before set to 2026-11-13 (bills strictly before that date may be deleted)")
for bid in [r[0] for r in run("SELECT id FROM bills WHERE business_date < '2026-11-13'")]:
    con.execute("DELETE FROM bill_voids WHERE bill_id=?", (bid,))
    con.execute("DELETE FROM bills WHERE id=?", (bid,))
print("deleted bills for 2026-11-12:", run("SELECT COUNT(*) FROM bills WHERE business_date='2026-11-12'"),
      "  lines left:", run("SELECT COUNT(*) FROM bill_lines WHERE bill_id NOT IN (SELECT id FROM bills)"))
print("rate-wise query for 2026-11-12 now returns:",
      run("""SELECT SUM(bl.taxable_minor), SUM(bl.cgst_minor+bl.sgst_minor+bl.igst_minor+bl.cess_minor)
             FROM bill_lines bl JOIN bills b ON b.id=bl.bill_id
             WHERE b.business_date='2026-11-12'"""))
print("what sales_daily still holds for 2026-11-12:",
      run("SELECT store_id, item_id, qty_base, amount_minor, bill_count FROM sales_daily WHERE business_date='2026-11-12'"))
print("VERDICT D: for a month older than archive_before the tax components are gone from D1 and")
print("           sales_daily keeps money only — no cgst/sgst/igst/cess, no rate. A regenerated")
print("           GSTR-1 (or an answer to a notice) needs a restore from R2.")

# ---------------------------------------------------------------- E
rule("E. cancelled-invoice register (GSTR-1 table 13) and the voided bill")
print("voided bills still present with invoice_no/display_no:",
      run("SELECT id, invoice_no, display_no, voided FROM bills WHERE voided=1"))
print("the voided bill (2026-11-13) is above archive_before, so it survived:",
      run("SELECT id, business_date, voided FROM bills"))
print("a voided bill inside the archive window is deleted with its void row, so the cancelled-")
print("invoice register (table 13) for that month exists only in R2.")
print("VERDICT E: a voided bill keeps its number and is never deleted by the archive job")
print("           (void records for the month are deleted with it), so table 13 is derivable")
print("           only while the bill row exists.")

# ---------------------------------------------------------------- F
rule("F. unsynced (never-pushed) bill shape, as the terminal writes it")
print("a terminal-local bill has invoice_no =",
      run("SELECT invoice_no FROM bills WHERE id='b1'"), "(server assigns it on push)")
print("uq_bills_invoice_no is partial, so NULL invoice numbers are unlimited:",
      run("SELECT COUNT(*) FROM bills WHERE invoice_no IS NULL"))
print("VERDICT F: the paper the customer walks out with carries display_no; the legal invoice")
print("           number does not exist yet offline. See the note in the report.")
