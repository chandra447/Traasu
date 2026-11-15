#!/usr/bin/env python3
"""Assert the money, immutability, void, rollup and archival rules the DDL enforces.

Each case is a sale or a write that the audit (docs/review-architecture.md) showed going
wrong. Runs the DDL + every migration with foreign keys on, exactly as D1 and the terminal do.
"""
import pathlib, sqlite3, sys

here = pathlib.Path(__file__).parent
con = sqlite3.connect(":memory:", isolation_level=None)
con.execute("PRAGMA foreign_keys = ON")
con.executescript((here / "d1-schema.sql").read_text())
for m in sorted((here / "migrations").glob("*.sql")):
    con.executescript(m.read_text())

con.executescript("""
INSERT INTO stores (id, name, created_at) VALUES ('s1', 'S1', 0), ('s2', 'S2', 0);
INSERT INTO terminals (id, store_id, name, created_at) VALUES ('t1', 's1', 'T1', 0);
INSERT INTO users (id, name, role, created_at) VALUES ('u1', 'Cashier', 'cashier', 0);
INSERT INTO items (id, name, dimension, pricing_mode, base_unit, default_sales_unit, updated_at)
VALUES ('i1', 'Dal', 'count', 'fixed_only', 'pcs', 'pcs', 0),
       ('i2', 'Oil', 'count', 'fixed_only', 'pcs', 'pcs', 0);
INSERT INTO staff_sessions (id, user_id, store_id, terminal_id, auth_method, started_at)
VALUES ('ss1', 'u1', 's1', 't1', 'pin_offline', 1000);
""")

seq = [0]


def line(no, item="i1", total=1029, taxable=980, cgst=24, sgst=25, igst=0, list_amount=None, discount=0):
    return dict(no=no, item=item, total=total, taxable=taxable, cgst=cgst, sgst=sgst, igst=igst,
                list_amount=total + discount if list_amount is None else list_amount, discount=discount)


def push(bill_id, lines, total=None, subtotal=None, tax=None, rounding=0, created_at=1500, date="2026-11-14",
         session="ss1"):
    """One bill exactly as the Worker batches it: deferred FKs, lines, header last."""
    seq[0] += 1
    total = sum(l["total"] for l in lines) + rounding if total is None else total
    subtotal = sum(l["taxable"] for l in lines) if subtotal is None else subtotal
    tax = sum(l["cgst"] + l["sgst"] + l["igst"] for l in lines) if tax is None else tax
    con.execute("BEGIN")
    try:
        con.execute("PRAGMA defer_foreign_keys = on")
        for l in lines:
            con.execute(
                """INSERT INTO bill_lines (bill_id, line_no, item_id, item_name, dimension, qty_base, price_version,
                     price_source, list_price_minor, price_minor, price_basis_qty_base, discount_minor, list_amount_minor,
                     price_breakdown, line_total_minor, taxable_minor, tax_rate_bp, cgst_minor, sgst_minor, cess_minor,
                     igst_minor)
                   VALUES (?, ?, ?, 'x', 'count', 1, 1, 'list', ?, ?, 1, ?, ?, '{}', ?, ?, 500, ?, ?, 0, ?)
                   ON CONFLICT (bill_id, line_no) DO NOTHING""",
                (bill_id, l["no"], l["item"], l["list_amount"], l["list_amount"], l["discount"], l["list_amount"],
                 l["total"], l["taxable"], l["cgst"], l["sgst"], l["igst"]))
        con.execute(
            """INSERT INTO bills (id, store_id, terminal_id, user_id, auth_method, business_date, local_seq, display_no,
                 total_minor, subtotal_minor, tax_minor, rounding_minor, created_at, staff_session_id)
               VALUES (?, 's1', 't1', 'u1', 'pin_offline', ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (id) DO NOTHING""",
            (bill_id, date, seq[0], f"d{seq[0]}", total, subtotal, tax, rounding, created_at, session))
        con.execute("COMMIT")
        return "OK"
    except sqlite3.Error as e:
        con.execute("ROLLBACK")
        return str(e)


def write(sql, *args):
    try:
        con.execute(sql, args)
        return "OK"
    except sqlite3.Error as e:
        return str(e)


def rollup(item="i1"):
    return con.execute("SELECT qty_base, amount_minor, bill_count FROM sales_daily WHERE item_id = ?",
                       (item,)).fetchone()


failures = 0


def expect(label, got, want):
    global failures
    ok = got == want
    failures += not ok
    print(f"{'ok  ' if ok else 'FAIL'} {label}: {got}" + ("" if ok else f"   (want {want})"))


# Money (D23)
expect("5% inclusive 1029 split cgst=24 sgst=25", push("b1", [line(1)]), "OK")
expect("same line split 25/25 (sum 1030)", push("b2", [line(1, cgst=25, sgst=25)]), "line_total_identity")
expect("split 23/26 violates floor rule", push("b3", [line(1, cgst=23, sgst=26)]), "tax_split_rule")
expect("IGST line, total includes the tax", push("b4", [line(1, total=11800, taxable=10000, cgst=0, sgst=0,
                                                          igst=1800)]), "OK")
expect("IGST plus CGST on one line", push("b5", [line(1, total=13600, taxable=10000, cgst=900, sgst=900,
                                                      igst=1800)]), "tax_split_conflict")
expect("discount with list_amount 0 (old bypass)", push("b6", [line(1, list_amount=0, discount=99999)]),
       "discount_identity")
expect("line discount reconciles", push("b7", [line(1, discount=100)]), "OK")
expect("header total 1 against a 1029 line", push("b8", [line(1)], total=1, subtotal=0, tax=1),
       "bill_lines_mismatch")
expect("header tax at invoice level (458) vs lines (459)",
       push("b9", [line(1, total=1001, taxable=848, cgst=76, sgst=77),
                   line(2, total=1001, taxable=848, cgst=76, sgst=77),
                   line(3, total=1001, taxable=848, cgst=76, sgst=77)],
            total=3003, subtotal=2545, tax=458), "bill_lines_mismatch")
expect("bill with no lines", push("b10", [], total=0, subtotal=0, tax=0), "bill_lines_mismatch")
expect("nearest-rupee rounding on top of lines", push("b11", [line(1)], rounding=-29), "OK")

# Staff sessions (D16): accepted, the Worker flags; never aborted
expect("bill 2 s before session start (clock step)", push("b12", [line(1)], created_at=998), "OK")
write("UPDATE staff_sessions SET ended_at = 2000, end_reason = 'switch_user' WHERE id = 'ss1'")
expect("cart finished after switch-user", push("b13", [line(1)], created_at=2005), "OK")
expect("session close is one-way", write("UPDATE staff_sessions SET ended_at = 3000 WHERE id = 'ss1'"),
       "session_already_closed")

# Rollup (D13) is trigger-maintained and replay-safe
before = rollup()
expect("replay of b1 is a no-op", push("b1", [line(1)]), "OK")
expect("rollup unchanged by replay", rollup(), before)

# Voids (D25)
expect("bill inserted as voided", write(
    """INSERT INTO bills (id, store_id, terminal_id, user_id, auth_method, business_date, local_seq, display_no,
         total_minor, voided, voided_at, voided_by) VALUES ('bv', 's1', 't1', 'u1', 'pin_offline', '2026-11-14', 99,
         'dv', 0, 1, 1, 'u1')"""), "void_via_bill_voids")
qty, amt, cnt = rollup()
expect("void from another store", write(
    "INSERT INTO bill_voids (id, bill_id, store_id, terminal_id, user_id, reason, created_at) "
    "VALUES ('v0', 'b1', 's2', 't1', 'u1', 'wrong', 3000)"), "void_store_mismatch")
expect("void b1", write("INSERT INTO bill_voids (id, bill_id, store_id, terminal_id, user_id, reason, created_at) "
                        "VALUES ('v1', 'b1', 's1', 't1', 'u1', 'customer left', 3000)"), "OK")
expect("void flips the bill flag",
       con.execute("SELECT voided, voided_by, void_reason FROM bills WHERE id = 'b1'").fetchone(),
       (1, "u1", "customer left"))
expect("void decrements the rollup", rollup(), (qty - 1, amt - 1029, cnt - 1))
expect("un-void", write("UPDATE bills SET voided = 0, voided_at = NULL, voided_by = NULL, void_reason = NULL "
                        "WHERE id = 'b1'"), "void_immutable")

# Immutability and archival (D15, D22)
expect("delete a bill inside retention", write("DELETE FROM bills WHERE id = 'b7'"), "bill_not_archivable")
expect("line delete", write("DELETE FROM bill_lines WHERE bill_id = 'b7'"), "bill_line_immutable")
expect("delete a device", write(
    "INSERT INTO devices (id, store_id, terminal_id, name, credential_prefix, credential_hash, install_id, "
    "enrolled_at) VALUES ('d1', 's1', 't1', 'D', 'p', 'h', 'i', 0)") + "/" + write("DELETE FROM devices WHERE id = 'd1'"),
       "OK/device_deactivate_only")
rollup_rows = con.execute("SELECT COUNT(*) FROM sales_daily").fetchone()
write("UPDATE schema_meta SET archive_before = '2026-12-01' WHERE id = 1")
expect("archive: voids first, then bills", write("DELETE FROM bill_voids WHERE bill_id = 'b1'") + "/" +
       write("DELETE FROM bills WHERE business_date < '2026-12-01'"), "OK/OK")
expect("archive leaves no lines behind", con.execute("SELECT COUNT(*) FROM bill_lines").fetchone(), (0,))
expect("archive keeps sales_daily", con.execute("SELECT COUNT(*) FROM sales_daily").fetchone(), rollup_rows)

# Item and price versions (D26)
expect("item edit without a version bump", write("UPDATE items SET name = 'Dal Premium' WHERE id = 'i1'"),
       "item_version_not_bumped")
expect("item edit with a version bump", write("UPDATE items SET name = 'Dal Premium', version = 2 WHERE id = 'i1'"),
       "OK")
expect("terminal replays the same item version", write(
    "UPDATE items SET name = 'Dal Premium', version = 2 WHERE id = 'i1'"), "OK")
expect("item version goes backwards", write("UPDATE items SET name = 'Dal', version = 1 WHERE id = 'i1'"),
       "item_version_not_bumped")


def price(store, qty, version, valid_from, amount=4000):
    return write("INSERT INTO item_prices (item_id, store_id, qty_base, version, price_minor, valid_from, created_at) "
                 "VALUES ('i2', ?, ?, ?, ?, ?, 0)", store, qty, version, amount, valid_from)


expect("default tier set v1 (two tiers)", price(None, 1000, 1, 100) + "/" + price(None, 500, 1, 100, 2500), "OK/OK")
expect("store override reuses v1", price("s1", 1000, 1, 100, 3900), "price_version_scope")
expect("store override takes v2", price("s1", 1000, 2, 100, 3900), "OK")
expect("default v3 while v1 tiers still open (half-open)", price(None, 1000, 3, 200), "price_revision_mixed")
write("UPDATE item_prices SET valid_to = 200 WHERE item_id = 'i2' AND store_id IS NULL AND valid_to IS NULL")
expect("default v3 after closing every v1 tier", price(None, 1000, 3, 200, 4200) + "/" +
       price(None, 500, 3, 200, 2600), "OK/OK")
expect("older version inserted after a newer one", price("s2", 1000, 2, 300), "price_version_scope")

# Batches (D27): soap received in April at MRP 100 and in May at MRP 200, both on the shelf in store s1
write("INSERT INTO items (id, name, dimension, pricing_mode, base_unit, default_sales_unit, uses_batches, updated_at) "
      "VALUES ('soap', 'Soap', 'count', 'fixed_only', 'pcs', 'pcs', 1, 0)")


def batch(bid, no, mrp, received_at, expiry=None, store="s1"):
    return write("INSERT INTO item_batches (id, item_id, store_id, batch_no, mrp_minor, expiry_date, received_at, "
                 "updated_at) VALUES (?, 'soap', ?, ?, ?, ?, ?, 0)", bid, store, no, mrp, expiry, received_at)


def move(mid, bid, direction, qty, reason, bill=None, line_no=None):
    return write("INSERT INTO inventory_movements (id, item_id, bill_id, bill_line_no, direction, qty_base, occurred_at, "
                 "created_at, batch_id, store_id, reason) VALUES (?, 'soap', ?, ?, ?, ?, 0, 0, ?, 's1', ?)",
                 mid, bill, line_no, direction, qty, bid, reason)


def on_hand(bid):
    return con.execute("SELECT on_hand_qty_base FROM batch_stock WHERE batch_id = ?", (bid,)).fetchone()[0]


PICKER = """
WITH live AS (
  SELECT b.id, b.received_at, IFNULL(s.on_hand_qty_base, 0) AS on_hand
  FROM item_batches b LEFT JOIN batch_stock s ON s.batch_id = b.id
  WHERE b.item_id = ?1 AND b.store_id = ?2 AND b.retired_at IS NULL
    AND (b.expiry_date IS NULL OR b.expiry_date >= ?3))
SELECT id FROM live
WHERE on_hand > 0 OR id = (SELECT id FROM live ORDER BY received_at DESC, id DESC LIMIT 1)
ORDER BY received_at DESC, id DESC
"""


def picker(day="2026-11-14"):
    return [r[0] for r in con.execute(PICKER, ("soap", "s1", day))]


expect("April batch, no expiry", batch("b_apr", "APR-24", 10000, 100), "OK")
expect("May batch, no expiry", batch("b_may", "MAY-24", 20000, 200), "OK")
expect("old batch that expired", batch("b_old", "JAN-24", 9000, 50, expiry="2026-01-01"), "OK")
expect("receive 10 / 24 / 5", "/".join([move("r1", "b_apr", "in", 10, "receipt"), move("r2", "b_may", "in", 24, "receipt"),
                                         move("r3", "b_old", "in", 5, "receipt")]), "OK/OK/OK")
expect("April price in its own store", write(
    "INSERT INTO item_prices (item_id, store_id, batch_id, qty_base, version, price_minor, mrp_minor, valid_from, "
    "created_at) VALUES ('soap', 's1', 'b_apr', 1, 1, 10000, 10000, 100, 0)"), "OK")
expect("May price open at the same time", write(
    "INSERT INTO item_prices (item_id, store_id, batch_id, qty_base, version, price_minor, mrp_minor, valid_from, "
    "created_at) VALUES ('soap', 's1', 'b_may', 1, 2, 20000, 20000, 200, 0)"), "OK")
expect("batch price in another store", write(
    "INSERT INTO item_prices (item_id, store_id, batch_id, qty_base, version, price_minor, valid_from, created_at) "
    "VALUES ('soap', 's2', 'b_may', 1, 3, 20000, 200, 0)"), "batch_price_scope")
expect("picker: both stocked batches, newest first, expired hidden", picker(), ["b_may", "b_apr"])
expect("sell all 10 of April", move("s1m", "b_apr", "out", 10, "sale"), "OK")
expect("April on hand", on_hand("b_apr"), 0)
expect("picker: April out of stock, only May", picker(), ["b_may"])
expect("May sold out too (stock count)", move("a1", "b_may", "out", 24, "adjustment"), "OK")
expect("picker: the newest batch always shows", picker(), ["b_may"])
expect("sell past zero offline", move("s2m", "b_may", "out", 2, "sale") + "/" + str(on_hand("b_may")), "OK/-2")
expect("April restocked: shows again", move("r4", "b_apr", "in", 6, "receipt") + "/" + ",".join(picker()),
       "OK/b_may,b_apr")
expect("void puts the goods back", push("bsoap", [line(1, item="soap")]) + "/" +
       move("s3m", "b_apr", "out", 1, "sale", bill="bsoap", line_no=1) + "/" + str(on_hand("b_apr")) + "/" +
       write("INSERT INTO bill_voids (id, bill_id, store_id, terminal_id, user_id, reason, created_at) "
             "VALUES ('vsoap', 'bsoap', 's1', 't1', 'u1', 'wrong pack', 4000)") + "/" + str(on_hand("b_apr")),
       "OK/OK/5/OK/6")
expect("batch edit without a version bump", write("UPDATE item_batches SET expiry_date = '2027-01-01' "
                                                  "WHERE id = 'b_may'"), "batch_version_not_bumped")
expect("batch edit with a version bump", write("UPDATE item_batches SET expiry_date = '2027-01-01', version = 2 "
                                               "WHERE id = 'b_may'"), "OK")
expect("picker after May expires", picker("2027-02-01"), ["b_apr"])
expect("delete a batch", write("DELETE FROM item_batches WHERE id = 'b_apr'"), "batch_retire_only")
expect("archival does not change stock", write("UPDATE schema_meta SET archive_before = '2026-12-01'") + "/" +
       write("DELETE FROM bill_voids WHERE bill_id = 'bsoap'") + "/" + write("DELETE FROM bills WHERE id = 'bsoap'") +
       "/" + str(on_hand("b_apr")), "OK/OK/OK/6")

expect("schema_version after all migrations", con.execute("SELECT schema_version FROM schema_meta").fetchone(),
       (len(list((here / "migrations").glob("*.sql"))) + 1,))

print("\nOK" if failures == 0 else f"\n{failures} invariant(s) broken")
sys.exit(1 if failures else 0)
