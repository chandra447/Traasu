# Traasu

**Bill sariga undali.** — *Every bill, weighed right.*

Offline-first billing for Telugu grocery and department stores. Records every bill, reports per item per day
and per month, keeps billing when the network drops, and syncs to Cloudflare when it returns.

Design only — no application code yet.

## What's here

- [`docs/DESIGN-BRIEF.md`](docs/DESIGN-BRIEF.md) — scope, stack, constraints
- [`docs/DECISIONS.md`](docs/DECISIONS.md) — every decision and what it costs
- [`docs/d1-schema.sql`](docs/d1-schema.sql) + [`docs/migrations/`](docs/migrations) — the database
- [`docs/d1-schema.md`](docs/d1-schema.md) — ER diagram and index map
- [`docs/api-routes.md`](docs/api-routes.md) — the Hono API
- [`docs/TRIAGE.md`](docs/TRIAGE.md) — what the reviews found and what we did about it
- [`docs/release-management.md`](docs/release-management.md) — one deployment per enterprise, pinned versions

## Verify

```sh
python3 docs/check-schema.py      # DDL + migrations apply; no float money
python3 docs/explain-queries.py   # every hot query gets an index
```

Both must print `ok` and `OK`.
