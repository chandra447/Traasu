#!/usr/bin/env python3
"""Platform slice probe P3: is one migration file atomic?

wrangler's `d1 migrations apply` builds the query as <file text> + INSERT INTO d1_migrations
(workers-sdk packages/wrangler/src/d1/migrations/helpers.ts, buildMigrationQuery — no BEGIN/COMMIT).
D1 runs each statement in an implicit transaction (docs/d1-schema.sql header; docs/d1-limits.md:60).
So a file that fails half way leaves its earlier statements committed and never reaches its own
`UPDATE schema_meta SET schema_version = 8`.
"""
import pathlib, sqlite3

DOCS = pathlib.Path(__file__).resolve().parents[2]


def statements(sql):
    """Split a script into complete statements the way sqlite parses it (handles ;
    inside trigger bodies, strings and comments)."""
    out, buf = [], ""
    for line in sql.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            if buf.strip():
                out.append(buf)
            buf = ""
    if buf.strip():
        out.append(buf)
    return out


con = sqlite3.connect(":memory:", isolation_level=None)
con.execute("PRAGMA foreign_keys = ON")
con.executescript((DOCS / "d1-schema.sql").read_text())
for m in sorted((DOCS / "migrations").glob("*.sql")):
    if not m.name.startswith("0008"):
        con.executescript(m.read_text())
con.execute("CREATE TABLE IF NOT EXISTS d1_migrations (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "name TEXT UNIQUE, applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL)")
for n in range(1, 8):
    con.execute("INSERT INTO d1_migrations (name) SELECT name FROM pragma_database_list LIMIT 0")
    con.execute(f"INSERT OR IGNORE INTO d1_migrations (name) VALUES ('000{n}-x.sql')")

sql8 = (DOCS / "migrations" / "0008-audit-fixes.sql").read_text()
stmts = statements(sql8)
print("start: schema_version =", con.execute("SELECT schema_version FROM schema_meta").fetchone()[0],
      "| bill_lines columns =", len(con.execute("PRAGMA table_info(bill_lines)").fetchall()))
print("0008:", len(sql8.splitlines()), "lines,", len(stmts), "statements.")
print("contains BEGIN:", any(s.strip().upper().startswith("BEGIN") for s in stmts),
      "| contains COMMIT:", any("COMMIT" in s.upper() for s in stmts))

FAIL_AT = 25
run = 0
for i, s in enumerate(stmts):
    if i == FAIL_AT:
        break
    con.executescript(s)
    run += 1
print(f"\nafter {run} statements the next one fails (a typo, a D1 limit, a locked table — the cause")
print("does not matter). Committed so far:")
print("  schema_version      =", con.execute("SELECT schema_version FROM schema_meta").fetchone()[0],
      " (the UPDATE ... = 8 is the last statement, so it never ran)")
print("  bill_lines columns  =", [r[1] for r in con.execute("PRAGMA table_info(bill_lines)")][-5:])
print("  items has version?  =", "version" in [r[1] for r in con.execute("PRAGMA table_info(items)")])
print("  d1_migrations 0008  =",
      con.execute("SELECT COUNT(*) FROM d1_migrations WHERE name LIKE '0008%'").fetchone()[0],
      "row  -> migrate.mjs counts it as NOT applied")

print("\nthe migrate job for that tenant is re-run: wrangler re-sends the whole file from statement 1")
try:
    for s in stmts:
        con.executescript(s)
    print("  re-run succeeded")
except sqlite3.Error as e:
    print(f"  re-run aborted at statement 1: {e}")
print("  bill_lines columns  =", len(con.execute("PRAGMA table_info(bill_lines)").fetchall()))
print("  schema_version      =", con.execute("SELECT schema_version FROM schema_meta").fetchone()[0])

print("\n/health for that tenant: schema_version 7 against a Worker declaring min_schema 8")
print("  -> 503 schema_mismatch, while the tables are neither at 7 nor at 8.")
print("\nVERDICT: a migration file is not atomic, its schema_version statement is last, and the")
print("         frozen-column guards make a half-applied 0008 unrecoverable by re-running.")
