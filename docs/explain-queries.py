#!/usr/bin/env python3
"""Seed a scratch DB from docs/d1-schema.sql and print EXPLAIN QUERY PLAN for the hot queries.

Flags a full table SCAN on any table that grows with bills or catalog size.
"""
import pathlib, re, sqlite3, sys

here = pathlib.Path(__file__).parent
ddl = (here / "d1-schema.sql").read_text()
con = sqlite3.connect(":memory:")
con.executescript(ddl)
mig_dir = pathlib.Path(__file__).with_name("migrations")
for m in sorted(mig_dir.glob("*.sql")) if mig_dir.exists() else []:
    con.executescript(m.read_text())

SEED = """
INSERT OR IGNORE INTO stores(id, name, created_at) VALUES ('str_1', 'Main', 0);
INSERT OR IGNORE INTO terminals(id, store_id, name, created_at) VALUES ('trm_1', 'str_1', 'Counter 1', 0);
INSERT OR IGNORE INTO users(id, name, email, role, active, created_at)
VALUES ('usr_1', 'Ravi', 'ravi@example.com', 'cashier', 1, 0);
INSERT OR IGNORE INTO store_settings(store_id, business_day_start_minutes, rounding_mode)
VALUES ('str_1', 240, 'total_nearest_rupee');
INSERT OR IGNORE INTO items(id, name, dimension, pricing_mode, base_unit, active, updated_at)
VALUES ('itm_1', 'Tamarind', 'mass', 'fixed_only', 'g', 1, 0);
INSERT OR IGNORE INTO item_prices(item_id, qty_base, price_minor, version, valid_from)
VALUES ('itm_1', 1000, 4000, 7, 0), ('itm_1', 500, 2500, 7, 0);
INSERT OR IGNORE INTO item_identifiers(alias, kind, item_id, qty_base, store_id, active)
VALUES ('T500', 'alias', 'itm_1', 500, 'str_1', 1);
INSERT OR IGNORE INTO bills(id, store_id, terminal_id, user_id, auth_method, business_date,
  local_seq, display_no, total_minor, subtotal_minor, tax_minor, rounding_minor, created_at, received_at)
VALUES ('b1', 'str_1', 'trm_1', 'usr_1', 'pin_offline', '2026-11-14', 1, 'S1-T1-000001', 6500, 6500, 0, 0, 0, 0);
INSERT OR IGNORE INTO bill_lines(bill_id, line_no, item_id, item_name, dimension, qty_base,
  price_version, price_source, list_price_minor, price_minor, list_amount_minor, discount_minor,
  price_breakdown, line_total_minor, taxable_minor, cgst_minor, sgst_minor, cess_minor, igst_minor,
  tax_rate_bp, price_basis_qty_base)
VALUES ('b1', 1, 'itm_1', 'Tamarind', 'mass', 1500, 7, 'tier', 4000, 4000, 6500, 0,
  '{}', 6500, 6500, 0, 0, 0, 0, 0, 1);
"""

for stmt in [s.strip() for s in SEED.split(";") if s.strip()]:
    try:
        con.execute(stmt)
    except sqlite3.Error as e:
        # a swallowed seed turns every plan below into a plan on an empty table
        print(f"SEED FAILED ({e}): {stmt.splitlines()[0][:70]}")
        raise SystemExit(2)

QUERIES = {
    "catalog checkpoint": "SELECT seq, entity, entity_id, op FROM catalog_changes WHERE seq > ? ORDER BY seq LIMIT 500",
    "alias resolve": "SELECT item_id, qty_base FROM item_identifiers WHERE store_id = ? AND alias = ? AND active = 1",
    "price at time": ("SELECT price_minor, version FROM item_prices WHERE item_id = ? AND qty_base = ? "
                      "AND valid_from <= ? AND (valid_to IS NULL OR valid_to > ?) ORDER BY valid_from DESC LIMIT 1"),
    "day report": ("SELECT bl.item_id, SUM(bl.qty_base), SUM(bl.line_total_minor) FROM bill_lines bl "
                   "JOIN bills b ON b.id = bl.bill_id WHERE b.store_id = ? AND b.business_date = ? "
                   "AND b.voided = 0 GROUP BY bl.item_id"),
    "month report": ("SELECT bl.item_id, SUM(bl.qty_base), SUM(bl.line_total_minor) FROM bill_lines bl "
                     "JOIN bills b ON b.id = bl.bill_id WHERE b.store_id = ? AND b.business_date BETWEEN ? AND ? "
                     "AND b.voided = 0 GROUP BY bl.item_id"),
    "override list": ("SELECT b.business_date, bl.item_name FROM bill_lines bl JOIN bills b ON b.id = bl.bill_id "
                      "WHERE b.store_id = ? AND bl.price_source IN ('override','manual') AND b.business_date BETWEEN ? AND ?"),
    "unsynced bills": "SELECT id FROM bills WHERE synced_at IS NULL ORDER BY local_seq LIMIT 100",
    "device lookup": "SELECT id, store_id FROM devices WHERE credential_prefix = ?",
    "staff roster": ("SELECT u.id, u.name FROM users u WHERE u.active = 1 "
                     "AND EXISTS (SELECT 1 FROM user_stores us WHERE us.user_id = u.id AND us.store_id = ?)"),
}

BIG = {"bill_lines", "bills", "catalog_changes", "item_prices", "item_identifiers", "audit_events",
       "inventory_movements", "sessions", "devices"}

bad = 0
for name, q in QUERIES.items():
    try:
        plan = con.execute("EXPLAIN QUERY PLAN " + q, [None] * q.count("?")).fetchall()
    except sqlite3.Error as e:
        print(f"{name}: ERROR {e}")
        bad += 1
        continue
    text = " | ".join(r[3] for r in plan)
    # a scan of a partial index is fine: the index only holds rows the predicate selects
    used = re.findall(r"USING (?:COVERING )?INDEX (\w+)", text)
    partial = any(
        "WHERE" in (con.execute(
            "select sql from sqlite_master where type='index' and name=?", (n,)).fetchone() or [""])[0].upper()
        for n in used)
    scans = [] if partial else [t for t in BIG if f"SCAN {t}" in text]
    flag = "  <-- SCAN" if scans else ""
    if scans:
        bad += 1
    print(f"{name}{flag}\n    {text}")

print("\nOK" if bad == 0 else f"\n{bad} query plan(s) need attention")
sys.exit(0 if bad == 0 else 1)
