# Design docs — index

Working set for the D1 schema and Hono API. `SUMMARY.md` at the repo root stays the decision log.

| File | Answers | Status |
|---|---|---|
| `DESIGN-BRIEF.md` | Shared context handed to every design agent | stable |
| `query-catalogue.md` | The queries the schema must serve, written as a yardstick | stable |
| `d1-limits.md` | Cloudflare D1 limits and what each one rules out | stable |
| `api-routes.md` | Route inventory by auth plane, payloads, error taxonomy | written by lead |
| `deploy-inventory.md` | Cloudflare resources, bindings, migrations, provisioning | written by lead |
| `d1-schema.sql` | Full DDL for one tenant | verified |
| `d1-schema.md` | UML (mermaid ER) + table rationale + index-to-query map | verified |
| `review-sync.md` | Adversarial review: checkpoint, batches, D1 limits | complete |
| `review-schema.md` | Adversarial review: money, immutability, SCD2, tenancy | complete |
| `review-security.md` | Adversarial review: auth, tenancy, attribution, overrides | complete |
| `migrations/0002–0005` | Review fixes, additive only | verified |
| `TRIAGE.md` | Disposition of all 91 findings | complete |
| `DECISIONS.md` | D1–D15 with costs | complete |
| `check-schema.py` | Executes DDL + migrations, flags Postgres-isms and float money | tool |

## How to verify a change

```sh
python3 docs/check-schema.py          # DDL + migrations parse, indexes exist, no float money
python3 docs/explain-queries.py       # every hot query gets an index; fails on a bad seed
```

The check is deliberately shallow: it proves the file is valid SQLite and that the obvious traps are absent.
It does not prove an index is used — that needs `EXPLAIN QUERY PLAN` against a seeded database, which is the
next thing to build once the DDL settles.
