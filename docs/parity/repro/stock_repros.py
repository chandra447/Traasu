#!/usr/bin/env python3
"""Stock/batch parity repros against the real DDL (read-only w.r.t. the repo)."""
import pathlib, sqlite3

DOCS = pathlib.Path(__file__).resolve().parents[2]


def mkdb():
    con = sqlite3.connect(":memory:", isolation_level=None)
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript((DOCS / "d1-schema.sql").read_text())
    for m in sorted((DOCS / "migrations").glob("*.sql")):
        con.executescript(m.read_text())
    con.executescript("""
    INSERT INTO stores (id, name, created_at) VALUES ('s1', 'S1', 0);
    INSERT INTO terminals (id, store_id, name, created_at) VALUES ('t1', 's1', 'T1', 0);
    INSERT INTO users (id, name, role, created_at) VALUES ('u1', 'Cashier', 'cashier', 0);
    INSERT INTO staff_sessions (id, user_id, store_id, terminal_id, auth_method, started_at)
      VALUES ('ss1', 'u1', 's1', 't1', 'pin_offline', 1000);
    INSERT INTO items (id, name, dimension, pricing_mode, base_unit, default_sales_unit, uses_batches, updated_at)
      VALUES ('soap', 'Soap', 'count', 'fixed_only', 'pcs', 'pcs', 1, 0),
             ('oil',  'Oil',  'count', 'fixed_only', 'pcs', 'pcs', 0, 0);
    INSERT INTO books (id, store_id, name, priority, created_at, updated_at)
      VALUES ('k_main', 's1', 'Main', 1, 0, 0), ('k_godown', 's1', 'Godown', 2, 0, 0);
    INSERT INTO item_batches (id, item_id, store_id, book_id, batch_no, mrp_minor, expiry_date, received_at, updated_at)
      VALUES ('b_apr', 'soap', 's1', 'k_main', 'APR-24', 10000, NULL, 100, 0),
             ('b_old', 'soap', 's1', 'k_main', 'JAN-24', 9000, '2026-01-01', 50, 0);
    INSERT INTO item_prices (item_id, store_id, batch_id, qty_base, version, price_minor, mrp_minor, valid_from, created_at)
      VALUES ('soap', 's1', 'b_apr', 1, 1, 10000, 10000, 100, 0),
             ('soap', 's1', 'b_old', 1, 2, 9000, 9000, 50, 0);
    INSERT INTO inventory_movements (id, item_id, bill_id, bill_line_no, direction, qty_base, occurred_at, created_at,
                                     batch_id, store_id, reason)
      VALUES ('r_apr', 'soap', NULL, NULL, 'in', 10, 100, 100, 'b_apr', 's1', 'receipt'),
             ('r_old', 'soap', NULL, NULL, 'in', 5, 50, 50, 'b_old', 's1', 'receipt');
    """)
    return con


def push(con, bill_id, lines, created_at=1500, date="2026-11-14"):
    """One bill as the Worker batches it: deferred FKs, lines, header last (QC §5)."""
    con.execute("BEGIN")
    try:
        con.execute("PRAGMA defer_foreign_keys = on")
        for l in lines:
            con.execute(
                """INSERT INTO bill_lines (bill_id, line_no, item_id, item_name, dimension, qty_base, price_version,
                     price_source, list_price_minor, price_minor, price_basis_qty_base, discount_minor, list_amount_minor,
                     price_breakdown, line_total_minor, taxable_minor, tax_rate_bp, cgst_minor, sgst_minor, cess_minor,
                     igst_minor, stock_item_id, stock_batch_id, stock_qty_base, batch_id, batch_no, mrp_minor)
                   VALUES (?, ?, ?, 'x', 'count', ?, 1, 'list', ?, ?, 1, 0, ?, '{}', ?, ?, 0, 0, 0, 0, 0,
                           NULL, NULL, NULL, ?, ?, ?)
                   ON CONFLICT (bill_id, line_no) DO NOTHING""",
                (bill_id, l["no"], l["item"], l.get("qty", 1), l.get("price", 100), l.get("price", 100),
                 l.get("price", 100), l.get("price", 100), l.get("price", 100),
                 l.get("batch"), l.get("batch_no"), l.get("mrp")))
        con.execute(
            """INSERT INTO bills (id, store_id, terminal_id, user_id, auth_method, business_date, local_seq, display_no,
                 total_minor, subtotal_minor, tax_minor, rounding_minor, created_at, staff_session_id)
               VALUES (?, 's1', 't1', 'u1', 'pin_offline', ?, ?, ?, ?, ?, 0, 0, ?, 'ss1')
               ON CONFLICT (id) DO NOTHING""",
            (bill_id, date, abs(hash(bill_id)) % 100000, f"d-{bill_id}",
             sum(l.get("price", 100) for l in lines), sum(l.get("price", 100) for l in lines), created_at))
        con.execute("COMMIT")
        return "OK"
    except sqlite3.Error as e:
        con.execute("ROLLBACK")
        return str(e)


def move(con, mid, item, direction, qty, batch, reason="sale", bill=None, line_no=None):
    try:
        con.execute(
            """INSERT INTO inventory_movements (id, item_id, bill_id, bill_line_no, direction, qty_base, occurred_at,
                 created_at, batch_id, store_id, reason)
               VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?, 's1', ?)""",
            (mid, item, bill, line_no, direction, qty, batch, reason))
        return "OK"
    except sqlite3.Error as e:
        return str(e)


def tryx(con, sql, *a):
    try:
        con.execute(sql, a)
        return "OK"
    except sqlite3.Error as e:
        return str(e)


def on_hand(con, bid):
    r = con.execute("SELECT on_hand_qty_base FROM batch_stock WHERE batch_id = ?", (bid,)).fetchone()
    return "no row" if r is None else r[0]


PICKER = """
WITH live AS (
  SELECT b.id, b.batch_no, b.mrp_minor, b.expiry_date, b.received_at,
         k.priority, k.name AS book, IFNULL(s.on_hand_qty_base, 0) AS on_hand, p.price_minor
  FROM item_batches b
  JOIN books k ON k.id = b.book_id AND k.active = 1
  LEFT JOIN batch_stock s ON s.batch_id = b.id
  LEFT JOIN item_prices p ON p.item_id = b.item_id AND p.batch_id = b.id
                         AND p.qty_base = ?4 AND p.valid_to IS NULL
  WHERE b.item_id = ?1 AND b.store_id = ?2 AND b.retired_at IS NULL
    AND (b.expiry_date IS NULL OR b.expiry_date >= ?3)),
latest AS (SELECT * FROM live ORDER BY received_at DESC, id DESC LIMIT 1),
same AS (SELECT live.* FROM live, latest
         WHERE IFNULL(live.mrp_minor, -1) = IFNULL(latest.mrp_minor, -1)
           AND live.price_minor IS latest.price_minor),
pick AS (SELECT id FROM same ORDER BY on_hand > 0 DESC, priority, received_at DESC, id DESC LIMIT 1)
SELECT live.id, live.mrp_minor, live.on_hand, live.price_minor
FROM live
WHERE live.id = (SELECT id FROM pick) OR (live.on_hand > 0 AND live.id NOT IN (SELECT id FROM same))
ORDER BY live.id = (SELECT id FROM pick) DESC, live.priority, live.received_at DESC, live.id DESC
"""


def picker(con, item="soap", day="2026-11-14", tier=1):
    return con.execute(PICKER, (item, "s1", day, tier)).fetchall()


def head(t):
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


# ---------------------------------------------------------------- C1
head("C1  A bill line can get TWO sale movements; the bill then cannot be voided")
con = mkdb()
print("push bill b1 (soap, APR)                        :",
      push(con, "b1", [{"no": 1, "item": "soap", "batch": "b_apr", "batch_no": "APR-24", "mrp": 10000, "price": 10000}]))
print("movement b1:1 (the sale)                        :", move(con, "b1:1", "soap", "out", 1, "b_apr", bill="b1", line_no=1))
print("movement b1:1 again, different id (a re-push)   :", move(con, "b1:1-retry", "soap", "out", 1, "b_apr",
                                                                bill="b1", line_no=1))
print("  -> one sale of 1, stock taken twice; on_hand  :", on_hand(con, "b_apr"), "(was 10, should be 9)")
print("void the bill                                   :",
      tryx(con, "INSERT INTO bill_voids (id, bill_id, store_id, terminal_id, user_id, reason, created_at) "
                "VALUES ('v1', 'b1', 's1', 't1', 'u1', 'wrong pack', 4000)"))
print("  -> both restock rows would carry id 'v1:1', so the void is refused outright")

# ---------------------------------------------------------------- C1b
head("C1b The happy path: one sale movement, the void puts the pack back")
con = mkdb()
print("push b1 + movement                              :",
      push(con, "b1", [{"no": 1, "item": "soap", "batch": "b_apr", "batch_no": "APR-24", "mrp": 10000, "price": 10000}]),
      "/", move(con, "b1:1", "soap", "out", 1, "b_apr", bill="b1", line_no=1),
      "-> on_hand", on_hand(con, "b_apr"))
print("void                                            :",
      tryx(con, "INSERT INTO bill_voids (id, bill_id, store_id, terminal_id, user_id, reason, created_at) "
                "VALUES ('v1', 'b1', 's1', 't1', 'u1', 'wrong pack', 4000)"),
      "-> on_hand", on_hand(con, "b_apr"))

# ---------------------------------------------------------------- C2
head("C2  A line can name another item's batch, and that batch's stock moves")
con = mkdb()
print("bill b2: line 1 is OIL (unbatched), batch_id = b_apr (a SOAP batch):",
      push(con, "b2", [{"no": 1, "item": "oil", "batch": "b_apr", "batch_no": "APR-24", "mrp": 10000, "price": 10000}]))
print("movement for OIL out of soap's batch            :", move(con, "b2:1", "oil", "out", 3, "b_apr",
                                                                bill="b2", line_no=1))
print("  -> soap b_apr on_hand falls by a soap sale that never happened:", on_hand(con, "b_apr"))
print("  -> oil records no stock at all (oil has no batch):",
      con.execute("SELECT COUNT(*) FROM batch_stock WHERE batch_id IN "
                  "(SELECT id FROM item_batches WHERE item_id='oil')").fetchone()[0], "rows")

# ---------------------------------------------------------------- C3
head("C3  Unknown batch_id: accepted, no stock effect, nowhere to flag it")
con = mkdb()
print("bill b3 line batch_id 'no-such-batch'          :",
      push(con, "b3", [{"no": 1, "item": "soap", "batch": "no-such-batch", "batch_no": "X", "mrp": 10000,
                        "price": 10000}]))
print("movement out of 'no-such-batch'                 :", move(con, "b3:1", "soap", "out", 1, "no-such-batch",
                                                                bill="b3", line_no=1))
print("  -> batch_stock rows for it                   :",
      con.execute("SELECT COUNT(*) FROM batch_stock WHERE batch_id='no-such-batch'").fetchone()[0])
print("  -> stock_seq (a sale that moves no stock still bumps the cursor):",
      con.execute("SELECT last_seq FROM stock_seq").fetchone()[0])
print("  -> bill_lines columns available for a batch verdict:",
      [c for c in (r[1] for r in con.execute("PRAGMA table_info(bill_lines)"))
       if "batch" in c or "flag" in c or "unknown" in c])

# ---------------------------------------------------------------- C4
head("C4  An EXPIRED batch can be billed; nothing on the server flags it")
con = mkdb()
print("b_old expiry 2026-01-01, bill dated 2026-11-14 :",
      push(con, "b4", [{"no": 1, "item": "soap", "batch": "b_old", "batch_no": "JAN-24", "mrp": 9000, "price": 9000}]))
print("movement out of b_old                           :", move(con, "b4:1", "soap", "out", 2, "b_old",
                                                                bill="b4", line_no=1),
      "-> on_hand", on_hand(con, "b_old"))
print("  -> the register's picker for that day never offers b_old:", picker(con))

# ---------------------------------------------------------------- C5
head("C5  Price above MRP is accepted; MRP lives in two places that can disagree")
con = mkdb()
print("close APR's open price, then revise to 12,000 with MRP 10,000:",
      tryx(con, "UPDATE item_prices SET valid_to = 2000 WHERE batch_id='b_apr' AND valid_to IS NULL"), "/",
      tryx(con, "INSERT INTO item_prices (item_id, store_id, batch_id, qty_base, version, price_minor, mrp_minor, "
                "valid_from, created_at) VALUES ('soap','s1','b_apr',1,3,12000,10000,2000,0)"))
print("  -> (batch.mrp, price.price_minor, price.mrp_minor):",
      con.execute("SELECT b.mrp_minor, p.price_minor, p.mrp_minor FROM item_batches b JOIN item_prices p "
                  "ON p.batch_id = b.id AND p.valid_to IS NULL WHERE b.id='b_apr'").fetchall())
print("bill b5 charges 12,000 against a printed MRP of 10,000:",
      push(con, "b5", [{"no": 1, "item": "soap", "batch": "b_apr", "batch_no": "APR-24", "mrp": 10000,
                        "price": 12000}]),
      "/", move(con, "b5:1", "soap", "out", 1, "b_apr", bill="b5", line_no=1))
print("  -> stored line (price_minor, mrp_minor)      :",
      con.execute("SELECT price_minor, mrp_minor FROM bill_lines WHERE bill_id='b5'").fetchall())
print("  -> a price row whose own mrp_minor disagrees with its batch:",
      tryx(con, "UPDATE item_prices SET valid_to = 3000 WHERE batch_id='b_apr' AND valid_to IS NULL"), "/",
      tryx(con, "INSERT INTO item_prices (item_id, store_id, batch_id, qty_base, version, price_minor, mrp_minor, "
                "valid_from, created_at) VALUES ('soap','s1','b_apr',1,4,10000,13000,3000,0)"), "-> accepted")

# ---------------------------------------------------------------- C6
head("C6  A physical count is never stored; the delta is computed from whatever on_hand says now")
con = mkdb()
counted = con.execute("SELECT on_hand_qty_base FROM batch_stock WHERE batch_id='b_apr'").fetchone()[0]
print("book says 10; the count sheet is printed     :", counted)
print("staff count the shelf: 8 (2 were damaged)     : 8")
print("meanwhile a register pushes a sale of 2       :", move(con, "s9", "soap", "out", 2, "b_apr"), "-> on_hand",
      on_hand(con, "b_apr"))
print("admin enters counted = 8; the Worker reads current",
      con.execute("SELECT on_hand_qty_base FROM batch_stock WHERE batch_id='b_apr'").fetchone()[0],
      "and writes delta = 8 - 8 = 0")
print("  -> on_hand stays at 8, the 2 damaged packs are never written off")
print("  -> the counted number 8 exists in no table:",
      [r[1] for r in con.execute("PRAGMA table_info(inventory_movements)") if "count" in r[1]], "(no column)")
print("  -> a later recount cannot tell a count from a damage write-off: reason is free text in note")

# ---------------------------------------------------------------- C7
head("C7  No live batch = the item cannot be priced at all (the sale is blocked)")
con = mkdb()
print("retire b_apr; b_old expired on 2026-01-01      :",
      tryx(con, "UPDATE item_batches SET retired_at = 3000, version = 2 WHERE id='b_apr'"))
print("picker rows for 2026-11-14                      :", picker(con))
print("register price lookup for the item, batch NULL  :", con.execute(
    "SELECT price_minor, version FROM item_prices WHERE item_id='soap' AND qty_base=1 "
    "AND (store_id IS 's1' OR store_id IS NULL) AND valid_from <= 1500 AND (valid_to IS NULL OR valid_to > 1500) "
    "AND batch_id IS NULL ORDER BY store_id IS NULL, valid_from DESC LIMIT 1").fetchall())
print("  -> items.uses_batches = 1 with no live batch is a legal state; nothing requires a batch to exist")

# ---------------------------------------------------------------- C8
head("C8  Two batches at the same MRP and price: the older one is never offered")
con = mkdb()
print("M1 (day 1, 10 packs) and M2 (day 8, 10 packs), same MRP, same price:")
print(" create                                        :", "/".join([
    tryx(con, "INSERT INTO item_batches (id,item_id,store_id,book_id,batch_no,mrp_minor,received_at,updated_at) "
              "VALUES ('m1','soap','s1','k_main','M1',10000,100,0)"),
    tryx(con, "INSERT INTO item_batches (id,item_id,store_id,book_id,batch_no,mrp_minor,received_at,updated_at) "
              "VALUES ('m2','soap','s1','k_main','M2',10000,800,0)"),
    tryx(con, "INSERT INTO item_prices (item_id,store_id,batch_id,qty_base,version,price_minor,valid_from,created_at) "
              "VALUES ('soap','s1','m1',1,4,10000,100,0)"),
    tryx(con, "INSERT INTO item_prices (item_id,store_id,batch_id,qty_base,version,price_minor,valid_from,created_at) "
              "VALUES ('soap','s1','m2',1,5,10000,800,0)"),
    move(con, "i_m1", "soap", "in", 10, "m1", reason="receipt"),
    move(con, "i_m2", "soap", "in", 10, "m2", reason="receipt")]))
print(" picker: the cashier is offered                :", picker(con))
print("  -> M1 is neither the pick nor a prompt: 'other stock' means another MRP or price")
print(" sell all 10 of M2                             :", move(con, "s_m2", "soap", "out", 10, "m2"))
print(" picker now                                    :", picker(con))
print("  -> M1 surfaces only when M2 reaches zero, i.e. at the end of its life")

# ---------------------------------------------------------------- C9
head("C9  Archival: sale movements go, batch_stock stays, and an archived void cannot restock")
con = mkdb()
print("push b7 + movement of 3                        :",
      push(con, "b7", [{"no": 1, "item": "soap", "batch": "b_apr", "batch_no": "APR-24", "mrp": 10000,
                        "qty": 3, "price": 10000}]),
      "/", move(con, "b7:1", "soap", "out", 3, "b_apr", bill="b7", line_no=1), "-> on_hand", on_hand(con, "b_apr"))
print("archive that month                              :",
      tryx(con, "UPDATE schema_meta SET archive_before='2026-12-01'"), "/",
      tryx(con, "DELETE FROM bills WHERE id='b7'"), "-> movements left:",
      con.execute("SELECT COUNT(*) FROM inventory_movements WHERE bill_id='b7'").fetchone()[0],
      ", on_hand:", on_hand(con, "b_apr"))
print("a void arrives for the archived bill            :",
      tryx(con, "INSERT INTO bill_voids (id,bill_id,store_id,terminal_id,user_id,reason,created_at) "
                "VALUES ('v7','b7','s1','t1','u1','returned',5000)"))
print("  -> refused ('void_store_mismatch'): the 3 packs that came back have no record anywhere")

# ---------------------------------------------------------------- C10
head("C10 Two registers sell the last packs offline: negative stock, no block (by design)")
con = mkdb()
print("R1 sells 6, R2 sells 6, both from b_apr (10 in stock):",
      move(con, "r1_s", "soap", "out", 6, "b_apr"), "/", move(con, "r2_s", "soap", "out", 6, "b_apr"),
      "-> on_hand", on_hand(con, "b_apr"), "(accepted; the owner fixes it with an adjustment)")
print("the pull after each sale                        :",
      con.execute("SELECT batch_id, on_hand_qty_base, seq FROM batch_stock WHERE store_id='s1' ORDER BY seq").fetchall())
print("  -> one row per batch carrying the latest seq, not one per sale")

# ---------------------------------------------------------------- C11
head("C11 An impossible expiry date is accepted and never expires")
con = mkdb()
print("2026-13-45 on a batch                           :",
      tryx(con, "INSERT INTO item_batches (id,item_id,store_id,book_id,batch_no,mrp_minor,expiry_date,received_at,updated_at) "
                "VALUES ('x','soap','s1','k_main','X',10000,'2026-13-45',0,0)"))
print("sqlite date() of it                             :", con.execute("SELECT date('2026-13-45')").fetchone()[0])
print("2026-02-31                                      :",
      tryx(con, "INSERT INTO item_batches (id,item_id,store_id,book_id,batch_no,mrp_minor,expiry_date,received_at,updated_at) "
                "VALUES ('y','soap','s1','k_main','Y',10000,'2026-02-31',0,0)"),
      "-> date():", con.execute("SELECT date('2026-02-31')").fetchone()[0])
print("the picker treats 2026-13-45 as future          :",
      con.execute("SELECT id FROM item_batches WHERE expiry_date >= '2026-11-14'").fetchall())

# ---------------------------------------------------------------- C12
head("C12 One stock receipt can write the same batch twice; an empty receipt is accepted")
con = mkdb()
print("receipt pr1                                     :",
      tryx(con, "INSERT INTO stock_receipts (id,store_id,book_id,reference,received_at) VALUES ('pr1','s1','k_main','INV-1',10)"))
print("batch b1                                        :",
      tryx(con, "INSERT INTO item_batches (id,item_id,store_id,book_id,batch_no,mrp_minor,received_at,updated_at) "
                "VALUES ('b1','soap','s1','k_main','B1',10000,10,0)"))
print("receipt movement, line 1                        :",
      move(con, "m1", "soap", "in", 10, "b1", reason="receipt"))
print("receipt movement, line 2, same batch            :",
      move(con, "m2", "soap", "in", 10, "b1", reason="receipt"), "-> on_hand", on_hand(con, "b1"))
print("a receipt with no lines at all                  :",
      tryx(con, "INSERT INTO stock_receipts (id,store_id,book_id,reference,received_at) VALUES ('pr2','s1','k_main','INV-2',11)"),
      "(accepted, no trigger)")

# ---------------------------------------------------------------- C13
head("C13 Deactivating the store's last active book unbills every batched item")
con = mkdb()
print("live batches while Main is active              :", picker(con))
print("deactivate the only book                        :",
      tryx(con, "UPDATE books SET active = 0, version = 2 WHERE id='k_main'"))
print("live batches now                                :", picker(con), "-> no price, no batch, no sale")

# ---------------------------------------------------------------- C14
head("C14 The transfer shape D28 describes cannot be built")
con = mkdb()
print("a second batch 'APR-24' in the Godown           :",
      tryx(con, "INSERT INTO item_batches (id,item_id,store_id,book_id,batch_no,mrp_minor,received_at,updated_at) "
                "VALUES ('b_copy','soap','s1','k_godown','APR-24',10000,100,0)"))
print("edit b_apr's book_id to the Godown              :",
      tryx(con, "UPDATE item_batches SET book_id='k_godown', version=2 WHERE id='b_apr'"))

print("\nDone.")
