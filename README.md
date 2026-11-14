<p align="center">
  <img src="brand/traasu/lockup-bg.svg" alt="Traasu" width="440">
</p>

<h3 align="center">Bill sariga undali. — <em>Every bill, weighed right.</em></h3>

<p align="center">
Offline-first billing for Telugu grocery and department stores.<br>
Records every bill, reports per item per day and per month, keeps billing when the network drops,<br>
and syncs to Cloudflare when it returns.
</p>

<p align="center"><em>Design only — no application code yet.</em></p>

## What's here

- [`docs/DESIGN-BRIEF.md`](docs/DESIGN-BRIEF.md) — scope, stack, constraints
- [`docs/DECISIONS.md`](docs/DECISIONS.md) — every decision and what it costs
- [`docs/d1-schema.sql`](docs/d1-schema.sql) + [`docs/migrations/`](docs/migrations) — the database
- [`docs/d1-schema.md`](docs/d1-schema.md) — ER diagram and index map
- [`docs/api-routes.md`](docs/api-routes.md) — the Hono API
- [`docs/auth-better-auth.md`](docs/auth-better-auth.md) — the admin plane
- [`docs/release-management.md`](docs/release-management.md) — one Worker for every tenant, one database per enterprise
- [`docs/review-architecture.md`](docs/review-architecture.md) — the 2026-11-14 audit: tenancy, D1 measurements, retention
- [`docs/TRIAGE.md`](docs/TRIAGE.md) — what the reviews found and what we did about it
- [`brand/`](brand/) — mark, palette, typography

## Verify

```sh
python3 docs/check-schema.py      # DDL + migrations apply; no float money
python3 docs/check-invariants.py  # triggers refuse the writes they are meant to refuse
python3 docs/explain-queries.py   # every hot query gets an index
python3 docs/capacity-bench.py    # bytes per bill on the real schema
```

All must print `ok` or `OK`.
