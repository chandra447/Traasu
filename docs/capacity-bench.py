#!/usr/bin/env python3
"""Bytes per bill on the real schema (DDL + every migration), for capacity.md.

    python3 docs/capacity-bench.py [bills] [lines]      # default 20000 bills, 4 and 10 lines

Writes the full push path in the Worker's order (deferred FKs, lines, movements, header; the
sales_daily rollup comes from the trigger), five stores interleaved, UUIDv7 text ids, realistic
names and JSON. Prints bytes/bill with and without inventory_movements. Local SQLite pages: on the
pre-0008 schema the same load measured 4,573 B/bill here and 4,961 on real D1; on the current schema it
prints 5,172 (4 lines) and 11,840 (10 lines).
"""
import hashlib, json, os, pathlib, random, sqlite3, sys, tempfile, uuid

here = pathlib.Path(__file__).parent
random.seed(7)
clock = [1763000000]
NAMES = ["Tamarind", "Toor Dal Premium 1kg", "Sunflower Oil Refined 1L Pouch", "Basmati Rice Long Grain",
         "Aashirvaad Atta Whole Wheat 5kg", "Onion", "Tomato Hybrid", "Green Chilli", "Coriander Leaves",
         "Amul Taaza Toned Milk 500ml", "Parle-G Gold Biscuits 1kg", "Surf Excel Easy Wash 1kg"]
BREAKDOWN = json.dumps({"parts": [{"qty_base": 1000, "price_minor": 4000}, {"qty_base": 500, "price_minor": 2500}],
                        "rule": "greedy_desc", "tier_version": 8})


def uuid7():
    clock[0] += 1
    b = bytearray((clock[0] * 1000 + random.randrange(1000)).to_bytes(6, "big") + os.urandom(10))
    b[6] = (b[6] & 0x0F) | 0x70
    b[8] = (b[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(b)))


def used_bytes(con):
    pages, free, size = (con.execute(f"PRAGMA {p}").fetchone()[0] for p in ("page_count", "freelist_count", "page_size"))
    return (pages - free) * size


def run(nbills, nlines, movements):
    path = tempfile.mktemp(suffix=".db")
    con = sqlite3.connect(path, isolation_level=None)
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript((here / "d1-schema.sql").read_text())
    for m in sorted((here / "migrations").glob("*.sql")):
        con.executescript(m.read_text())
    stores, terms, users, sess = ([uuid7() for _ in range(5)] for _ in range(4))
    items = [uuid7() for _ in range(2000)]
    con.execute("BEGIN")
    for s, t, u, ss in zip(stores, terms, users, sess):
        con.execute("INSERT INTO stores (id, name, created_at) VALUES (?, 'Store', 0)", (s,))
        con.execute("INSERT INTO terminals (id, store_id, name, created_at) VALUES (?, ?, 'T', 0)", (t, s))
        con.execute("INSERT INTO users (id, name, role, created_at) VALUES (?, 'c', 'cashier', 0)", (u,))
        con.execute("INSERT INTO staff_sessions (id, user_id, store_id, terminal_id, auth_method, started_at) "
                    "VALUES (?, ?, ?, ?, 'pin_offline', 0)", (ss, u, s, t))
    for i in items:
        con.execute("INSERT INTO items (id, name, dimension, pricing_mode, base_unit, default_sales_unit, updated_at) "
                    "VALUES (?, ?, 'mass', 'tiered', 'g', 'kg', 0)", (i, random.choice(NAMES)))
    con.execute("COMMIT")
    if not movements:
        con.execute("DROP TABLE inventory_movements")
    base = used_bytes(con)
    seqs = [0] * 5
    for n in range(nbills):
        k = n % 5
        seqs[k] += 1
        bid, day = uuid7(), f"2026-{1 + n * 12 // nbills:02d}-{1 + (n // 50) % 28:02d}"
        con.execute("BEGIN")
        con.execute("PRAGMA defer_foreign_keys = on")
        total = taxable_sum = 0
        for ln in range(1, nlines + 1):
            amount = random.randrange(1000, 60000)
            taxable = round(amount / 1.05)
            cgst = (amount - taxable) // 2
            item = random.choice(items)
            con.execute(
                """INSERT INTO bill_lines (bill_id, line_no, item_id, item_name, dimension, qty_base, price_version,
                     price_source, list_price_minor, price_minor, price_basis_qty_base, list_amount_minor, discount_minor,
                     price_breakdown, line_total_minor, taxable_minor, tax_rate_bp, cgst_minor, sgst_minor, cess_minor,
                     igst_minor, hsn, price_store_id, computed_list_price_minor)
                   VALUES (?, ?, ?, ?, 'mass', 1500, 8, 'tier', 4000, 4000, 1000, ?, 0, ?, ?, ?, 500, ?, ?, 0, 0,
                     '0810', ?, 4000)""",
                (bid, ln, item, random.choice(NAMES), amount, BREAKDOWN, amount, taxable, cgst,
                 amount - taxable - cgst, stores[k]))
            if movements:
                con.execute("INSERT INTO inventory_movements VALUES (?, ?, ?, ?, 'out', 1500, ?, ?)",
                            (uuid7(), item, bid, ln, clock[0], clock[0]))
            total += amount
            taxable_sum += taxable
        con.execute(
            """INSERT INTO bills (id, store_id, terminal_id, user_id, auth_method, business_date, local_seq, display_no,
                 total_minor, subtotal_minor, tax_minor, rounding_minor, created_at, payload_sha256, invoice_no,
                 staff_session_id, received_at, synced_at, business_day_start_minutes)
               VALUES (?, ?, ?, ?, 'pin_offline', ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, 240)""",
            (bid, stores[k], terms[k], users[k], day, seqs[k], f"S{k}-T{k}-{seqs[k]:06d}", total, taxable_sum,
             total - taxable_sum, clock[0], hashlib.sha256(bid.encode()).hexdigest(), f"KUK/2026/{n:06d}",
             sess[k], clock[0], clock[0]))
        con.execute("COMMIT")
    per_bill = (used_bytes(con) - base) / nbills
    con.close()
    os.remove(path)
    return per_bill


if __name__ == "__main__":
    nbills = int(sys.argv[1]) if len(sys.argv) > 1 else 20000
    line_counts = [int(sys.argv[2])] if len(sys.argv) > 2 else [4, 10]
    bills_per_year = 5 * 600 * 365
    print(f"{nbills} bills, 5 stores interleaved; years to 10 GB at 5 stores x 600 bills/day, no archival")
    for nlines in line_counts:
        for movements in (True, False):
            b = run(nbills, nlines, movements)
            gb = b * bills_per_year / 1e9
            print(f"  {nlines:2d} lines, movements={'yes' if movements else 'no ':3}  {b:6.0f} B/bill  "
                  f"{gb:5.2f} GB/year  {10 / gb:4.1f} years")
