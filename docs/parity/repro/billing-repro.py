#!/usr/bin/env python3
"""Billing-slice reproduction cases against the real Traasu DDL (d1-schema.sql + 0002..0008).

Each case is a sale or write the design says cannot happen, or a good sale the design refuses.
Run: python3 repro.py   (no repo writes; in-memory sqlite)
"""
import pathlib, sqlite3, sys

DOCS = pathlib.Path(__file__).resolve().parents[2]
con = sqlite3.connect(":memory:", isolation_level=None)
con.execute("PRAGMA foreign_keys = ON")
con.executescript((DOCS / "d1-schema.sql").read_text())
for m in sorted((DOCS / "migrations").glob("*.sql")):
    con.executescript(m.read_text())

# ---------------------------------------------------------------- seed
con.executescript("""
INSERT INTO stores (id, name, created_at) VALUES ('s1','S1',0), ('s2','S2',0);
INSERT INTO store_settings (store_id, business_day_start_minutes, rounding_mode, override_ceiling_pct, updated_at)
  VALUES ('s1', 540, 'nearest_rupee', 10, 0);
INSERT INTO terminals (id, store_id, name, created_at) VALUES ('t1','s1','T1',0), ('t2','s1','T2',0);
INSERT INTO users (id, name, role, created_at) VALUES ('u1','Cashier','cashier',0), ('mgr','Manager','staff',0);
INSERT INTO books (id, store_id, name, priority, created_at, updated_at) VALUES ('k1','s1','Main',1,0,0);
INSERT INTO numbering_policy (id, store_id, scope, terminal_id, prefix, padding, reset_cadence, next_value, updated_at)
  VALUES ('np1','s1','store',NULL,'KUK/2026/',6,'yearly',1,0),
         ('np2','s1','register','t1','S1-T1-',6,'never',1,0);
INSERT INTO items (id, name, dimension, pricing_mode, base_unit, default_sales_unit, tax_rate_id,
                   hsn, price_includes_tax, mrp_minor, allow_discount_pct, updated_at)
  VALUES ('tam','Tamarind','mass','tiered','g','kg',
            '0192f3c0-0000-7000-8100-000000000002','0810',1,5000,0,0),
         ('rice','Loose Rice','mass','linear','g','kg',
            '0192f3c0-0000-7000-8100-000000000002','1006',1,6000,0,0),
         ('soap','Soap','count','fixed_only','pcs','pcs',
            '0192f3c0-0000-7000-8100-000000000004','3401',1,10000,0,0),
         ('excl','Bulk Atta','mass','fixed_only','g','kg',
            '0192f3c0-0000-7000-8100-000000000004','1101',0,4000,0,0);
INSERT INTO item_prices (item_id, store_id, qty_base, version, price_minor, valid_from, created_at)
  VALUES ('tam', NULL, 1000, 1, 4000, 100, 0), ('tam', NULL, 500, 1, 2500, 100, 0);
""")

seq = [0]
failures = []


def write(sql, *args):
    try:
        con.execute(sql, args)
        return "OK"
    except sqlite3.Error as e:
        return str(e)


def q(sql, *args):
    return con.execute(sql, args).fetchone()


def case(n, title):
    print(f"\n=== {n}. {title}")


def result(label, got):
    print(f"    {label}: {got}")


LINE_DEFAULTS = dict(
    item_name="x", dimension="count", qty_base=1, price_version=1, price_source="list",
    list_price_minor=0, price_minor=0, price_basis_qty_base=1, discount_minor=0,
    list_amount_minor=None, price_breakdown="{}", line_total_minor=0, taxable_minor=0,
    tax_rate_bp=500, cgst_minor=0, sgst_minor=0, cess_minor=0, igst_minor=0,
    mrp_minor=None, price_store_id=None, price_includes_tax=1, hsn=None,
    override_by=None, approval_grant_id=None, batch_id=None, batch_no=None,
    item_version=1, stock=None)


def push(bill_id, lines, total=None, subtotal=None, tax=None, rounding=0, created_at=1763107200,
         date="2026-11-14", store="s1", terminal="t1", invoice_no=None, display_no=None,
         local_seq=None, movement_ids=None):
    """One bill exactly as the Worker batches it: deferred FKs, lines, movements, header last."""
    seq[0] += 1
    ls = []
    for i, raw in enumerate(lines, 1):
        d = dict(LINE_DEFAULTS); d.update(raw); d["no"] = i
        if d["list_amount_minor"] is None:
            d["list_amount_minor"] = d["line_total_minor"] + d["discount_minor"]
        ls.append(d)
    total = sum(l["line_total_minor"] for l in ls) + rounding if total is None else total
    subtotal = sum(l["taxable_minor"] for l in ls) if subtotal is None else subtotal
    tax = sum(l["cgst_minor"] + l["sgst_minor"] + l["cess_minor"] + l["igst_minor"] for l in ls) if tax is None else tax
    try:
        con.execute("BEGIN")
        con.execute("PRAGMA defer_foreign_keys = on")
        for l in ls:
            con.execute("""INSERT INTO bill_lines (bill_id, line_no, item_id, item_name, dimension, qty_base,
                price_version, price_source, list_price_minor, price_minor, price_basis_qty_base, discount_minor,
                list_amount_minor, price_breakdown, line_total_minor, taxable_minor, tax_rate_bp, cgst_minor,
                sgst_minor, cess_minor, igst_minor, mrp_minor, price_store_id, price_includes_tax, hsn,
                override_by, approval_grant_id, batch_id, batch_no, item_version,
                stock_item_id, stock_batch_id, stock_qty_base)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT (bill_id, line_no) DO NOTHING""",
                (bill_id, l["no"], l["item"], l["item_name"], l["dimension"], l["qty_base"],
                 l["price_version"], l["price_source"], l["list_price_minor"], l["price_minor"],
                 l["price_basis_qty_base"], l["discount_minor"], l["list_amount_minor"], l["price_breakdown"],
                 l["line_total_minor"], l["taxable_minor"], l["tax_rate_bp"], l["cgst_minor"], l["sgst_minor"],
                 l["cess_minor"], l["igst_minor"], l["mrp_minor"], l["price_store_id"], l["price_includes_tax"],
                 l["hsn"], l["override_by"], l["approval_grant_id"], l["batch_id"], l["batch_no"],
                 l["item_version"], *(l["stock"] or (None, None, None))))
        for mid, (item, dirn, qty, batch) in (movement_ids or {}).items():
            con.execute("""INSERT INTO inventory_movements (id, item_id, bill_id, bill_line_no, direction, qty_base,
                occurred_at, created_at, batch_id, store_id, reason)
              VALUES (?,?,?,?,?,?,?,?,?,?, 'sale')""",
                (mid, item, bill_id, 1, dirn, qty, created_at, created_at, batch, store))
        con.execute("""INSERT INTO bills (id, store_id, terminal_id, user_id, auth_method, business_date,
                local_seq, display_no, invoice_no, total_minor, subtotal_minor, tax_minor, rounding_minor,
                created_at, business_day_start_minutes)
              VALUES (?,?,?,'u1','pin_offline',?,?,?,?,?,?,?,?,?,540)
              ON CONFLICT (id) DO NOTHING""",
            (bill_id, store, terminal, date, local_seq or seq[0], display_no or f"D{seq[0]}",
             invoice_no, total, subtotal, tax, rounding, created_at))
        con.execute("COMMIT")
        return "OK"
    except sqlite3.Error as e:
        con.execute("ROLLBACK")
        return str(e)


# ---------------------------------------------------------------------------
case(1, "Inclusive line: the design's own canonical example is accepted (control)")
r = push("b_ctrl", [dict(item="tam", item_name="Tamarind", dimension="mass", qty_base=1500,
                         list_price_minor=4000, price_minor=4000, price_basis_qty_base=1000,
                         list_amount_minor=6500, line_total_minor=6500, taxable_minor=6190,
                         cgst_minor=155, sgst_minor=155, tax_rate_bp=500, price_includes_tax=1,
                         price_breakdown='{"parts":[{"qty_base":1000,"price_minor":4000},'
                                        '{"qty_base":500,"price_minor":2500}]}')])
result("1.5 kg @ Rs40/1000g, 5% inclusive", r)
result("header", q("SELECT subtotal_minor, tax_minor, rounding_minor, total_minor FROM bills WHERE id='b_ctrl'"))

# ---------------------------------------------------------------------------
case(2, "Exclusive-priced item (items.price_includes_tax = 0): a correct sale cannot be stored")
result("honest exclusive bill: taxable 10000 + 18% = line_total 11800, list_amount 10000",
       push("b_excl", [dict(item="excl", item_name="Atta", dimension="mass", qty_base=1000,
                            list_price_minor=10000, price_minor=10000, price_basis_qty_base=1000,
                            list_amount_minor=10000, line_total_minor=11800, taxable_minor=10000,
                            cgst_minor=900, sgst_minor=900, tax_rate_bp=1800, price_includes_tax=0)]))
result("same with a negative discount to make the identity hold",
       push("b_excl2", [dict(item="excl", item_name="Atta", dimension="mass", qty_base=1000,
                             list_price_minor=10000, price_minor=10000, price_basis_qty_base=1000,
                             list_amount_minor=10000, discount_minor=-1800, line_total_minor=11800,
                             taxable_minor=10000, cgst_minor=900, sgst_minor=900, tax_rate_bp=1800,
                             price_includes_tax=0)]))
result("only storable shape: list_amount secretly redefined as inclusive (11800 != catalog 10000)",
       push("b_excl3", [dict(item="excl", item_name="Atta", dimension="mass", qty_base=1000,
                             list_price_minor=10000, price_minor=10000, price_basis_qty_base=1000,
                             list_amount_minor=11800, line_total_minor=11800, taxable_minor=10000,
                             cgst_minor=900, sgst_minor=900, tax_rate_bp=1800, price_includes_tax=0)]))
result("stored list_amount vs catalog tier price",
       q("SELECT list_amount_minor, list_price_minor * qty_base / price_basis_qty_base AS catalog_amount, "
         "price_includes_tax FROM bill_lines WHERE bill_id='b_excl3'"))

# ---------------------------------------------------------------------------
case(3, "MRP is a legal ceiling in D27 but no rule, trigger or Worker check enforces it")
result("batch at MRP 100, selling price 200 set in the catalog",
       write("INSERT INTO item_batches (id,item_id,store_id,book_id,batch_no,mrp_minor,received_at,updated_at) "
             "VALUES ('bs1','soap','s1','k1','SOAP-1',10000,100,0)"))
result("batched item price above the batch MRP",
       write("INSERT INTO item_prices (item_id,store_id,batch_id,qty_base,version,price_minor,mrp_minor,"
             "valid_from,created_at) VALUES ('soap','s1','bs1',1,1,20000,12000,100,0)"))
result("bill line at Rs200 with the line's own mrp_minor = Rs100",
       push("b_mrp", [dict(item="soap", item_name="Soap", dimension="count", qty_base=1,
                           list_price_minor=20000, price_minor=20000, list_amount_minor=20000,
                           line_total_minor=20000, taxable_minor=16949, cgst_minor=1525, sgst_minor=1526,
                           tax_rate_bp=1800, mrp_minor=10000, batch_id="bs1", batch_no="SOAP-1")]))
result("stored line", q("SELECT price_minor, mrp_minor, price_minor > mrp_minor AS above_mrp "
                        "FROM bill_lines WHERE bill_id='b_mrp'"))
result("the same bill with mrp_minor NULL (no MRP claimed at all)",
       push("b_mrp2", [dict(item="soap", item_name="Soap", dimension="count", qty_base=1,
                            list_price_minor=20000, price_minor=20000, list_amount_minor=20000,
                            line_total_minor=20000, taxable_minor=16949, cgst_minor=1525, sgst_minor=1526,
                            tax_rate_bp=1800)]))
result("three MRP sources disagree and nothing reconciles them",
       q("SELECT (SELECT mrp_minor FROM items WHERE id='soap') AS item_mrp, "
         "(SELECT mrp_minor FROM item_batches WHERE id='bs1') AS batch_mrp, "
         "(SELECT mrp_minor FROM item_prices WHERE batch_id='bs1') AS price_mrp"))

# ---------------------------------------------------------------------------
case(4, "A discount recorded as a list sale: no override_by, invisible to the override report")
result("line: price_source 'list', discount Rs50, override_by NULL",
       push("b_disc", [dict(item="tam", item_name="Tamarind", dimension="mass", qty_base=1000,
                            list_price_minor=4000, price_minor=4000, price_basis_qty_base=1000,
                            list_amount_minor=4500, discount_minor=500, line_total_minor=4000,
                            taxable_minor=3810, cgst_minor=95, sgst_minor=95, tax_rate_bp=500)]))
result("override report predicate (price_source IN override/manual) sees it?",
       q("SELECT COUNT(*) FROM bill_lines WHERE bill_id='b_disc' AND price_source IN ('override','manual')"))
result("lines carrying a discount with no approver",
       q("SELECT COUNT(*) FROM bill_lines WHERE discount_minor > 0 AND override_by IS NULL"))

case(4.1, "A low list_amount hides the discount and defeats any ceiling computed from it")
r = push("b_low", [dict(item="tam", item_name="Tamarind", dimension="mass", qty_base=1500,
                        list_price_minor=4000, price_minor=4000, price_basis_qty_base=1000,
                        list_amount_minor=100, line_total_minor=100, taxable_minor=95,
                        cgst_minor=2, sgst_minor=3, tax_rate_bp=500)])
result("Rs 1 charged for Rs 65 of tamarind, claimed as a list sale", r)
result("stored line: rate and amount cannot both be true, and discount reads 0",
       q("SELECT list_price_minor, price_basis_qty_base, qty_base, list_amount_minor, discount_minor, "
         "price_mismatch FROM bill_lines WHERE bill_id='b_low'"))
result("what the catalog says that version costs",
       q("SELECT SUM(price_minor) FROM item_prices WHERE item_id='tam' AND version=1 AND valid_from <= 1763107200 "
         "AND (valid_to IS NULL OR valid_to > 1763107200)"))

# ---------------------------------------------------------------------------
case(5, "The stored tax is never tied to the stored tax_rate_bp")
result("18% line (tax_rate_bp 1800) with zero tax and no tax in the total",
       push("b_tax0", [dict(item="soap", item_name="Soap", dimension="count", qty_base=1,
                            list_price_minor=10000, price_minor=10000, list_amount_minor=10000,
                            line_total_minor=10000, taxable_minor=10000, tax_rate_bp=1800)]))
result("5% line (tax_rate_bp 500) carrying 18% tax",
       push("b_tax18", [dict(item="tam", item_name="Tamarind", dimension="mass", qty_base=1000,
                             list_price_minor=10000, price_minor=10000, price_basis_qty_base=1000,
                             list_amount_minor=11800, line_total_minor=11800, taxable_minor=10000,
                             cgst_minor=900, sgst_minor=900, tax_rate_bp=500)]))
for row in con.execute("SELECT bill_id, tax_rate_bp, taxable_minor, cgst_minor+sgst_minor AS tax "
                       "FROM bill_lines WHERE bill_id IN ('b_tax0','b_tax18') ORDER BY bill_id"):
    result(f"  stored {row[0]}: rate {row[1]}bp on taxable {row[2]} carries tax {row[3]}", "ACCEPTED")

# ---------------------------------------------------------------------------
case(6, "Offers: a free item and an apportioned bill-level discount fit; an unapportioned one does not")
result("free line (100% discount) at an 18% rate",
       push("b_free", [dict(item="soap", item_name="Soap", dimension="count", qty_base=1,
                            price_source="manual", override_by="mgr", override_reason="other",
                            list_price_minor=10000, price_minor=10000, list_amount_minor=10000,
                            discount_minor=10000, line_total_minor=0, taxable_minor=0,
                            cgst_minor=0, sgst_minor=0, tax_rate_bp=1800)]))
result("bill total for the free-item bill", q("SELECT total_minor FROM bills WHERE id='b_free'"))
result("bill-level Rs50 off apportioned to the discounted line",
       push("b_apport", [
           dict(item="tam", item_name="Tamarind", dimension="mass", qty_base=1500, list_price_minor=4000,
                price_minor=4000, price_basis_qty_base=1000, list_amount_minor=6500, discount_minor=500,
                line_total_minor=6000, taxable_minor=5714, cgst_minor=143, sgst_minor=143, tax_rate_bp=500),
           dict(item="soap", item_name="Soap", dimension="count", qty_base=1, list_price_minor=3000,
                price_minor=3000, list_amount_minor=3000, line_total_minor=3000, taxable_minor=2857,
                cgst_minor=71, sgst_minor=72, tax_rate_bp=500)]))
result("an unapportioned Rs50 bill discount parked in rounding_minor",
       push("b_unapport", [dict(item="soap", item_name="Soap", dimension="count", qty_base=1,
                                list_price_minor=5000, price_minor=5000, list_amount_minor=5000,
                                line_total_minor=5000, taxable_minor=4762, cgst_minor=119, sgst_minor=119,
                                tax_rate_bp=500),
                           dict(item="soap", item_name="Soap", dimension="count", qty_base=1,
                                list_price_minor=5000, price_minor=5000, list_amount_minor=5000,
                                line_total_minor=5000, taxable_minor=4762, cgst_minor=119, sgst_minor=119,
                                tax_rate_bp=500)], rounding=-5000))
result("a discount line as a negative-quantity line",
       push("b_negline", [dict(item="soap", item_name="Soap", dimension="count", qty_base=-1,
                               list_price_minor=3000, price_minor=3000, list_amount_minor=3000,
                               line_total_minor=3000, taxable_minor=2857, cgst_minor=71, sgst_minor=72,
                               tax_rate_bp=500)]))

# ---------------------------------------------------------------------------
case(7, "Replay: lines and the rollup are idempotent, the stock ledger is not")
result("stock in 10 soaps", write(
    "INSERT INTO inventory_movements (id,item_id,direction,qty_base,occurred_at,created_at,batch_id,store_id,reason) "
    "VALUES ('rcv','soap','in',10,100,100,'bs1','s1','receipt')"))
r1 = push("b_rep", [dict(item="soap", item_name="Soap", dimension="count", qty_base=1, list_price_minor=10000,
                         price_minor=10000, list_amount_minor=10000, line_total_minor=10000,
                         taxable_minor=8475, cgst_minor=762, sgst_minor=763, tax_rate_bp=1800,
                         batch_id="bs1", batch_no="SOAP-1")],
          movement_ids={"mv1": ("soap", "out", 1, "bs1")})
result("first push", r1)
result("on hand after one sale", q("SELECT on_hand_qty_base FROM batch_stock WHERE batch_id='bs1'"))
result("rollup after the sale", q("SELECT qty_base, amount_minor, bill_count FROM sales_daily "
                                  "WHERE store_id='s1' AND item_id='soap' AND business_date='2026-11-14'"))
result("replay of the same bill with a fresh movement id (a retry after a lost response)",
       push("b_rep", [dict(item="soap", item_name="Soap", dimension="count", qty_base=1, list_price_minor=10000,
                           price_minor=10000, list_amount_minor=10000, line_total_minor=10000,
                           taxable_minor=8475, cgst_minor=762, sgst_minor=763, tax_rate_bp=1800,
                           batch_id="bs1", batch_no="SOAP-1")],
            movement_ids={"mv2": ("soap", "out", 1, "bs1")}))
result("on hand after the replay (one pack left the shop)", q(
    "SELECT on_hand_qty_base FROM batch_stock WHERE batch_id='bs1'"))
result("sale movements for the one line", q(
    "SELECT COUNT(*) FROM inventory_movements WHERE bill_id='b_rep' AND reason='sale'"))
result("rollup after the replay (unchanged, as D13 promises)",
       q("SELECT qty_base, amount_minor, bill_count FROM sales_daily "
         "WHERE store_id='s1' AND item_id='soap' AND business_date='2026-11-14'"))
result("a unique index on the ledger would have refused the duplicate",
       write("CREATE UNIQUE INDEX uq_mv_line ON inventory_movements(bill_id, bill_line_no) "
             "WHERE bill_id IS NOT NULL"))

# ---------------------------------------------------------------------------
case(8, "Void: full reversal only; the poisoned bill cannot be voided at all; no partial return")
result("void a clean bill (b_ctrl)",
       write("INSERT INTO bill_voids (id,bill_id,store_id,terminal_id,user_id,reason,created_at) "
             "VALUES ('v1','b_ctrl','s1','t1','u1','customer left',1763107300)"))
result("bill flagged voided", q("SELECT voided FROM bills WHERE id='b_ctrl'"))
for row in con.execute("SELECT item_id, qty_base, amount_minor, bill_count FROM sales_daily "
                       "WHERE store_id='s1' AND business_date='2026-11-14' ORDER BY item_id"):
    result("  rollup", row)
result("  tam bills on the day and their status",
       [r for r in con.execute("SELECT b.id, b.total_minor, b.voided FROM bills b JOIN bill_lines l "
                               "ON l.bill_id=b.id WHERE l.item_id='tam' AND b.business_date='2026-11-14' "
                               "ORDER BY b.id")])
result("a second void of the same bill",
       write("INSERT INTO bill_voids (id,bill_id,store_id,terminal_id,user_id,reason,created_at) "
             "VALUES ('v2','b_ctrl','s1','t1','u1','again',1763107400)"))
result("a void with a whitespace reason",
       write("INSERT INTO bill_voids (id,bill_id,store_id,terminal_id,user_id,reason,created_at) "
             "VALUES ('v3','b_tax0','s1','t1','u1','   ',1763107400)"))
result("void the bill whose one line carries two sale movements (the replayed b_rep)",
       write("INSERT INTO bill_voids (id,bill_id,store_id,terminal_id,user_id,reason,created_at) "
             "VALUES ('v4','b_rep','s1','t1','u1','customer left',1763107400)"))
result("  -> the restock trigger derives the same movement id twice", "the sale can never be corrected")
result("a return as a negative bill total",
       push("b_ret", [dict(item="soap", item_name="Soap", dimension="count", qty_base=1,
                           list_price_minor=10000, price_minor=10000, list_amount_minor=10000,
                           line_total_minor=10000, taxable_minor=8475, cgst_minor=762, sgst_minor=763,
                           tax_rate_bp=1800)], total=-10000, subtotal=-8475, tax=-1525))

# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
case(9, "Invoice numbering: a concurrent duplicate burns a number; a yearly reset cannot work")


def numbered(bid, no, seqno, total=0, date="2026-11-14", created_at=1763107200):
    """A bill with one zero-value line, so the header trigger is satisfied."""
    con.execute("BEGIN")
    con.execute("PRAGMA defer_foreign_keys = on")
    con.execute("""INSERT INTO bill_lines (bill_id, line_no, item_id, item_name, dimension, qty_base,
        price_version, price_source, list_price_minor, price_minor, price_basis_qty_base, discount_minor,
        list_amount_minor, price_breakdown, line_total_minor, taxable_minor, tax_rate_bp, cgst_minor,
        sgst_minor, cess_minor, igst_minor) VALUES (?,1,'soap','Soap','count',1,1,'list',0,0,1,0,0,'{}',?,?,0,0,0,0,0)""",
                 (bid, total, total))
    con.execute("""INSERT INTO bills (id, store_id, terminal_id, user_id, auth_method, business_date, local_seq,
        display_no, invoice_no, total_minor, subtotal_minor, tax_minor, created_at)
        VALUES (?, 's1','t1','u1','pin_offline',?,?,?,?,?,?,0,?)""",
                 (bid, date, seqno, f"N{seqno}", f"KUK/2026/{no:06d}", total, total, created_at))
    con.execute("COMMIT")
    return "OK"


for i, bid in enumerate(["n1", "n2", "n3"], 1):
    con.execute("UPDATE numbering_policy SET next_value = next_value + 1 WHERE id='np1'")
    no = q("SELECT next_value - 1 FROM numbering_policy WHERE id='np1'")[0]
    numbered(bid, no, 100 + i)
result("three bills", [r[0] for r in con.execute("SELECT invoice_no FROM bills WHERE id LIKE 'n%' ORDER BY id")])
con.execute("UPDATE numbering_policy SET next_value = next_value + 1 WHERE id='np1'")   # racing duplicate
result("the losing concurrent push incremented the counter, then its header inserted nothing",
       f"counter {q('SELECT next_value FROM numbering_policy WHERE id=?', 'np1')[0] - 1}, "
       f"highest number issued 000003")
con.execute("UPDATE numbering_policy SET next_value = next_value + 1 WHERE id='np1'")
no = q("SELECT next_value - 1 FROM numbering_policy WHERE id='np1'")[0]
numbered("n4", no, 104)
result("next real invoice", [r[0] for r in con.execute(
    "SELECT invoice_no FROM bills WHERE id LIKE 'n%' ORDER BY id")])
result("reset the counter for the new financial year, same static prefix",
       write("UPDATE numbering_policy SET next_value = 1 WHERE id='np1'"))
con.execute("UPDATE numbering_policy SET next_value = next_value + 1 WHERE id='np1'")
try:
    numbered("n5", 1, 105, date="2027-04-01")
    reset = "OK"
except sqlite3.Error as e:
    reset = str(e)
    if con.in_transaction:
        con.execute("ROLLBACK")
result("first invoice of the reset year, same static prefix", reset)
result("bills in the table with no legal invoice number (printed offline)",
       q("SELECT COUNT(*) FROM bills WHERE invoice_no IS NULL"))

# ---------------------------------------------------------------------------
case(10, "local_seq after a reinstall, and display_no after insert")
result("first bill on terminal t1 at seq 900", push("r1x", [dict(item="soap", item_name="Soap", dimension="count",
                                                               qty_base=1)], local_seq=900))
result("a re-installed terminal (fresh disk) starts again at 900 with a new bill id",
       push("r2x", [dict(item="soap", item_name="Soap", dimension="count", qty_base=1)], local_seq=900))
result("display_no is not frozen after insert",
       write("UPDATE bills SET display_no='EDITED' WHERE id='r1x'"))

# ---------------------------------------------------------------------------
case(11, "Another store's price override can be applied, and the Worker lookup by key finds it")
result("store s2 override at v2", write(
    "INSERT INTO item_prices (item_id,store_id,qty_base,version,price_minor,valid_from,created_at) "
    "VALUES ('tam','s2',1000,2,3000,100,0)"))
result("line at store s1 that names s2 as its price scope",
       push("b_xstore", [dict(item="tam", item_name="Tamarind", dimension="mass", qty_base=1000,
                              list_price_minor=3000, price_minor=3000, price_basis_qty_base=1000,
                              price_version=2, price_store_id="s2", list_amount_minor=3000,
                              line_total_minor=3000, taxable_minor=2857, cgst_minor=71, sgst_minor=72,
                              tax_rate_bp=500)]))
result("worker lookup by (item, price_store_id, version) finds the other store's row",
       q("SELECT p.store_id, p.price_minor FROM item_prices p JOIN bill_lines l ON l.item_id=p.item_id "
         "AND l.price_version=p.version AND IFNULL(p.store_id,'')=IFNULL(l.price_store_id,'') "
         "WHERE l.bill_id='b_xstore'"))

# ---------------------------------------------------------------------------
case(12, "Clock skew: an offline bill may land in any business date, including a closed month")
old = 1730000000  # 2024-10-27
result("bill created a year before it is received, business_date from its own clock",
       push("b_skew", [dict(item="soap", item_name="Soap", dimension="count", qty_base=1,
                            list_price_minor=10000, price_minor=10000, list_amount_minor=10000,
                            line_total_minor=10000, taxable_minor=8475, cgst_minor=762, sgst_minor=763,
                            tax_rate_bp=1800)], created_at=old, date="2024-10-27", local_seq=777))
result("bill with created_at in the future",
       push("b_future", [dict(item="soap", item_name="Soap", dimension="count", qty_base=1)],
            created_at=2000000000, date="2033-05-18", local_seq=778))
result("the DDL accepts both", q("SELECT COUNT(*) FROM bills WHERE id IN ('b_skew','b_future')"))

# ---------------------------------------------------------------------------
case(13, "Rounding: the day's bill totals do not equal the day's line totals")
result("a rounded bill (lines 6500, cash 6471)",
       push("b_round", [dict(item="tam", item_name="Tamarind", dimension="mass", qty_base=1500,
                             list_price_minor=4000, price_minor=4000, price_basis_qty_base=1000,
                             list_amount_minor=6500, line_total_minor=6500, taxable_minor=6190,
                             cgst_minor=155, sgst_minor=155, tax_rate_bp=500)], rounding=-29))
result("day report value from lines (D10) vs money the customer paid",
       q("""SELECT (SELECT SUM(l.line_total_minor) FROM bill_lines l JOIN bills b ON b.id=l.bill_id
                     WHERE b.store_id='s1' AND b.business_date='2026-11-14' AND b.voided=0) AS from_lines,
                  (SELECT SUM(total_minor) FROM bills WHERE store_id='s1' AND business_date='2026-11-14'
                     AND voided=0) AS from_headers,
                  (SELECT SUM(rounding_minor) FROM bills WHERE store_id='s1' AND business_date='2026-11-14'
                     AND voided=0) AS rounding"""))
result("rounding_mode is not copied onto the bill",
       q("SELECT COUNT(*) FROM pragma_table_info('bills') WHERE name='rounding_mode'"))

# ---------------------------------------------------------------------------
case(14, "A store override that does not repeat every default tier: two scopes in one line, one version")
result("store s1 override at v3 with only the 1000 g tier (v2 belongs to another store)",
       write("INSERT INTO item_prices (item_id,store_id,qty_base,version,price_minor,valid_from,created_at) "
             "VALUES ('tam','s1',1000,3,3900,150,0)"))
for tier in (1000, 500):
    rows = [tuple(r) for r in con.execute(
        """SELECT store_id, qty_base, version, price_minor FROM item_prices
           WHERE item_id='tam' AND qty_base=? AND (store_id IS 's1' OR store_id IS NULL)
             AND valid_from <= 1763107200 AND (valid_to IS NULL OR valid_to > 1763107200)
           ORDER BY store_id IS NULL, valid_from DESC LIMIT 1""", (tier,))]
    result(f"  query 3 for a {tier} g tier in store s1", rows)
result("the two parts a 1.5 kg line would use: 3900 (v3, store) + 2500 (v1, default)",
       "list_amount 6400, but no single (version, store) names both")
result("rows that (item, store, version=3) returns",
       [tuple(r) for r in con.execute(
           "SELECT qty_base, price_minor FROM item_prices WHERE item_id='tam' AND version=3 AND store_id='s1'")])
result("rows that version=1, store NULL returns",
       [tuple(r) for r in con.execute(
           "SELECT qty_base, price_minor FROM item_prices WHERE item_id='tam' AND version=1 AND store_id IS NULL")])
result("a line naming v3/store s1 with the true 6400 list amount",
       push("b_scope", [dict(item="tam", item_name="Tamarind", dimension="mass", qty_base=1500,
                             list_price_minor=3900, price_minor=3900, price_basis_qty_base=1000,
                             price_version=3, price_store_id="s1", list_amount_minor=6400,
                             line_total_minor=6400, taxable_minor=6095, cgst_minor=152, sgst_minor=153,
                             tax_rate_bp=500)]))

# ---------------------------------------------------------------------------
case(15, "linear / fractional quantities: the register price query has no row for them")
result("loose rice priced 4800 per 1000 g",
       write("INSERT INTO item_prices (item_id,store_id,qty_base,version,price_minor,valid_from,created_at) "
             "VALUES ('rice',NULL,1000,1,4800,100,0)"))
for qty in (1000, 1350):
    rows = [tuple(r) for r in con.execute(
        """SELECT price_minor, version FROM item_prices WHERE item_id='rice' AND qty_base=?
           AND (store_id IS 's1' OR store_id IS NULL) AND valid_from <= 1763107200
           AND (valid_to IS NULL OR valid_to > 1763107200)
           ORDER BY store_id IS NULL, valid_from DESC LIMIT 1""", (qty,))]
    result(f"  query 3 for a {qty} g sale of a linear item", rows)
result("the same sale priced by pro-rating and stored (5% inclusive: tax 309, taxable 6171)",
       push("b_linear", [dict(item="rice", item_name="Loose Rice", dimension="mass", qty_base=1350,
                              list_price_minor=4800, price_minor=4800, price_basis_qty_base=1000,
                              list_amount_minor=6480, line_total_minor=6480, taxable_minor=6171,
                              cgst_minor=154, sgst_minor=155, tax_rate_bp=500)]))
result("a fixed_only item sold in a fraction of a pack is not refused either",
       push("b_frac", [dict(item="soap", item_name="Soap", dimension="count", qty_base=1,
                            list_price_minor=10000, price_minor=10000, list_amount_minor=5000,
                            line_total_minor=5000, taxable_minor=4237, cgst_minor=381, sgst_minor=382,
                            tax_rate_bp=1800)]))

# ---------------------------------------------------------------------------
case(16, "Closing the last open price of a scope leaves the item with no price at all")
result("close both default tiers of tamid at t=500 (a promo that ended, no successor written)",
       write("UPDATE item_prices SET valid_to = 500 WHERE item_id='tam' AND store_id IS NULL AND valid_to IS NULL"))
for store, tier in (("s2", 1000), ("s1", 500)):
    rows = [tuple(r) for r in con.execute(
        """SELECT price_minor, version FROM item_prices WHERE item_id='tam' AND qty_base=?
           AND (store_id IS ? OR store_id IS NULL) AND valid_from <= 600
           AND (valid_to IS NULL OR valid_to > 600)
           ORDER BY store_id IS NULL, valid_from DESC LIMIT 1""", (tier, store))]
    result(f"  query 3 in store {store} for a {tier} g sale at t=600", rows)
result("open price rows left for tam (any scope)",
       q("SELECT COUNT(*) FROM item_prices WHERE item_id='tam' AND valid_to IS NULL"))

print("\nDDL version:", q("SELECT schema_version FROM schema_meta"))
print("cases run:", seq[0], "bills attempted; lines:", q("SELECT COUNT(*) FROM bill_lines")[0])
