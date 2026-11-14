# Design docs — index

Working set for the D1 schema and Hono API. `SUMMARY.md` at the repo root stays the decision log.

| File | Answers | Status |
|---|---|---|
| `DESIGN-BRIEF.md` | Shared context handed to every design agent | stable |
| `review-architecture.md` | The 2026-11-14 architecture audit: tenancy, D1 measurements, Better Auth, retention | complete |
| `query-catalogue.md` | The queries the schema must serve, written as a yardstick | stable |
| `d1-limits.md` | Cloudflare D1 limits and what each one rules out | stable |
| `capacity.md` | How long a tenant fits in 10 GB, with the arithmetic | stable |
| `api-routes.md` | Route inventory by auth plane, payloads, error taxonomy | written by lead |
| `deploy-inventory.md` | Cloudflare resources, bindings, migrations, provisioning | written by lead |
| `auth-better-auth.md` | The admin plane on Better Auth, and what failed before it worked | verified on 1.7.23 |
| `d1-schema.sql` | Full DDL for one tenant | verified |
| `d1-schema.md` | UML (mermaid ER) + table rationale + index-to-query map | verified |
| `review-sync.md` | Adversarial review: checkpoint, batches, D1 limits | complete |
| `review-schema.md` | Adversarial review: money, immutability, SCD2, tenancy | complete |
| `review-security.md` | Adversarial review: auth, tenancy, attribution, overrides | complete |
| `migrations/0002–0008` | Review and audit fixes, additive except trigger replacements | verified |
| `TRIAGE.md` | Disposition of every finding | complete |
| `DECISIONS.md` | D1–D25 with costs | complete |
| `check-schema.py` | Executes DDL + migrations, flags Postgres-isms and float money | tool |
| `check-invariants.py` | 29 behavioural cases: money identities, void, rollup, archival, session flags | tool |
| `explain-queries.py` | Checks every hot query's plan against a seeded database | tool |
| `capacity-bench.py` | The committed benchmark behind the bytes-per-bill numbers | tool |

## How to verify a change

```sh
python3 docs/check-schema.py          # DDL + migrations parse, indexes exist, no float money
python3 docs/check-invariants.py      # triggers behave: money identities, void, rollup, archival guard
python3 docs/explain-queries.py       # every hot query gets an index; fails on a bad seed
python3 docs/capacity-bench.py        # bytes per bill and archive cost, if you changed the row shape
```

All must print `ok` or `OK`. `check-schema.py` is deliberately shallow: it proves the files are valid SQLite.
`check-invariants.py` proves the triggers refuse the writes they are meant to refuse, which the DDL alone cannot
show. `explain-queries.py` proves an index is used, by seeding a database and reading `EXPLAIN QUERY PLAN`.
`capacity-bench.py` is the only source for footprint numbers; a number quoted in prose without a run of this
script is an estimate and should say so.
