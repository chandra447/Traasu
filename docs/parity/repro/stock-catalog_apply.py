#!/usr/bin/env python3
"""Can a register apply the catalog delta/batch documents in the order the wire delivers them?

The register runs 'the same DDL' (DESIGN-BRIEF), so its copy carries the same triggers and unique
indexes. Deltas arrive ordered by catalog_changes.seq with documents embedded at read time (§3.3).
"""
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
    INSERT INTO items (id, name, dimension, pricing_mode, base_unit, default_sales_unit, uses_batches, updated_at)
      VALUES ('soap', 'Soap', 'count', 'fixed_only', 'pcs', 'pcs', 1, 0);
    """)
    return con


def tryx(con, sql, *a):
    try:
        con.execute(sql, a)
        return "OK"
    except sqlite3.Error as e:
        return str(e)


def head(t):
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


# ---------------------------------------------------------------- 1
head("1  Reordering two books: applying the change rows in seq order on the register")
server = mkdb()
print("server: Main p1, Godown p2                  :", "/".join([
    tryx(server, "INSERT INTO books (id,store_id,name,priority,created_at,updated_at) "
                 "VALUES ('k_main','s1','Main',1,0,0)"),
    tryx(server, "INSERT INTO books (id,store_id,name,priority,created_at,updated_at) "
                 "VALUES ('k_godown','s1','Godown',2,0,0)")]))
print("admin reorders (D28: park, move, set)       :", "/".join([
    tryx(server, "UPDATE books SET priority=3, version=2 WHERE id='k_main'"),
    tryx(server, "UPDATE books SET priority=1, version=2 WHERE id='k_godown'"),
    tryx(server, "UPDATE books SET priority=2, version=3 WHERE id='k_main'")]))
seq_first, seq_second = "k_main", "k_godown"   # the change rows the three statements wrote, in seq order
print("catalog change rows, in seq order           :", [seq_first, seq_second])
print("documents read at current state             :",
      server.execute("SELECT id, priority FROM books ORDER BY id").fetchall())

reg = mkdb()
print("register starts with the old books          :", "/".join([
    tryx(reg, "INSERT INTO books (id,store_id,name,priority,created_at,updated_at) "
              "VALUES ('k_main','s1','Main',1,0,0)"),
    tryx(reg, "INSERT INTO books (id,store_id,name,priority,created_at,updated_at) "
              "VALUES ('k_godown','s1','Godown',2,0,0)")]))
apply_main = ("INSERT INTO books (id,store_id,name,priority,active,version,created_at,updated_at) "
              "VALUES ('k_main','s1','Main',2,1,3,0,0) ON CONFLICT(id) DO UPDATE SET "
              "name=excluded.name, priority=excluded.priority, active=excluded.active, version=excluded.version")
apply_godown = ("INSERT INTO books (id,store_id,name,priority,active,version,created_at,updated_at) "
                "VALUES ('k_godown','s1','Godown',1,1,2,0,0) ON CONFLICT(id) DO UPDATE SET "
                "name=excluded.name, priority=excluded.priority, active=excluded.active, version=excluded.version")
print("apply in seq order: Main first              :", tryx(reg, apply_main))
print("  (the register's books now)                :", reg.execute("SELECT id,priority FROM books ORDER BY id").fetchall())
print("apply in seq order: Godown second           :", tryx(reg, apply_godown))
print("retry the same page                         :", tryx(reg, apply_main), "- identical failure, no progress")
print("neither order works: Godown first also fails:", "/".join([
    tryx((reg2 := mkdb()), "INSERT INTO books (id,store_id,name,priority,created_at,updated_at) "
                           "VALUES ('k_main','s1','Main',1,0,0)"),
    tryx(reg2, "INSERT INTO books (id,store_id,name,priority,created_at,updated_at) "
               "VALUES ('k_godown','s1','Godown',2,0,0)"),
    tryx(reg2, apply_godown), tryx(reg2, apply_main)]) + " -> " +
      str(reg2.execute("SELECT id,priority FROM books ORDER BY id").fetchall()))
print("  -> Main=1/Godown=2 to Godown=1/Main=2 needs a THIRD, temporary priority (D28).")
print("     Two final-state documents cannot express it, so the page can never be applied.")

# ---------------------------------------------------------------- 2
head("2  A new batch and its prices: applying the price document before the batch")
reg2 = mkdb()
price_doc = ("INSERT INTO item_prices (item_id, store_id, batch_id, qty_base, version, price_minor, mrp_minor, "
             "valid_from, created_at) VALUES ('soap','s1','b_new',1,7,10000,10000,2000,0)")
batch_doc = ("INSERT INTO item_batches (id,item_id,store_id,book_id,batch_no,mrp_minor,received_at,updated_at) "
             "VALUES ('b_new','soap','s1','k_main','NEW-1',10000,2000,0)")
print("books missing on a fresh register too       :", tryx(reg2, "SELECT COUNT(*) FROM books"))
tryx(reg2, "INSERT INTO books (id,store_id,name,priority,created_at,updated_at) VALUES ('k_main','s1','Main',1,0,0)")
print("price before batch                          :", tryx(reg2, price_doc))
print("batch, then price                           :", "/".join([tryx(reg2, batch_doc), tryx(reg2, price_doc)]))

# ---------------------------------------------------------------- 3
head("3  What the snapshot pages actually enumerate (api-routes.md §3.3)")
print('pages: items, prices, identifiers, users, reference (units/tax rates), store')
print("catalog_changes.entity CHECK (d1-schema.sql:262): item, item_price, item_identifier,")
print("  item_batch, book, unit, tax_rate, user, store, store_settings, numbering_policy, terminal_config")
print("delta `entity` enumeration (api-routes.md §3.3) : item, item_price, item_identifier, unit, tax_rate,")
print("  user, store, store_settings, numbering_policy, terminal_config")
print("-> item_batch and book are in the journal but in neither the delta enumeration nor a snapshot page")
print("\nDone.")
