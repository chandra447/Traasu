#!/usr/bin/env python3
"""Seed a scratch DB from docs/d1-schema.sql + migrations and print EXPLAIN QUERY PLAN for the hot queries.

Flags a full table SCAN on any table that grows with bills or catalog size. The seed uses plain INSERT:
a row that violates a constraint stops the script instead of leaving the plans to run on empty tables.
"""
import pathlib, re, sqlite3, sys

here = pathlib.Path(__file__).parent
con = sqlite3.connect(":memory:", isolation_level=None)
con.execute("PRAGMA foreign_keys = ON")
con.executescript((here / "d1-schema.sql").read_text())
for m in sorted((here / "migrations").glob("*.sql")):
    con.executescript(m.read_text())

SEED = """
INSERT INTO stores (id, name, created_at) VALUES ('str_1', 'Main', 0);
INSERT INTO terminals (id, store_id, name, created_at) VALUES ('trm_1', 'str_1', 'Counter 1', 0);
INSERT INTO users (id, name, email, role, active, created_at) VALUES ('usr_1', 'Ravi', 'ravi@example.com', 'cashier', 1, 0);
INSERT INTO user_stores (user_id, store_id) VALUES ('usr_1', 'str_1');
INSERT INTO staff_credentials (user_id, pin_hash, pin_salt, updated_at) VALUES ('usr_1', 'h', 's', 0);
INSERT INTO store_settings (store_id, business_day_start_minutes, rounding_mode, updated_at)
VALUES ('str_1', 240, 'nearest_rupee', 0);
INSERT INTO items (id, name, dimension, pricing_mode, base_unit, default_sales_unit, active, updated_at)
VALUES ('itm_1', 'Tamarind', 'mass', 'tiered', 'g', 'kg', 1, 0);
INSERT INTO item_prices (item_id, qty_base, price_minor, version, valid_from, created_at)
VALUES ('itm_1', 1000, 4000, 7, 0, 0), ('itm_1', 500, 2500, 7, 0, 0);
INSERT INTO item_identifiers (id, alias, kind, item_id, qty_base, store_id, active, updated_at)
VALUES ('idn_1', 'T500', 'alias', 'itm_1', 500, 'str_1', 1, 0);
INSERT INTO devices (id, store_id, terminal_id, name, credential_prefix, credential_hash, install_id, enrolled_at)
VALUES ('dev_1', 'str_1', 'trm_1', 'Counter 1', 'v1_9tR2', 'hash', 'inst', 0);
BEGIN;
PRAGMA defer_foreign_keys = on;
INSERT INTO bill_lines (bill_id, line_no, item_id, item_name, dimension, qty_base, price_version, price_source,
  list_price_minor, price_minor, price_basis_qty_base, list_amount_minor, discount_minor, price_breakdown,
  line_total_minor, taxable_minor, tax_rate_bp, cgst_minor, sgst_minor, cess_minor, igst_minor)
VALUES ('b1', 1, 'itm_1', 'Tamarind', 'mass', 1500, 7, 'tier', 4000, 4000, 1000, 6500, 0,
  '{"parts":[{"qty_base":1000,"price_minor":4000},{"qty_base":500,"price_minor":2500}]}',
  6500, 6190, 500, 155, 155, 0, 0);
INSERT INTO inventory_movements (id, item_id, bill_id, bill_line_no, direction, qty_base, occurred_at, created_at)
VALUES ('mv_1', 'itm_1', 'b1', 1, 'out', 1500, 0, 0);
INSERT INTO bills (id, store_id, terminal_id, user_id, auth_method, business_date, local_seq, display_no,
  total_minor, subtotal_minor, tax_minor, rounding_minor, created_at, received_at)
VALUES ('b1', 'str_1', 'trm_1', 'usr_1', 'pin_offline', '2026-11-14', 1, 'S1-T1-000001', 6500, 6190, 310, 0, 0, 0);
COMMIT;
"""

for stmt in [s.strip() for s in SEED.split(";") if s.strip()]:
    try:
        con.execute(stmt)
    except sqlite3.Error as e:
        print(f"SEED FAILED ({e}): {stmt.splitlines()[0][:70]}")
        raise SystemExit(2)

for t in ("items", "item_prices", "item_identifiers", "bills", "bill_lines", "sales_daily", "inventory_movements"):
    if con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 0:
        print(f"SEED EMPTY: {t}")
        raise SystemExit(2)

QUERIES = {
    "catalog checkpoint": "SELECT seq, entity, entity_id, op FROM catalog_changes WHERE seq > ? ORDER BY seq LIMIT 500",
    "alias resolve": "SELECT item_id, qty_base FROM item_identifiers WHERE store_id = ? AND alias = ? AND active = 1",
    "price at time (store-scoped)": (
        "SELECT price_minor, version FROM item_prices WHERE item_id = ? AND qty_base = ? "
        "AND (store_id IS ? OR store_id IS NULL) AND valid_from <= ? AND (valid_to IS NULL OR valid_to > ?) "
        "ORDER BY store_id IS NULL, valid_from DESC LIMIT 1"),
    "batch for a new line + prompt (register, at scan)": (
        "WITH live AS (SELECT b.id, b.batch_no, b.mrp_minor, b.expiry_date, b.received_at, "
        "k.priority, k.name AS book, IFNULL(s.on_hand_qty_base, 0) AS on_hand, p.price_minor FROM item_batches b "
        "JOIN books k ON k.id = b.book_id AND k.active = 1 "
        "LEFT JOIN batch_stock s ON s.batch_id = b.id "
        "LEFT JOIN item_prices p ON p.item_id = b.item_id AND p.batch_id = b.id AND p.qty_base = ? "
        "AND p.valid_to IS NULL WHERE b.item_id = ? AND b.store_id = ? "
        "AND b.retired_at IS NULL AND (b.expiry_date IS NULL OR b.expiry_date >= ?)), "
        "latest AS (SELECT * FROM live ORDER BY received_at DESC, id DESC LIMIT 1), "
        "same AS (SELECT live.* FROM live, latest WHERE IFNULL(live.mrp_minor, -1) = IFNULL(latest.mrp_minor, -1) "
        "AND live.price_minor IS latest.price_minor), "
        "pick AS (SELECT id FROM same ORDER BY on_hand > 0 DESC, priority, received_at DESC, id DESC LIMIT 1) "
        "SELECT live.*, live.id = (SELECT id FROM pick) AS used FROM live "
        "WHERE live.id = (SELECT id FROM pick) OR (live.on_hand > 0 AND live.id NOT IN (SELECT id FROM same)) "
        "ORDER BY used DESC, live.priority, live.received_at DESC, live.id DESC"),
    "batch prices (register, after pick)": (
        "SELECT qty_base, price_minor, mrp_minor, version FROM item_prices "
        "WHERE item_id = ? AND batch_id = ? AND valid_to IS NULL ORDER BY qty_base"),
    "parent batch for a child line (register, at scan)": (
        "SELECT b.id FROM item_batches b JOIN books k ON k.id = b.book_id AND k.active = 1 "
        "LEFT JOIN batch_stock s ON s.batch_id = b.id WHERE b.item_id = ? AND b.store_id = ? "
        "AND b.retired_at IS NULL AND (b.expiry_date IS NULL OR b.expiry_date >= ?) "
        "ORDER BY IFNULL(s.on_hand_qty_base, 0) > 0 DESC, k.priority, b.received_at DESC, b.id DESC LIMIT 1"),
    "repack items of a bulk (admin repack entry)": (
        "SELECT i.id, i.name, i.content_qty_base FROM item_repack_sources s JOIN items i ON i.id = s.item_id "
        "WHERE s.bulk_item_id = ?"),
    "packs that can go back to a bulk batch (admin unpack)": (
        "SELECT l.batch_id, l.content_qty_base, s.on_hand_qty_base FROM repacks r "
        "JOIN repack_lines l ON l.repack_id = r.id LEFT JOIN batch_stock s ON s.batch_id = l.batch_id "
        "WHERE r.bulk_batch_id = ? AND r.kind = 'pack'"),
    "stock pull (GET /v1/stock)": (
        "SELECT batch_id, on_hand_qty_base, seq FROM batch_stock WHERE store_id = ? AND seq > ? "
        "ORDER BY seq LIMIT 500"),
    "day report (bill_lines)": (
        "SELECT bl.item_id, SUM(bl.qty_base), SUM(bl.line_total_minor) FROM bill_lines bl "
        "JOIN bills b ON b.id = bl.bill_id WHERE b.store_id = ? AND b.business_date = ? "
        "AND b.voided = 0 GROUP BY bl.item_id"),
    "multi-day report (sales_daily)": (
        "SELECT item_id, SUM(qty_base), SUM(amount_minor) FROM sales_daily "
        "WHERE store_id = ? AND business_date BETWEEN ? AND ? GROUP BY item_id"),
    "override list": (
        "SELECT b.business_date, bl.item_name FROM bill_lines bl JOIN bills b ON b.id = bl.bill_id "
        "WHERE b.store_id = ? AND bl.price_source IN ('override','manual') AND b.business_date BETWEEN ? AND ? "
        "AND b.voided = 0"),
    "unsynced bills": "SELECT id FROM bills WHERE synced_at IS NULL ORDER BY local_seq LIMIT 100",
    "device lookup": "SELECT id, store_id FROM devices WHERE credential_prefix = ?",
    "staff roster": (
        "SELECT u.id, u.name, c.pin_hash, c.pin_salt FROM user_stores us JOIN users u ON u.id = us.user_id "
        "LEFT JOIN staff_credentials c ON c.user_id = u.id WHERE us.store_id = ? AND u.active = 1"),
    "archival: bills to export": (
        "SELECT id FROM bills WHERE store_id = ? AND business_date < ? ORDER BY business_date, id LIMIT 500"),
    "archival cascade: movements of a line": (
        "SELECT id FROM inventory_movements WHERE bill_id = ? AND bill_line_no = ?"),
}

BIG = {"bill_lines", "bills", "catalog_changes", "item_prices", "item_identifiers", "audit_events",
       "inventory_movements", "auth_session", "devices", "sales_daily", "repacks", "repack_lines"}

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
        "WHERE" in ((con.execute(
            "select sql from sqlite_master where type='index' and name=?", (n,)).fetchone() or [None])[0] or "").upper()
        for n in used)
    scans = [] if partial else [t for t in BIG if re.search(rf"\bSCAN {t}\b", text)]
    flag = "  <-- SCAN" if scans else ""
    if scans:
        bad += 1
    print(f"{name}{flag}\n    {text}")

print("\nOK" if bad == 0 else f"\n{bad} query plan(s) need attention")
sys.exit(0 if bad == 0 else 1)
