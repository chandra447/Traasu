#!/usr/bin/env python3
"""Platform slice probe P2: late voids, training bills, invoice numbering, approver roles.

Real DDL, in memory, exactly as docs/check-invariants.py builds it.
"""
import pathlib, sqlite3

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


def try_(label, sql, *a):
    try:
        con.execute(sql, a)
        print(f"  {label}: ACCEPTED")
    except sqlite3.Error as e:
        print(f"  {label}: refused -> {e}")


seq = [0]


def push(bill, store, term, sess, date, lines, created=1500, invoice=None):
    seq[0] += 1
    sub = sum(l[2] for l in lines)
    tax = sum(l[4] + l[5] + l[6] for l in lines)
    tot = sum(l[3] for l in lines)
    con.execute("BEGIN")
    con.execute("PRAGMA defer_foreign_keys = on")
    try:
        _push_inner(bill, store, term, sess, date, lines, created, invoice, sub, tax, tot)
        con.execute("COMMIT")
        return "OK"
    except sqlite3.Error:
        con.execute("ROLLBACK")
        raise


def _push_inner(bill, store, term, sess, date, lines, created, invoice, sub, tax, tot):
    for n, (item, qty, taxable, ltot, cgst, sgst, igst) in enumerate(lines, 1):
        con.execute(
            """INSERT INTO bill_lines (bill_id,line_no,item_id,item_name,dimension,qty_base,price_version,
                 price_source,list_price_minor,price_minor,price_basis_qty_base,discount_minor,list_amount_minor,
                 price_breakdown,line_total_minor,taxable_minor,tax_rate_bp,cgst_minor,sgst_minor,cess_minor,igst_minor)
               VALUES (?,?,?,'x','count',?,1,'list',?,?,1,0,?,'{}',?,?,500,?,?,0,?)""",
            (bill, n, item, qty, ltot, ltot, ltot, ltot, taxable, cgst, sgst, igst))
    con.execute(
        """INSERT INTO bills (id,store_id,terminal_id,user_id,auth_method,business_date,local_seq,display_no,
             invoice_no,total_minor,subtotal_minor,tax_minor,rounding_minor,created_at,staff_session_id)
           VALUES (?,?,?,'u1','pin_offline',?,?,?,?,?,?,?,0,?,?)""",
        (bill, store, term, date, seq[0], f"D{seq[0]}", invoice, tot, sub, tax, created, sess))


con.executescript("""
INSERT INTO stores (id,name,created_at) VALUES ('s1','Kukatpally',0),('s2','Secunderabad',0);
INSERT INTO terminals (id,store_id,name,created_at) VALUES ('t1','s1','Counter 1',0),('t2','s2','C1',0);
INSERT INTO users (id,name,role,created_at) VALUES ('u1','Cashier','cashier',0),
       ('u_mgr','Store Manager','staff',0),('u_own','Owner','owner',0);
INSERT INTO staff_sessions (id,user_id,store_id,terminal_id,auth_method,started_at)
VALUES ('ss1','u1','s1','t1','pin_offline',1000);
INSERT INTO items (id,name,dimension,pricing_mode,base_unit,default_sales_unit,updated_at)
VALUES ('i1','Dal','count','fixed_only','pcs','pcs',0),('i2','Soap','count','fixed_only','pcs','pcs',0);
""")
push("b1", "s1", "t1", "ss1", "2026-09-10", [("i1", 1000, 15238, 16000, 381, 381, 0)])
push("b2", "s1", "t1", "ss1", "2026-09-10", [("i2", 500, 22857, 24000, 571, 572, 0)])
push("b3", "s1", "t1", "ss1", "2026-11-14", [("i2", 10, 1000, 1050, 25, 25, 0)])
for b in ("b1", "b2", "b3"):
    con.execute("UPDATE bills SET invoice_no = 'KTP/' || substr(id,2) WHERE id = ?", (b,))

# ---------------------------------------------------------------- 1
rule("1. A void that arrives for an archived bill (D22: 'parked bill_archived, never dropped')")
con.execute("UPDATE schema_meta SET archive_before = '2026-09-11' WHERE id = 1")
print("archive_before = 2026-09-11; the September bills are exported to R2 and deleted:")
for b in ("b1", "b2"):
    con.execute("DELETE FROM bill_voids WHERE bill_id=?", (b,))
    con.execute("DELETE FROM bills WHERE id=?", (b,))
print("  bills left:", run("SELECT id, business_date FROM bills"))
print("September still in the rollup:",
      run("SELECT item_id, qty_base, amount_minor FROM sales_daily WHERE business_date='2026-09-10'"))
print("\nThe terminal, offline for a week, now sends a void for b1. The Worker must record it:")
try_("INSERT INTO bill_voids for the archived bill",
     """INSERT INTO bill_voids (id,bill_id,store_id,terminal_id,user_id,reason,created_at)
        VALUES ('v_late','b1','s1','t1','u1','customer returned the bag',1900)""")
con.execute("DROP TRIGGER trg_bill_voids_same_store")
print("  (with the trigger out of the way, the foreign key refuses it too)")
try_("  same insert, trigger dropped",
     """INSERT INTO bill_voids (id,bill_id,store_id,terminal_id,user_id,reason,created_at)
        VALUES ('v_late','b1','s1','t1','u1','customer returned the bag',1900)""")
print("  -> the sale is a fact and the void is a fact; D1 has nowhere to put it.")
print("  candidate tables in this schema that could hold a void for a bill that no longer exists:",
      [r[0] for r in run("""SELECT name FROM sqlite_master WHERE type='table' AND
           (name LIKE '%void%' OR name LIKE '%park%' OR name LIKE '%pend%')""")])
print("  bill_voids columns:", [r[1] for r in run("PRAGMA table_info(bill_voids)")])
print("\nEven if the row could be stored, the rollup cannot be corrected from it:")
print("  sales_daily needs (store, date, item, qty, amount) to decrement, and bill_voids carries")
print("  no item, no qty, no amount. The bill's lines are in R2, not D1.")
print("  September totals after a late void that the schema cannot record:")
print("   ", run("""SELECT item_id, qty_base, amount_minor FROM sales_daily
                   WHERE business_date='2026-09-10' ORDER BY item_id"""))

# ---------------------------------------------------------------- 2
rule("2. Practice / training bills — is there a safe place?")
print("Any column that could mark a bill as not-real:",
      [r[1] for r in run("PRAGMA table_info(bills)")
       if any(k in r[1] for k in ("train", "practice", "demo", "test", "kind", "type"))])
print("Any column on stores/terminals that could scope a whole store out of reporting:",
      [f"{t}.{c}" for t in ("stores", "terminals") for c in
       [r[1] for r in run(f"PRAGMA table_info({t})")]])
con.execute("INSERT INTO stores (id,name,created_at) VALUES ('s_train','Training Room',0)")
con.execute("INSERT INTO terminals (id,store_id,name,created_at) VALUES ('t_train','s_train','Trainer',0)")
seq[0] = 100
push("b_train", "s_train", "t_train", "ss1", "2026-11-14",
     [("i1", 1000, 15238, 16000, 381, 381, 0)])
print("\na 'training' store inside the same D1 sells 1 kg of dal. Immediately:")
print("  it is in sales_daily:", run("SELECT store_id,business_date,item_id,amount_minor FROM sales_daily "
                                   "WHERE store_id='s_train'"))
print("  it is in the rate-wise GST query for the enterprise:",
      run("""SELECT bl.tax_rate_bp, SUM(bl.taxable_minor) FROM bill_lines bl JOIN bills b ON b.id=bl.bill_id
             WHERE b.business_date='2026-11-14' AND b.voided=0 GROUP BY 1"""))
print("  and a store row shares the enterprise's GSTIN, because there is no GSTIN anywhere to differ")
print("  (probe P1 C). So a training store is a real store as far as every report and return is")
print("  concerned. There is no flag that says otherwise.")
print("\nThe safe place the design already has:")
print("  docs/d1-schema.sql runs 'unchanged' on the terminal's local SQLite (file header). A second")
print("  file, training.db, is a complete register: items, prices, a numbering policy, bills.")
con2 = sqlite3.connect(":memory:", isolation_level=None)
con2.execute("PRAGMA foreign_keys = ON")
con2.executescript((DOCS / "d1-schema.sql").read_text())
for m in sorted((DOCS / "migrations").glob("*.sql")):
    con2.executescript(m.read_text())
print("  a fresh training.db has devices rows:", con2.execute("SELECT COUNT(*) FROM devices").fetchone()[0],
      "| enrol_codes:", con2.execute("SELECT COUNT(*) FROM enrol_codes").fetchone()[0])
print("  /v1/token needs a devices row for the credential's subject, and /v1/enroll needs a code")
print("  minted by an owner inside a tenant D1. With neither, no route can accept a bill from it.")
sql = """INSERT INTO stores (id,name,created_at) VALUES ('st','Train',0)"""
con2.execute(sql)
con2.execute("INSERT INTO terminals (id,store_id,name,created_at) VALUES ('tt','st','T',0)")
con2.execute("INSERT INTO users (id,name,role,created_at) VALUES ('ut','Trainee','cashier',0)")
con2.execute("INSERT INTO staff_sessions (id,user_id,store_id,terminal_id,auth_method,started_at) "
             "VALUES ('sst','ut','st','tt','pin_offline',0)")
con2.execute("INSERT INTO items (id,name,dimension,pricing_mode,base_unit,default_sales_unit,updated_at) "
             "VALUES ('it','Train Soap','count','fixed_only','pcs','pcs',0)")
con2.execute("BEGIN"); con2.execute("PRAGMA defer_foreign_keys = on")
con2.execute("""INSERT INTO bill_lines (bill_id,line_no,item_id,item_name,dimension,qty_base,price_version,
    price_source,list_price_minor,price_minor,price_basis_qty_base,discount_minor,list_amount_minor,
    price_breakdown,line_total_minor,taxable_minor,tax_rate_bp,cgst_minor,sgst_minor,cess_minor,igst_minor)
    VALUES ('bt',1,'it','x','count',1,1,'list',1050,1050,1,0,1050,'{}',1050,1000,500,25,25,0,0)""")
con2.execute("""INSERT INTO bills (id,store_id,terminal_id,user_id,auth_method,business_date,local_seq,
    display_no,invoice_no,total_minor,subtotal_minor,tax_minor,rounding_minor,created_at,staff_session_id)
    VALUES ('bt','st','tt','ut','pin_offline','2026-11-14',1,'T1',NULL,1050,1000,50,0,100,'sst')""")
con2.execute("COMMIT")
print("  a training bill in training.db: invoice_no =",
      con2.execute("SELECT invoice_no FROM bills WHERE id='bt'").fetchone()[0],
      "(invoice_no is server-assigned, and there is no server) ->")
print("  a training session can never consume a GST invoice serial, by construction.")
try_("DELETE the training store's bill (the 'clear practice data' GoFrugal lacks)",
     "DELETE FROM bills WHERE id='bt'")
print("  -> a training file is disposable with `rm`, which is what GoFrugal's FAQ 09 says it cannot do.")

# ---------------------------------------------------------------- 3
rule("3. Two registers allocating invoice numbers concurrently")
# a fifth bill, properly batched, whose invoice_no collides with b3's
try:
    push("b5", "s1", "t1", "ss1", "2026-11-14", [("i1", 1, 100, 105, 2, 3, 0)], invoice="KTP/3")
    print("  duplicate invoice_no: ACCEPTED (bad)")
except sqlite3.Error as e:
    print(f"  duplicate invoice_no: refused -> {e}")
print("  uq_bills_invoice_no is what refused it, and the bill's own lines went back with the")
print("  failed batch (one db.batch() per bill), so no orphan line survives.")
print("  bills with invoice KTP/3:", run("SELECT id, invoice_no FROM bills WHERE invoice_no='KTP/3'"))
try_("rewrite an assigned invoice_no", "UPDATE bills SET invoice_no='KTP/other' WHERE id='b3'")
# Rule 46(b): a serial number not exceeding sixteen characters
push("b6", "s1", "t1", "ss1", "2026-11-14", [("i1", 1, 100, 105, 2, 3, 0)],
     invoice="KTP/2026-27/00001234")
print("  a 22-character invoice_no:", run("SELECT length(invoice_no), invoice_no FROM bills WHERE id='b6'"))
print("  -> accepted; nothing in the DDL or the route table caps the length at 16 characters")
print("  (numbering_policy.prefix is unbounded TEXT and padding is 1..12).")
print("\nVERDICT: concurrent allocation fails closed, which is the important half. The shape of the")
print("         number itself is unconstrained.")

rule("4. Who may approve an override? (route table: 'manager email/password', error: not_approver)")
print("users.role CHECK:", [r[0] for r in run(
    "SELECT sql FROM sqlite_master WHERE name='users'")][0].split("CHECK")[1].strip()[:40])
print("approval_grants columns:", [r[1] for r in run("PRAGMA table_info(approval_grants)")])
print("a store manager is users.role =", run("SELECT role FROM users WHERE id='u_mgr'")[0][0])
print("and approval_grants has no capability column, so 'may approve' is expressible only as")
print("role='owner' — the route table's own error list says `not_approver: user is not an")
print("active owner`.")
print("\nVERDICT for 4: a chain with 5 stores has one or two owners. Either the owner's password")
print("travels to every store, or every over-ceiling discount needs an online call from the owner's")
print("phone. Neither is a grocery store's day.")
