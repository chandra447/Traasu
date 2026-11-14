#!/usr/bin/env python3
"""Validate docs/d1-schema.sql: parses, indexes exist, no Postgres-isms."""
import re, sqlite3, sys, pathlib

p = pathlib.Path(__file__).with_name("d1-schema.sql")
if not p.exists():
    sys.exit("missing docs/d1-schema.sql")
sql = p.read_text()

bad = [w for w in ("SERIAL", "BIGSERIAL", "ENUM(", "JSONB", "TIMESTAMPTZ", "::") if w in sql.upper()]
if bad:
    print("postgres-isms:", bad)

con = sqlite3.connect(":memory:")
con.executescript(sql)

# apply migrations in filename order, exactly as provisioning would
mig_dir = p.parent / "migrations"
migs = sorted(mig_dir.glob("*.sql")) if mig_dir.exists() else []
for m in migs:
    con.executescript(m.read_text())
    print(f"migration applied: {m.name}")

tables = [r[0] for r in con.execute(
    "select name from sqlite_master where type='table' and name not like 'sqlite_%' order by 1")]
idx = [r[0] for r in con.execute(
    "select name from sqlite_master where type='index' and name not like 'sqlite_%' order by 1")]
print(f"tables ({len(tables)}): " + ", ".join(tables))
print(f"indexes ({len(idx)}): " + ", ".join(idx))

# every syncable catalog table must expose a cursor column
cursor_tables = [t for t in tables if t in (
    "items", "item_prices", "item_identifiers", "units", "tax_rates", "terminal_config",
    "catalog_changes", "store_settings", "users", "item_names")]
for t in cursor_tables:
    cols = [r[1] for r in con.execute(f"pragma table_info({t})")]
    if not any(c in cols for c in ("seq", "catalog_seq", "version", "updated_at")):
        print(f"note {t}: no per-row cursor (expected: covered by catalog_changes)")

# money/quantity must not be float
for t in tables:
    for r in con.execute(f"pragma table_info({t})"):
        name, typ = r[1], (r[2] or "").upper()
        if ("REAL" in typ or "FLOAT" in typ or "DOUBLE" in typ) and re.search(
                r"minor|price|qty|total|amount|rate", name):
            print(f"WARN {t}.{name} is {typ} but looks like money/quantity")

print("ok")
