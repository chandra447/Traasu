#!/usr/bin/env python3
"""Catalog-slice repros against the real DDL (docs/d1-schema.sql + migrations 0002-0008).

Each case prints what the schema ACCEPTS or REFUSES. "ACCEPTED" on a bad state is the
evidence; "error: <msg>" on a good state is the evidence for the opposite.
"""
import pathlib, sqlite3, json

DOCS = pathlib.Path(__file__).resolve().parents[2]
con = sqlite3.connect(":memory:", isolation_level=None)
con.execute("PRAGMA foreign_keys = ON")
con.executescript((DOCS / "d1-schema.sql").read_text())
for m in sorted((DOCS / "migrations").glob("*.sql")):
    con.executescript(m.read_text())


def w(sql, *args):
    try:
        con.execute(sql, args)
        return "ACCEPTED"
    except sqlite3.Error as e:
        return f"error: {e}"


def q(sql, *args):
    return con.execute(sql, args).fetchall()


print("=" * 78)
print("SETUP")
print("=" * 78)
w("INSERT INTO stores (id,name,created_at) VALUES ('s1','Main',0),('s2','Branch',0)")
w("INSERT INTO terminals (id,store_id,name,created_at) VALUES ('t1','s1','Counter 1',0)")
w("INSERT INTO users (id,name,role,created_at) VALUES ('u1','Cashier','cashier',0)")
w("INSERT INTO staff_sessions (id,user_id,store_id,terminal_id,auth_method,started_at) VALUES ('ss1','u1','s1','t1','pin_offline',1000)")
w("INSERT INTO books (id,store_id,name,priority,created_at,updated_at) VALUES ('k_main','s1','Main',1,0,0)")
print("units:", q("SELECT unit,unit_group,factor_to_base FROM units"))

# ---------------------------------------------------------------- C1
print("\n" + "=" * 78)
print("C1  dimension / base_unit / default_sales_unit are three independent FKs")
print("=" * 78)
ITEM = """INSERT INTO items (id,name,dimension,pricing_mode,base_unit,default_sales_unit,updated_at)
          VALUES (?,?,?,?,?,?,0)"""
print("mass item, base_unit=pcs        :", w(ITEM, "c1", "Rice A", "mass", "fixed_only", "pcs", "pcs"))
print("mass item, base_unit=kg         :", w(ITEM, "c2", "Rice B", "mass", "fixed_only", "kg", "kg"))
print('  make c2 batched:', w("UPDATE items SET uses_batches=1, version=2 WHERE id='c2'"))
print("mass item, sales_unit=ml        :", w(ITEM, "c3", "Rice C", "mass", "fixed_only", "g", "ml"))
print("count item, base_unit=g         :", w(ITEM, "c4", "Eggs", "count", "fixed_only", "g", "g"))
print("dimension='litre' (not an enum) :", w(ITEM, "c5", "Milk", "litre", "fixed_only", "ml", "ml"))
print("\nconsequence: a child of c2 with content_qty_base=1000 (one 'kg' pack) takes")
print("1000 base units of an item whose base unit is kg = 1 tonne per pack:")
print("  child insert:", w("""INSERT INTO items (id,name,dimension,pricing_mode,base_unit,default_sales_unit,
                              parent_item_id,content_qty_base,uses_batches,updated_at)
                          VALUES ('c6','Rice B 1kg pack','mass','fixed_only','kg','kg','c2',1000,0,0)"""))
print("  -> stock taken per pack:", q("SELECT content_qty_base FROM items WHERE id='c6'")[0][0], "kg")

# ---------------------------------------------------------------- C2
print("\n" + "=" * 78)
print("C2  base_unit and dimension can change after bills exist (no DDL guard)")
print("=" * 78)
w(ITEM, "sale", "Sugar", "mass", "fixed_only", "g", "kg")


def push(bill_id, item_id, qty, version=1, seq=[0]):
    seq[0] += 1
    con.execute("BEGIN")
    try:
        con.execute("PRAGMA defer_foreign_keys = on")
        con.execute("""INSERT INTO bill_lines (bill_id,line_no,item_id,item_name,dimension,qty_base,price_version,
                       price_source,list_price_minor,price_minor,price_basis_qty_base,list_amount_minor,discount_minor,
                       price_breakdown,line_total_minor,taxable_minor,tax_rate_bp,cgst_minor,sgst_minor,cess_minor,
                       igst_minor)
                       VALUES (?,1,?,'x','mass',?,1,'list',1000,1000,1000,1000,0,'{}',1000,1000,0,0,0,0,0)""",
                   (bill_id, item_id, qty))
        con.execute("""INSERT INTO bills (id,store_id,terminal_id,user_id,auth_method,business_date,local_seq,
                       display_no,total_minor,subtotal_minor,tax_minor,rounding_minor,created_at,staff_session_id)
                       VALUES (?,'s1','t1','u1','pin_offline','2026-11-14',?,'d',1000,1000,0,0,1500,'ss1')""",
                   (bill_id, seq[0]))
        con.execute("COMMIT")
        return "ACCEPTED"
    except sqlite3.Error as e:
        con.execute("ROLLBACK")
        return f"error: {e}"


print("bill 1, 1500 g:", push("b1", "sale", 1500))
print("admin flips base_unit g->kg at version 2:",
      w("UPDATE items SET base_unit='kg', default_sales_unit='g', version=2 WHERE id='sale'"))
print("bill 2, '2' base units:", push("b2", "sale", 2))
print("sales_daily qty_base for the item:", q("SELECT qty_base FROM sales_daily WHERE item_id='sale'"))
print("  (1500 g + 2 kg reported as 1502 of the same unit)")

# ---------------------------------------------------------------- C3
print("\n" + "=" * 78)
print("C3  MRP lives in three places and price > MRP is storable at every level")
print("=" * 78)
w(ITEM, "m1", "Soap", "count", "fixed_only", "pcs", "pcs")
print("set items.mrp_minor=5000 (v2)  :", w("UPDATE items SET mrp_minor=5000, version=2 WHERE id='m1'"))
print("item mrp 5000, tier price 6000:",
      w("INSERT INTO item_prices (item_id,qty_base,price_minor,version,valid_from,created_at) VALUES ('m1',1,6000,1,1000,1000)"))
w(ITEM, "m3", "Tea", "count", "fixed_only", "pcs", "pcs")
print("tier price 4000, tier mrp 3000:",
      w("INSERT INTO item_prices (item_id,qty_base,price_minor,mrp_minor,version,valid_from,created_at) VALUES ('m3',1,4000,3000,1,1000,1000)"))
w("""INSERT INTO items (id,name,dimension,pricing_mode,base_unit,default_sales_unit,uses_batches,mrp_minor,updated_at)
     VALUES ('m2','Soap batched','count','fixed_only','pcs','pcs',1,5000,0)""")
w("INSERT INTO item_batches (id,item_id,store_id,book_id,batch_no,mrp_minor,received_at,updated_at) VALUES ('bt1','m2','s1','k_main','APR',5000,1000,0)")
print("batch mrp 5000, batch price 6000:",
      w("INSERT INTO item_prices (item_id,store_id,batch_id,qty_base,price_minor,version,valid_from,created_at) VALUES ('m2','s1','bt1',1,6000,1,1000,1000)"))
print("item mrp_minor = 0 (import of a blank GoFrugal MRP):",
      w("UPDATE items SET mrp_minor=0, version=3 WHERE id='m2'"))
print("\nwhat the three MRPs say for m1 now:")
print("  items.mrp_minor   :", q("SELECT mrp_minor FROM items WHERE id='m1'")[0][0])
print("  tier 1 mrp/price  :", q("SELECT IFNULL(mrp_minor,'NULL'),price_minor FROM item_prices WHERE item_id='m1' AND qty_base=1")[0])
print("  m3 tier mrp/price :", q("SELECT IFNULL(mrp_minor,'NULL'),price_minor FROM item_prices WHERE item_id='m3'")[0])

# ---------------------------------------------------------------- C4
print("\n" + "=" * 78)
print("C4  a dated promo window: legal only in one write order, and no route asks for it")
print("=" * 78)
w(ITEM, "d1", "Atta", "mass", "fixed_only", "g", "kg")
PR = """INSERT INTO item_prices (item_id,qty_base,price_minor,version,valid_from,valid_to,created_at)
        VALUES ('d1',1000,?,?,?,?,?)"""
print("current price v7 open from 1000 :", w(PR, 4000, 7, 1000, None, 1000))
print("promo v8 3500 for [2000,3000)  :", w(PR, 3500, 8, 2000, 3000, 2000))
print("  -> refused while the current row is open (trg_item_prices_revision_coherent)")

SELECT = """SELECT price_minor, version FROM item_prices
            WHERE item_id='d1' AND qty_base=1000 AND (store_id IS NULL)
              AND valid_from <= ? AND (valid_to IS NULL OR valid_to > ?)
            ORDER BY valid_from DESC LIMIT 1"""
print("\n  the only legal order: schedule the whole future, then close nothing twice")
print("  1. close v7 at the promo start :", w("UPDATE item_prices SET valid_to=2000 WHERE item_id='d1' AND version=7"))
print("  2. promo v8 [2000,3000)        :", w(PR, 3500, 8, 2000, 3000, 2000))
print("  3. post-promo v9 from 3000     :", w(PR, 4000, 9, 3000, None, 2000))
print("  price at t=1500 (today, v7)    :", q(SELECT, 1500, 1500))
print("  price at t=2500 (promo, v8)    :", q(SELECT, 2500, 2500))
print("  price at t=3500 (revert, v9)   :", q(SELECT, 3500, 3500))
print("  so the DDL can hold a window, but:")
print("   * PUT /v1/admin/items/:id/prices takes {store_key, batch_id, base_version, prices[]}")
print("     and no valid_from/valid_to, so no route can create one; D26 fixes valid_from")
print("     to the server clock.")
print("   * a terminal that is offline when the promo starts keeps billing v7 (4000).")
print("     The dated row is exactly what would let it switch by itself; there is no")
print("     route to create one, so the promo is missed until somebody pulls.")

# ---------------------------------------------------------------- C5
print("\n" + "=" * 78)
print("C5  PUT prices[] = [] leaves an ACTIVE item with no price at all")
print("=" * 78)
print("close every open row of m1  :", w("UPDATE item_prices SET valid_to=1001 WHERE item_id='m1' AND valid_to IS NULL"))
print("rows for m1 that are valid at t=1500:",
      q("""SELECT price_minor FROM item_prices WHERE item_id='m1' AND qty_base=1
           AND valid_from <= 1500 AND (valid_to IS NULL OR valid_to > 1500)"""))
print("m1 still active:", q("SELECT active,version FROM items WHERE id='m1'"))
print("   -> the register has an item it cannot price; the route documents")
print("      'an empty prices[] closes that set' as a supported call.")

# ---------------------------------------------------------------- C6
print("\n" + "=" * 78)
print("C6  item_prices rows are mutable and deletable: a version's money can be rewritten")
print("=" * 78)
print("before: v1 money for m1 =", q("SELECT price_minor,mrp_minor,valid_from FROM item_prices WHERE item_id='m1' AND version=1"))
print("UPDATE price_minor=1        :", w("UPDATE item_prices SET price_minor=1 WHERE item_id='m1' AND version=1"))
print("UPDATE valid_from=9999      :", w("UPDATE item_prices SET valid_from=9999 WHERE item_id='m1' AND version=1"))
print("after :", q("SELECT price_minor,mrp_minor,valid_from FROM item_prices WHERE item_id='m1' AND version=1"))
print("DELETE the whole v1 row     :", w("DELETE FROM item_prices WHERE item_id='m1' AND version=1"))
print("rows left for m1:", q("SELECT version,qty_base,price_minor FROM item_prices WHERE item_id='m1'"))
print("  D26: a bill line naming v1 now resolves to 'not found' -> price_mismatch on a sale")
print("  the shop really made, and no trigger fired on any of the three writes.")

# ---------------------------------------------------------------- C7
print("\n" + "=" * 78)
print("C7  weighing-scale barcodes: one fixed alias == one fixed quantity")
print("=" * 78)
w(ITEM, "sc", "Tomato", "mass", "linear", "g", "kg")
IDN = """INSERT INTO item_identifiers (id,store_id,alias,kind,item_id,qty_base,active,updated_at)
         VALUES (?,?,?,?,?,?,1,0)"""
print("label AA00100450WT as alias :", w(IDN, "i1", "s1", "AA00100450WT", "plu", "sc", 450))
print("label AA00100475WT as alias :", w(IDN, "i2", "s1", "AA00100475WT", "plu", "sc", 475))
print("scan of the 450 g label     :", q("SELECT item_id,qty_base FROM item_identifiers WHERE store_id='s1' AND alias='AA00100450WT' AND active=1"))
print("scan of a 460 g label       :", q("SELECT item_id,qty_base FROM item_identifiers WHERE store_id='s1' AND alias='AA00100460WT' AND active=1"))
print("columns available to parse a label:", [r[1] for r in q("PRAGMA table_info(item_identifiers)")])

print("\nduplicate EAN for two different items in two stores (one label, two products):")
print("  item A at s1:", w(IDN, "i3", "s1", "8901234567890", "ean", "m1", 1))
print("  item B at s2:", w(IDN, "i4", "s2", "8901234567890", "ean", "d1", 1))
print("  same store, two items (correctly refused):", w(IDN, "i5", "s1", "8901234567890", "ean", "sc", 1))

# ---------------------------------------------------------------- C8
print("\n" + "=" * 78)
print("C8  price_source has no value for 'priced by the scale label'")
print("=" * 78)


def push_source(src, override_by=None, bill="b9", item="m1", seqno=[100]):
    seqno[0] += 1
    con.execute("BEGIN")
    try:
        con.execute("PRAGMA defer_foreign_keys = on")
        con.execute("""INSERT INTO bill_lines (bill_id,line_no,item_id,item_name,dimension,qty_base,price_version,
                        price_source,list_price_minor,price_minor,price_basis_qty_base,list_amount_minor,discount_minor,
                        price_breakdown,line_total_minor,taxable_minor,tax_rate_bp,cgst_minor,sgst_minor,cess_minor,
                        igst_minor,override_by,override_reason)
                        VALUES (?,1,?,'x','count',1,1,?,1000,1000,1,1000,0,'{}',1000,1000,0,0,0,0,0,?,'other')""",
                     (bill, item, src, override_by))
        con.execute("""INSERT INTO bills (id,store_id,terminal_id,user_id,auth_method,business_date,local_seq,
                        display_no,total_minor,subtotal_minor,tax_minor,rounding_minor,created_at,staff_session_id)
                        VALUES (?,'s1','t1','u1','pin_offline','2026-11-14',?,'d',1000,1000,0,0,1500,'ss1')""",
                     (bill, seqno[0]))
        con.execute("COMMIT")
        return "ACCEPTED"
    except sqlite3.Error as e:
        con.execute("ROLLBACK")
        return f"error: {e}"


print("price_source='label'                  :", push_source("label"))
print("price_source='manual', no override_by :", push_source("manual"))
print("price_source='manual', override_by=u1 :", push_source("manual", "u1"))
print("  (a label price must be filed as a cashier override, so every scale-priced")
print("   line lands in the override report and needs an override reason.)")

# ---------------------------------------------------------------- C9
print("\n" + "=" * 78)
print("C9  tax_rates has no history: a rate change cannot be verified against old bills")
print("=" * 78)
print("columns :", [r[1] for r in q("PRAGMA table_info(tax_rates)")])
print("UPDATE GST 5% -> 12%:", w("UPDATE tax_rates SET rate_bp=1200 WHERE name='GST 5%'"))
print("row now              :", q("SELECT name,rate_bp FROM tax_rates WHERE name='GST 5%'"))
print("catalog_changes row for a tax_rate carries version:",
      q("SELECT entity,version FROM catalog_changes WHERE entity='tax_rate' LIMIT 1"))
print("  a bill line pushed with tax_rate_bp=500 after this cannot be checked:")
print("  the server has no row that says '500 until T, 1200 afterwards'.")

# ---------------------------------------------------------------- C10
print("\n" + "=" * 78)
print("C10 the terminal's cursor table in api-routes is not in the DDL")
print("=" * 78)
print("api-routes.md:637 reads sync_state; table exists?",
      q("SELECT name FROM sqlite_master WHERE type='table' AND name='sync_state'"))
print("tables in the schema:", sorted(r[0] for r in q("SELECT name FROM sqlite_master WHERE type='table'")))

# ---------------------------------------------------------------- C11
print("\n" + "=" * 78)
print("C11 catalog_rebuild can be 'complete' with no end_seq")
print("=" * 78)
print("insert (gen=1,start=500,end=NULL,complete=1):",
      w("INSERT INTO catalog_rebuild (generation,start_seq,end_seq,complete,started_at) VALUES (1,500,NULL,1,0)"))
print("row:", q("SELECT * FROM catalog_rebuild"))

# ---------------------------------------------------------------- C12
print("\n" + "=" * 78)
print("C12 no external/source code on items: a GoFrugal re-import cannot be idempotent")
print("=" * 78)
print("items columns:", [r[1] for r in q("PRAGMA table_info(items)")])
print("two 'TATA SALT 1KG' rows:", w(ITEM, "imp1", "TATA SALT 1KG", "count", "fixed_only", "pcs", "pcs"))
print("                        :", w(ITEM, "imp2", "TATA SALT 1KG", "count", "fixed_only", "pcs", "pcs"))
print("rows with that name:", q("SELECT COUNT(*) FROM items WHERE name='TATA SALT 1KG'"))

# ---------------------------------------------------------------- C13
print("\n" + "=" * 78)
print("C13 item_price delta payload = the item's ENTIRE price history, every time")
print("=" * 78)
w(ITEM, "py", "Bulk", "mass", "tiered", "g", "kg")
rows = []
NOW = 26000
for v in range(1, 401):
    vf = 1000 + (v - 1) * 60
    vt = None if v == 400 else vf + 60
    for qty, price in ((1000, 4000 + v), (500, 2500 + v), (250, 1300 + v)):
        rows.append(("py", qty, price, v, vf, vt, vf))
con.executemany("""INSERT INTO item_prices (item_id,qty_base,price_minor,version,valid_from,valid_to,created_at)
                   VALUES (?,?,?,?,?,?,?)""", rows)
n = q("SELECT COUNT(*) FROM item_prices WHERE item_id='py'")[0][0]
blob = q("""SELECT json_group_array(json_object('item_id',item_id,'store_key',IFNULL(store_id,''),
             'qty_base',qty_base,'version',version,'price_minor',price_minor,
             'valid_from',valid_from,'valid_to',valid_to))
           FROM item_prices WHERE item_id='py'""")[0][0]
print(f"400 daily revisions x 3 tiers = {n} rows; one item_price document = {len(blob)} bytes")
print(f"20 such items edited in one page = {20*len(blob)} bytes (page cap is 8 MB; limit 500 rows)")
print("   every later edit to ANY of them re-sends the whole history again.")
print("   rows that are actually usable at a later t (open + future windows):",
      q(f"""SELECT COUNT(*) FROM item_prices WHERE item_id='py'
             AND (valid_to IS NULL OR valid_to > {NOW})""")[0][0])

# ---------------------------------------------------------------- C14
print("\n" + "=" * 78)
print("C14 what the version triggers DO protect (replay + concurrent admin edit)")
print("=" * 78)
w(ITEM, "conc", "Concurrent", "count", "fixed_only", "pcs", "pcs")
print("item v2, name A        :", w("UPDATE items SET name='A', version=2 WHERE id='conc'"))
print("item v2, name B        :", w("UPDATE items SET name='B', version=2 WHERE id='conc'"))
print("item v3, name B        :", w("UPDATE items SET name='B', version=3 WHERE id='conc'"))
w(ITEM, "div", "VersionScope", "count", "fixed_only", "pcs", "pcs")
P1 = """INSERT INTO item_prices (item_id,store_id,qty_base,price_minor,version,valid_from,created_at)
        VALUES ('div',?,1,3000,?,1000,1000)"""
print("price v1, default scope:", w(P1, None, 1))
print("price v1, scope s1     :", w(P1, "s1", 1))
print("price v2, default scope:", w(P1, None, 2))
print("  -> a version number means one scope's tier set on the server (good),")
print("     and an equal-version edit of an item is refused (good).")

# ---------------------------------------------------------------- C15
print("\n" + "=" * 78)
print("C15 item-name search has no index (register type-ahead)")
print("=" * 78)
for r in q("EXPLAIN QUERY PLAN SELECT id FROM items WHERE name LIKE 'TAM%' AND active=1"):
    print("  ", r)
print("alias lookup plan:")
for r in q("EXPLAIN QUERY PLAN SELECT item_id,qty_base FROM item_identifiers WHERE store_id='s1' AND alias='X' AND active=1"):
    print("  ", r)

# ---------------------------------------------------------------- C16
print("\n" + "=" * 78)
print("C16 import transform: paise from a decimal rupee price")
print("=" * 78)
for s in ("40.45", "0.45", "19.99", "100.10"):
    print(f"  {s:>7} -> int(float*100) = {int(float(s)*100):>6}   round = {round(float(s)*100):>6}")
print("  a per-gram rate from an export is not a pack price: 0.45/g at qty_base=1000 = 450.00, not 0.45 stored raw")

# ---------------------------------------------------------------- C17
print("\n" + "=" * 78)
print("C17 catalog_changes.entity is a closed CHECK vocabulary (D25 says it should not be)")
print("=" * 78)
print("insert entity='category':",
      w("""INSERT INTO catalog_changes (seq,entity,entity_id,op,version,changed_at)
           VALUES (9999,'category','x','upsert',NULL,0)"""))
print("insert entity='item_repack_source':",
      w("""INSERT INTO catalog_changes (seq,entity,entity_id,op,version,changed_at)
           VALUES (9998,'item_repack_source','x','upsert',NULL,0)"""))
print("entity vocabulary:", q("SELECT sql FROM sqlite_master WHERE name='catalog_changes'")[0][0].split("entity IN (")[1].split(")")[0])

# ---------------------------------------------------------------- C18
print("\n" + "=" * 78)
print("C18 identifier kind/format are not validated")
print("=" * 78)
IDN = """INSERT INTO item_identifiers (id,store_id,alias,kind,item_id,qty_base,active,updated_at)
         VALUES (?,?,?,?,?,?,1,0)"""
print("ean with 3 characters      :", w(IDN, "v1", "s1", "123", "ean", "m3", 1))
print("ean with a bad check digit :", w(IDN, "v2", "s1", "8901234567891", "ean", "m3", 1))
print("plu with 12 digits         :", w(IDN, "v3", "s1", "123456789012", "plu", "m3", 1))
print("alias ' A ' (untrimmed)    :", w(IDN, "v4", "s1", " A ", "alias", "m3", 1))

# ---------------------------------------------------------------- C19
print("\n" + "=" * 78)
print("C19 a deactivated item keeps its active barcode")
print("=" * 78)
print("deactivate m1 (v3)          :", w("UPDATE items SET active=0, version=3 WHERE id='m1'"))
print("m1 aliases still active     :", q("SELECT alias,active FROM item_identifiers WHERE item_id='m1'"))
print("scan still resolves m1      :", q("""SELECT i.item_id, i.alias, t.name, t.active
                                            FROM item_identifiers i JOIN items t ON t.id=i.item_id
                                            WHERE i.store_id='s1' AND i.alias='8901234567890' AND i.active=1"""))
print("  the scan path returns an inactive item unless every lookup also checks items.active.")
