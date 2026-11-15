#!/usr/bin/env python3
"""Repack / parent-child (D29, D30) parity audit reproductions against the real DDL.

Every case runs on a fresh in-memory SQLite with docs/d1-schema.sql + every migration,
foreign keys ON, exactly like docs/check-invariants.py and the register's local copy.
"""
import pathlib
import sqlite3
import sys

DOCS = pathlib.Path(__file__).resolve().parents[2]

failures = 0


def expect(label, got, want):
    global failures
    ok = got == want
    failures += not ok
    print(f"{'ok  ' if ok else 'FAIL'} {label}: {got}" + ("" if ok else f"   (want {want})"))


def fresh(books=True):
    con = sqlite3.connect(":memory:", isolation_level=None)
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript((DOCS / "d1-schema.sql").read_text())
    for m in sorted((DOCS / "migrations").glob("*.sql")):
        con.executescript(m.read_text())
    con.executescript("""
    INSERT INTO stores (id, name, created_at) VALUES ('s1', 'S1', 0), ('s2', 'S2', 0);
    INSERT INTO terminals (id, store_id, name, created_at) VALUES ('t1', 's1', 'T1', 0);
    INSERT INTO users (id, name, role, created_at) VALUES ('u1', 'Cashier', 'cashier', 0);
    INSERT INTO staff_sessions (id, user_id, store_id, terminal_id, auth_method, started_at)
      VALUES ('ss1', 'u1', 's1', 't1', 'pin_offline', 1000);
    """)
    if books:
        con.executescript("""
        INSERT INTO books (id, store_id, name, priority, active, version, created_at, updated_at)
          VALUES ('k_main', 's1', 'Main', 1, 1, 1, 0, 0), ('k_god', 's1', 'Godown', 2, 1, 1, 0, 0),
                 ('k_dead', 's1', 'Closed', 3, 0, 1, 0, 0);
        """)
    return con


def w(con, sql, *args):
    try:
        con.execute(sql, args)
        return "OK"
    except sqlite3.Error as e:
        return str(e)


def on_hand(con, batch):
    row = con.execute("SELECT on_hand_qty_base FROM batch_stock WHERE batch_id = ?", (batch,)).fetchone()
    return None if row is None else row[0]


def batch(con, bid, item, mrp, book="k_main", expiry=None, repack=None):
    return w(con, "INSERT INTO item_batches (id, item_id, store_id, book_id, batch_no, mrp_minor, expiry_date, "
                  "received_at, updated_at, repack_id) VALUES (?, ?, 's1', ?, ?, ?, ?, 500, 0, ?)",
             bid, item, book, bid, mrp, expiry, repack)


def move(con, mid, bid, direction, qty, reason, item, repack=None, receipt=None):
    return w(con, "INSERT INTO inventory_movements (id, item_id, direction, qty_base, occurred_at, created_at, "
                  "batch_id, store_id, reason, repack_id, receipt_id) "
                  "VALUES (?, ?, ?, ?, 500, 500, ?, 's1', ?, ?, ?)",
             mid, item, direction, qty, bid, reason, repack, receipt)


def item(con, iid, dimension="count", unit="pcs", batched=0, parent=None, content=None):
    return w(con, "INSERT INTO items (id, name, dimension, pricing_mode, base_unit, default_sales_unit, "
                  "uses_batches, parent_item_id, content_qty_base, updated_at) "
                  "VALUES (?, ?, ?, 'fixed_only', ?, ?, ?, ?, ?, 0)",
             iid, iid, dimension, unit, unit, batched, parent, content)


def price(con, con_item, qty, ver, minor, bid=None, store="s1"):
    return w(con, "INSERT INTO item_prices (item_id, store_id, qty_base, version, price_minor, valid_from, "
                  "created_at, batch_id) VALUES (?, ?, ?, ?, ?, 100, 100, ?)",
             con_item, store, qty, ver, minor, bid)


def push(con, bill_id, lines, seq=[0], created_at=1500):
    """One bill exactly as the Worker batches it: deferred FKs, lines, movements, header last."""
    seq[0] += 1
    total = sum(l["total"] for l in lines)
    subtotal = sum(l["taxable"] for l in lines)
    tax = sum(l["cgst"] + l["sgst"] for l in lines)
    con.execute("BEGIN")
    try:
        con.execute("PRAGMA defer_foreign_keys = on")
        for l in lines:
            row = dict(bill_id=bill_id, line_no=l["no"], item_id=l["item"], item_name="x",
                       dimension="count", qty_base=l["qty"], price_version=l.get("ver", 1), price_source="list",
                       list_price_minor=l["total"], price_minor=l["total"], price_basis_qty_base=1,
                       discount_minor=0, list_amount_minor=l["total"], price_breakdown="{}",
                       line_total_minor=l["total"], taxable_minor=l["taxable"], tax_rate_bp=500,
                       cgst_minor=l["cgst"], sgst_minor=l["sgst"], cess_minor=0, igst_minor=0,
                       batch_id=l.get("batch"), batch_no=l.get("batch_no"), mrp_minor=l.get("mrp"),
                       stock_item_id=l.get("stock_item"), stock_batch_id=l.get("stock_batch"),
                       stock_qty_base=l.get("stock_qty"))
            cols = ", ".join(row)
            marks = ", ".join("?" * len(row))
            con.execute(f"INSERT INTO bill_lines ({cols}) VALUES ({marks}) "
                        "ON CONFLICT (bill_id, line_no) DO NOTHING", tuple(row.values()))
        for l in lines:
            if l.get("movement_item"):
                con.execute(
                    """INSERT INTO inventory_movements (id, item_id, bill_id, bill_line_no, direction, qty_base,
                         occurred_at, created_at, batch_id, store_id, reason)
                       VALUES (?, ?, ?, ?, 'out', ?, ?, ?, ?, 's1', 'sale')
                       ON CONFLICT (id) DO NOTHING""",
                    (f'{bill_id}:{l["no"]}:sale', l["movement_item"], bill_id, l["no"], l.get("stock_qty") or l["qty"],
                     created_at, created_at, l.get("stock_batch") or l.get("batch")))
        con.execute(
            """INSERT INTO bills (id, store_id, terminal_id, user_id, auth_method, business_date, local_seq,
                 display_no, total_minor, subtotal_minor, tax_minor, rounding_minor, created_at, staff_session_id)
               VALUES (?, 's1', 't1', 'u1', 'pin_offline', '2026-11-14', ?, ?, ?, ?, ?, 0, ?, 'ss1')
               ON CONFLICT (id) DO NOTHING""",
            (bill_id, seq[0], f"d{seq[0]}", total, subtotal, tax, created_at))
        con.execute("COMMIT")
        return "OK"
    except sqlite3.Error as e:
        con.execute("ROLLBACK")
        return str(e)


def line(no, iid, qty=1, total=15000, taxable=15000, cgst=0, sgst=0, **kw):
    d = dict(no=no, item=iid, qty=qty, total=total, taxable=taxable, cgst=cgst, sgst=sgst)
    d.update(kw)
    return d


def pack(con, rid, bulk_batch, book="k_main", loss=0, lines=None, deterministic=True):
    """The documented db.batch(): header, new batches, their prices, lines. ON CONFLICT DO NOTHING."""
    lines = lines or []
    con.execute("BEGIN")
    try:
        con.execute("PRAGMA defer_foreign_keys = on")
        con.execute("INSERT INTO repacks (id, store_id, book_id, kind, bulk_batch_id, loss_qty_base, packed_at, "
                    "created_at) VALUES (?, 's1', ?, 'pack', ?, ?, 500, 500) ON CONFLICT (id) DO NOTHING",
                    (rid, book, bulk_batch, loss))
        for i, l in enumerate(lines, 1):
            bid = f"{rid}:{i}:pack" if deterministic else l["batch_id"]
            if l.get("batch_id"):
                bid = l["batch_id"]
            con.execute("INSERT INTO item_batches (id, item_id, store_id, book_id, batch_no, mrp_minor, "
                        "received_at, updated_at, repack_id) VALUES (?, ?, 's1', ?, ?, ?, 500, 0, ?) "
                        "ON CONFLICT (id) DO NOTHING",
                        (bid, l["item"], book, l.get("batch_no", bid), l.get("mrp", 6000), rid))
            con.execute("INSERT INTO item_prices (item_id, store_id, qty_base, version, price_minor, valid_from, "
                        "created_at, batch_id) VALUES (?, 's1', 1, ?, ?, 100, 100, ?) "
                        "ON CONFLICT DO NOTHING",
                        (l["item"], l.get("version", 1), l.get("selling", 7500), bid))
            con.execute("INSERT INTO repack_lines (repack_id, line_no, item_id, batch_id, qty_base, "
                        "content_qty_base) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (repack_id, line_no) DO NOTHING",
                        (rid, i, l["item"], bid, l["qty"], l["content"]))
        con.execute("COMMIT")
        return "OK"
    except sqlite3.Error as e:
        con.execute("ROLLBACK")
        return str(e)


def unpack(con, rid, bulk_batch, book, lines, loss=0):
    con.execute("BEGIN")
    try:
        con.execute("PRAGMA defer_foreign_keys = on")
        con.execute("INSERT INTO repacks (id, store_id, book_id, kind, bulk_batch_id, loss_qty_base, packed_at, "
                    "created_at) VALUES (?, 's1', ?, 'unpack', ?, ?, 600, 600)",
                    (rid, book, bulk_batch, loss))
        for i, l in enumerate(lines, 1):
            con.execute("INSERT INTO repack_lines (repack_id, line_no, item_id, batch_id, qty_base, "
                        "content_qty_base) VALUES (?, ?, ?, ?, ?, ?)",
                        (rid, i, l["item"], l["batch_id"], l["qty"], l["content"]))
        con.execute("COMMIT")
        return "OK"
    except sqlite3.Error as e:
        con.execute("ROLLBACK")
        return str(e)


# the register's D28 lookup for a batched item (query-catalogue.md §3, verbatim shape)
REG_BATCH_QUERY = """
WITH live AS (
  SELECT b.id, b.batch_no, b.mrp_minor, k.priority, IFNULL(s.on_hand_qty_base, 0) AS on_hand, p.price_minor
  FROM item_batches b
  JOIN books k ON k.id = b.book_id AND k.active = 1
  LEFT JOIN batch_stock s ON s.batch_id = b.id
  LEFT JOIN item_prices p ON p.item_id = b.item_id AND p.batch_id = b.id
                         AND p.qty_base = ?2 AND p.valid_to IS NULL
  WHERE b.item_id = ?1 AND b.store_id = ?3 AND b.retired_at IS NULL
    AND (b.expiry_date IS NULL OR b.expiry_date >= ?4))
SELECT * FROM live ORDER BY on_hand > 0 DESC, priority, id DESC LIMIT 1
"""

CHILD_PICK_QUERY = """
SELECT b.id FROM item_batches b
JOIN books k ON k.id = b.book_id AND k.active = 1
LEFT JOIN batch_stock s ON s.batch_id = b.id
WHERE b.item_id = ?1 AND b.store_id = 's1' AND b.retired_at IS NULL
  AND (b.expiry_date IS NULL OR b.expiry_date >= ?2)
ORDER BY IFNULL(s.on_hand_qty_base, 0) > 0 DESC, k.priority, b.received_at DESC, b.id DESC
LIMIT 1
"""

print("=" * 78)
print("A. Catalog bootstrap: what a fresh register can and cannot get for batches/books")
print("=" * 78)

# A1: snapshot applies items + prices (api-routes §3.3 "Pages cover items, prices, ..."), no batches page.
con = fresh()
print("A1 registry item, then a batch price row -- no item_batch document exists on the device")
expect("item doc applies", item(con, "rice1kg", batched=1, content=1000), "OK")
expect("snapshot 'prices' page: one price row for a pack batch", price(con, "rice1kg", 1, 1, 7500, bid="pb1"),
       "batch_price_scope")
print("  -> the prices page aborts the whole page; it can never be applied before batches")

print("\nA2 the items page itself carries no batch, so nothing can be priced after it")
con = fresh()
expect("item + item-level price only", item(con, "rice1kg", batched=1) + "/" +
       price(con, "rice1kg", 1, 1, 7500), "OK/OK")
expect("register batch lookup for the pack (D28)", con.execute(REG_BATCH_QUERY, ("rice1kg", 1, "s1", "2026-11-14"))
       .fetchall(), [])
print("  -> 0 rows: no batch, no price, the item cannot be added to a cart")

print("\nA3 the register needs book docs too: a batch cannot even be applied without its book")
con = fresh(books=False)
expect("item + batch on a register with no book rows",
       item(con, "rice1kg", batched=1) + "/" + batch(con, "pb1", "rice1kg", 7500), "OK/batch_book_store_mismatch")
print("  -> order must be books, then batches, then prices; the snapshot lists neither")

print("\n" + "=" * 78)
print("B. Catalog page order: a child applied before its parent")
print("=" * 78)

con = fresh()
con.execute("BEGIN")
con.execute("PRAGMA defer_foreign_keys = on")
print("B1 page transaction: child row first, parent row second (page ordered by name, hash or v4 id)")
expect("child Coffee 1kg before Coffee 20kg", item(con, "coffee_1kg", batched=0, parent="coffee", content=1000),
       "item_stock_link_invalid")
con.execute("ROLLBACK")
expect("after the rollback nothing from the page survived",
       con.execute("SELECT COUNT(*) FROM items WHERE id IN ('coffee','coffee_1kg')").fetchone()[0], 0)

con.execute("BEGIN")
print("B2 parent applied in its pre-edit state (uses_batches = 0), then the child")
expect("parent first, still unbatched", item(con, "coffee", dimension="mass", unit="g", batched=0), "OK")
expect("then the child", item(con, "coffee_1kg", batched=0, parent="coffee", content=1000),
       "item_stock_link_invalid")
con.execute("ROLLBACK")

con = fresh()
print("B3 correct order: parent batched first, then the child")
expect("parent", item(con, "coffee", dimension="mass", unit="g", batched=1), "OK")
expect("child", item(con, "coffee_1kg", batched=0, parent="coffee", content=1000), "OK")

print("\n" + "=" * 78)
print("C. Repack idempotency: the route is 'yes on client id', the DDL has no replay path")
print("=" * 78)

con = fresh()
item(con, "rice25", dimension="mass", unit="g", batched=1)
item(con, "rice1kg", batched=1, content=1000)
w(con, "INSERT INTO item_repack_sources (item_id, bulk_item_id, created_at) VALUES ('rice1kg','rice25',0)")
batch(con, "r_b1", "rice25", 150000)
move(con, "rr1", "r_b1", "in", 25000, "receipt", "rice25")
print("C1 first pack: 20 x 1kg, 100 g loss")
expect("pack", pack(con, "rp1", "r_b1", loss=100, lines=[dict(item="rice1kg", qty=20, content=1000)]), "OK")
expect("sack / pack on hand", (on_hand(con, "r_b1"), on_hand(con, "rp1:1:pack")), (4900, 20))

print("C2 exact replay, deterministic batch ids, ON CONFLICT DO NOTHING")
expect("replay", pack(con, "rp1", "r_b1", loss=100, lines=[dict(item="rice1kg", qty=20, content=1000)]),
       "repack_line_invalid")
print("  -> the BEFORE INSERT trigger fires before the (repack_id, line_no) dedupe:")
print("     ON CONFLICT DO NOTHING cannot make a repack replay a no-op")
expect("stock unchanged", (on_hand(con, "r_b1"), on_hand(con, "rp1:1:pack")), (4900, 20))

print("C3 replay where the Worker re-mints the batch id and the next price version")
expect("replay", pack(con, "rp1", "r_b1", loss=0,
                      lines=[dict(item="rice1kg", qty=20, content=1000, batch_id="rp1-b-new", version=2)]), "OK")
expect("the phantom batch: row exists, no line, no stock",
       con.execute("SELECT id, repack_id, mrp_minor FROM item_batches WHERE id = 'rp1-b-new'").fetchone(),
       ("rp1-b-new", "rp1", 6000))
expect("its price row", con.execute("SELECT version, price_minor FROM item_prices WHERE batch_id = 'rp1-b-new'")
       .fetchone(), (2, 7500))
expect("batches of the repack item", con.execute(
    "SELECT COUNT(*) FROM item_batches WHERE item_id = 'rice1kg'").fetchone()[0], 2)
expect("orphan batch on hand (what the route returns)", on_hand(con, "rp1-b-new"), None)
expect("both batches are live and sellable to the register", con.execute(
    REG_BATCH_QUERY.replace("ORDER BY on_hand > 0 DESC, priority, id DESC LIMIT 1", "")
    .replace("SELECT * FROM live", "SELECT id, mrp_minor, on_hand, price_minor FROM live"), ("rice1kg", 1, "s1",
                                                                                            "2026-11-14")).fetchall(),
       [("rp1:1:pack", 6000, 20, 7500), ("rp1-b-new", 6000, 0, 7500)])

print("C4 the same batch without ON CONFLICT: a retry is an error, not a no-op")
expect("header re-insert", w(con, "INSERT INTO repacks (id, store_id, book_id, kind, bulk_batch_id, "
                                 "loss_qty_base, packed_at, created_at) "
                                 "VALUES ('rp1','s1','k_main','pack','r_b1',100,500,500)"),
       "UNIQUE constraint failed: repacks.id")

print("\n" + "=" * 78)
print("D. Unpack (GoFrugal ReUse) is not capped at what was packed")
print("=" * 78)
con = fresh()
item(con, "rice25", dimension="mass", unit="g", batched=1)
item(con, "rice1kg", batched=1, content=1000)
w(con, "INSERT INTO item_repack_sources (item_id, bulk_item_id, created_at) VALUES ('rice1kg','rice25',0)")
batch(con, "r_b1", "rice25", 150000)
move(con, "rr1", "r_b1", "in", 25000, "receipt", "rice25")
pack(con, "rp1", "r_b1", lines=[dict(item="rice1kg", qty=20, content=1000)])
expect("after packing 20: sack / pack", (on_hand(con, "r_b1"), on_hand(con, "rp1:1:pack")), (5000, 20))
expect("unpack 20 back", unpack(con, "ru1", "r_b1", "k_main",
                               [dict(item="rice1kg", batch_id="rp1:1:pack", qty=20, content=1000)]), "OK")
expect("sack / pack", (on_hand(con, "r_b1"), on_hand(con, "rp1:1:pack")), (25000, 0))
expect("unpack 5 more from the same, now empty, pack batch", unpack(
    con, "ru2", "r_b1", "k_main", [dict(item="rice1kg", batch_id="rp1:1:pack", qty=5, content=1000)]), "OK")
expect("sack / pack: 5000 g created, 5 packs sold that never existed",
       (on_hand(con, "r_b1"), on_hand(con, "rp1:1:pack")), (30000, -5))
expect("unpacked so far vs packed", (con.execute(
    "SELECT SUM(qty_base) FROM repack_lines l JOIN repacks r ON r.id = l.repack_id "
    "WHERE r.kind = 'unpack' AND l.batch_id = 'rp1:1:pack'").fetchone()[0], 20), (25, 20))

print("\n" + "=" * 78)
print("E. Repacking into a deactivated book")
print("=" * 78)
con = fresh()
item(con, "rice25", dimension="mass", unit="g", batched=1)
item(con, "rice1kg", batched=1, content=1000)
w(con, "INSERT INTO item_repack_sources (item_id, bulk_item_id, created_at) VALUES ('rice1kg','rice25',0)")
batch(con, "r_b1", "rice25", 150000)
move(con, "rr1", "r_b1", "in", 25000, "receipt", "rice25")
expect("repack 20 packs into the deactivated book", pack(con, "rp1", "r_b1", book="k_dead",
                                                        lines=[dict(item="rice1kg", qty=20, content=1000)]), "OK")
expect("pack stock recorded", on_hand(con, "rp1:1:pack"), 20)
expect("register lookup for the pack item", con.execute(REG_BATCH_QUERY, ("rice1kg", 1, "s1", "2026-11-14"))
       .fetchall(), [])
print("  -> 20 packs exist and cannot be priced or sold; the 20000 g left the sack")

print("\nE2 the same hole on the receipt path (purchase entry into a deactivated book)")
con = fresh()
item(con, "soap", batched=1, dimension="count")
expect("receipt", w(con, "INSERT INTO stock_receipts (id, store_id, book_id, received_at) "
                          "VALUES ('rc1','s1','k_dead',500)") + "/" + batch(con, "sb1", "soap", 10000, book="k_dead")
       + "/" + move(con, "rm1", "sb1", "in", 10, "receipt", "soap", receipt="rc1"), "OK/OK/OK")
expect("register lookup", con.execute(REG_BATCH_QUERY, ("soap", 1, "s1", "2026-11-14")).fetchall(), [])

print("\n" + "=" * 78)
print("F. uses_batches 1 -> 0 on an item that already has stock and sales")
print("=" * 78)
con = fresh()
item(con, "soap", batched=1)
batch(con, "sb1", "soap", 10000)
move(con, "rm1", "sb1", "in", 10, "receipt", "soap")
price(con, "soap", 1, 1, 9500, bid="sb1")
expect("a sale from the batch", push(con, "bs1", [dict(
    no=1, item="soap", qty=1, total=9500, taxable=9500, cgst=0, sgst=0, batch="sb1",
    batch_no="sb1", mrp=10000, movement_item="soap")]), "OK")
expect("on hand", on_hand(con, "sb1"), 9)
expect("turn batches off, no children and no repack sources",
       w(con, "UPDATE items SET uses_batches = 0, version = 2 WHERE id = 'soap'"), "OK")
expect("now sell two at the item level", push(con, "bs2", [line(1, "soap", qty=2, total=19000, taxable=19000)]),
       "OK")
expect("the batch still holds 9 and nothing reaches it again", on_hand(con, "sb1"), 9)
expect("its price set is still open but the item no longer reads batches", con.execute(
    "SELECT COUNT(*) FROM item_prices WHERE item_id = 'soap' AND batch_id = 'sb1' AND valid_to IS NULL")
    .fetchone()[0], 1)

print("\n" + "=" * 78)
print("G. A stock-bearing line with no stock link: accepted, nothing deducted")
print("=" * 78)
con = fresh()
item(con, "coffee", dimension="mass", unit="g", batched=1)
item(con, "coffee_1kg", batched=0, parent="coffee", content=1000)
batch(con, "cf1", "coffee", 200000, expiry="2026-09-30")   # expired, the only sack
move(con, "rcf1", "cf1", "in", 20000, "receipt", "coffee")
expect("child pick with only an expired sack", con.execute(CHILD_PICK_QUERY, ("coffee", "2026-11-14")).fetchone(),
       None)
expect("the child is still billed, with no stock link at all",
       push(con, "bc1", [line(1, "coffee_1kg", qty=1, total=15000, taxable=15000)]), "OK")
expect("the sack is untouched", on_hand(con, "cf1"), 20000)
expect("child line stock columns", con.execute(
    "SELECT stock_item_id, stock_batch_id, stock_qty_base FROM bill_lines WHERE bill_id = 'bc1'").fetchone(),
    (None, None, None))
expect("movements for the bill", con.execute(
    "SELECT COUNT(*) FROM inventory_movements WHERE bill_id = 'bc1'").fetchone()[0], 0)

print("\nG2 the same on a batched item: a pack line with batch_id NULL")
con = fresh()
item(con, "rice1kg", batched=1, content=1000)
price(con, "rice1kg", 1, 1, 7500)          # an item-level set, no batch
batch(con, "pb1", "rice1kg", 6000, repack=None)
move(con, "rm1", "pb1", "in", 20, "receipt", "rice1kg")
expect("sell a pack without a batch_id", push(con, "bb1", [line(1, "rice1kg", qty=1, total=7500, taxable=7500)]),
       "OK")
expect("pack stock untouched", on_hand(con, "pb1"), 20)

print("\n" + "=" * 78)
print("H. A child's stock_qty_base is not tied to content_qty_base")
print("=" * 78)
con = fresh()
item(con, "coffee", dimension="mass", unit="g", batched=1)
item(con, "coffee_1kg", batched=0, parent="coffee", content=1000)
batch(con, "cf1", "coffee", 200000)
move(con, "rcf1", "cf1", "in", 20000, "receipt", "coffee")
expect("sell 2 x 1kg but record 1000 g taken",
       push(con, "bc2", [dict(no=1, item="coffee_1kg", qty=2, total=30000, taxable=30000, cgst=0, sgst=0,
                              stock_item="coffee", stock_batch="cf1", stock_qty=1000,
                              movement_item="coffee")]), "OK")
expect("sack after selling 2 kg", on_hand(con, "cf1"), 19000)
expect("line stock_qty_base vs qty x content", (con.execute(
    "SELECT stock_qty_base FROM bill_lines WHERE bill_id = 'bc2'").fetchone()[0], 2 * 1000), (1000, 2000))

print("\n" + "=" * 78)
print("I. Item master edits after the feature has been used")
print("=" * 78)
con = fresh()
item(con, "rice25", dimension="mass", unit="g", batched=1)
item(con, "rice1kg", batched=1, content=1000)
w(con, "INSERT INTO item_repack_sources (item_id, bulk_item_id, created_at) VALUES ('rice1kg','rice25',0)")
batch(con, "r_b1", "rice25", 150000)
move(con, "rr1", "r_b1", "in", 25000, "receipt", "rice25")
pack(con, "rp1", "r_b1", lines=[dict(item="rice1kg", qty=20, content=1000)])

print("I1 change the repack item's content after 20 packs exist")
expect("content 1000 -> 500", w(con, "UPDATE items SET content_qty_base = 500, version = 2 WHERE id = 'rice1kg'"),
       "OK")
expect("unpack the old batch at the item's current content (what a route would send)",
       unpack(con, "ru1", "r_b1", "k_main",
              [dict(item="rice1kg", batch_id="rp1:1:pack", qty=1, content=500)]), "repack_line_invalid")
expect("unpack at the content recorded on the pack line", unpack(
    con, "ru2", "r_b1", "k_main", [dict(item="rice1kg", batch_id="rp1:1:pack", qty=1, content=1000)]), "OK")

print("\nI2 delete a repack mapping: no trigger stops it")
expect("DELETE FROM item_repack_sources", w(con, "DELETE FROM item_repack_sources"), "OK")
expect("ReUse of a batch made under that mapping is now refused at the header", unpack(
    con, "ru3", "r_b1", "k_main", [dict(item="rice1kg", batch_id="rp1:1:pack", qty=1, content=1000)]),
    "repack_scope_invalid")
expect("map Rice 500g <- Rice 1kg: a chain D30 forbids", "/".join([
    item(con, "rice500", batched=1, content=500),
    w(con, "INSERT INTO item_repack_sources (item_id, bulk_item_id, created_at) "
           "VALUES ('rice500','rice1kg',0)")]), "OK/OK")
expect("Rice 1kg is now both a bulk and a pack item (a chain)", con.execute(
    "SELECT COUNT(*) FROM item_repack_sources s JOIN item_batches b ON b.item_id = s.bulk_item_id "
    "AND b.repack_id IS NOT NULL WHERE s.bulk_item_id = 'rice1kg'").fetchone()[0], 1)
expect("repack packs from packs: Rice 500g out of a Rice 1kg pack batch", "/".join([
    w(con, "INSERT INTO repacks (id, store_id, book_id, kind, bulk_batch_id, loss_qty_base, packed_at, "
         "created_at) VALUES ('rp9','s1','k_main','pack','rp1:1:pack',0,700,700)"),
    batch(con, "r5b", "rice500", 3000, repack="rp9"),
    w(con, "INSERT INTO repack_lines (repack_id, line_no, item_id, batch_id, qty_base, content_qty_base) "
         "VALUES ('rp9',1,'rice500','r5b',2,500)"),
    str(on_hand(con, "rp1:1:pack"))]), "OK/OK/OK/-981")
print("  -> 2 x 500 g are taken as 1000 *packs* from a batch holding 19 packs: the bulk side is")
print("     always deducted in base units, so a pack item used as a bulk is off by its content (1000x)")

print("\nI3 change a bulk's base unit after 25 kg of stock and a repack")
con = fresh()
item(con, "rice25", dimension="mass", unit="g", batched=1)
item(con, "rice1kg", batched=1, content=1000)
w(con, "INSERT INTO item_repack_sources (item_id, bulk_item_id, created_at) VALUES ('rice1kg','rice25',0)")
batch(con, "r_b1", "rice25", 150000)
move(con, "rr1", "r_b1", "in", 25000, "receipt", "rice25")
pack(con, "rp1", "r_b1", lines=[dict(item="rice1kg", qty=20, content=1000)])
expect("base_unit g -> kg, version bumped",
       w(con, "UPDATE items SET base_unit = 'kg', version = 2 WHERE id = 'rice25'"), "OK")
expect("5000 base units that were grams are now read as 5000 kg", on_hand(con, "r_b1"), 5000)


print("\n" + "=" * 78)
print("J. The child line's batch_id NULL and the D26 price check")
print("=" * 78)
con = fresh()
item(con, "coffee", dimension="mass", unit="g", batched=1)
item(con, "coffee_1kg", batched=0, parent="coffee", content=1000)
batch(con, "cf1", "coffee", 200000)
move(con, "rcf1", "cf1", "in", 20000, "receipt", "coffee")
expect("parent batch price set at version 2", price(con, "coffee", 1, 2, 100000, bid="cf1"), "OK")
expect("child item-level set at version 3", price(con, "coffee_1kg", 1, 3, 15000), "OK")
D26 = """SELECT price_minor FROM item_prices
         WHERE item_id = ?1 AND IFNULL(store_id,'') = IFNULL(?2,'') AND version = ?3"""
expect("sell the child", push(con, "bc3", [dict(
    no=1, item="coffee_1kg", qty=1, total=15000, taxable=15000, cgst=0, sgst=0, ver=3,
    stock_item="coffee", stock_batch="cf1", stock_qty=1000, movement_item="coffee")]), "OK")
expect("the line", con.execute(
    "SELECT batch_id, price_version, stock_item_id, stock_batch_id, stock_qty_base "
    "FROM bill_lines WHERE bill_id = 'bc3'").fetchone(), (None, 3, "coffee", "cf1", 1000))
expect("D26 lookup for the child line finds the child's own set",
       con.execute(D26, ("coffee_1kg", "s1", 3)).fetchall(), [(15000,)])
expect("the parent's set is not reachable from the child line",
       con.execute(D26, ("coffee", "s1", 3)).fetchall(), [])
expect("a child line that carries a bogus batch_id is still accepted", push(con, "bc4", [dict(
    no=1, item="coffee_1kg", qty=1, total=15000, taxable=15000, cgst=0, sgst=0, ver=3, batch="nonsense",
    stock_item="coffee", stock_batch="cf1", stock_qty=1000, movement_item="coffee")]), "OK")
expect("its price check still passes", con.execute(D26, ("coffee_1kg", "s1", 3)).fetchall(), [(15000,)])

print("\n" + "=" * 78)
print("K. Two pack lines of one item in one repack need two price versions")
print("=" * 78)
con = fresh()
item(con, "rice25", dimension="mass", unit="g", batched=1)
item(con, "rice1kg", batched=1, content=1000)
w(con, "INSERT INTO item_repack_sources (item_id, bulk_item_id, created_at) VALUES ('rice1kg','rice25',0)")
batch(con, "r_b1", "rice25", 150000)
move(con, "rr1", "r_b1", "in", 25000, "receipt", "rice25")
expect("two lines, same item, same next version", pack(con, "rp2", "r_b1", lines=[
    dict(item="rice1kg", qty=10, content=1000, batch_id="b1", version=1),
    dict(item="rice1kg", qty=9, content=1000, batch_id="b2", version=1)]), "price_version_scope")
expect("versions 1 and 2", pack(con, "rp3", "r_b1", lines=[
    dict(item="rice1kg", qty=10, content=1000, batch_id="b3", version=1),
    dict(item="rice1kg", qty=9, content=1000, batch_id="b4", version=2)]), "OK")

print()
print("OK" if failures == 0 else f"{failures} case(s) differ from the expected value")
sys.exit(1 if failures else 0)
