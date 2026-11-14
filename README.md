# Traasu — offline-first grocery billing POS

Design repository. **No application code yet.** Everything here is the specification a build will follow:
the data model, the API, the sync contract, the auth model, and the adversarial reviews that shaped them.

The product: a counter billing system for Telugu grocery and department stores. Records every bill, reports
per item per day and per month, works with no network, and syncs to Cloudflare when it can. No payments in v1.

## Read in this order

| # | File | Why |
|---|---|---|
| 1 | [`docs/DESIGN-BRIEF.md`](docs/DESIGN-BRIEF.md) | Scope, stack, and the constraints everything else obeys |
| 2 | [`docs/DECISIONS.md`](docs/DECISIONS.md) | D1–D17, each with the cost of the choice |
| 3 | [`docs/d1-schema.sql`](docs/d1-schema.sql) | The DDL, plus `docs/migrations/` |
| 4 | [`docs/d1-schema.md`](docs/d1-schema.md) | ER diagram, table invariants, index-to-query map |
| 5 | [`docs/api-routes.md`](docs/api-routes.md) | Routes, payloads, checkpoint contract, error taxonomy |
| 6 | [`docs/deploy-inventory.md`](docs/deploy-inventory.md) | What exists per tenant, provisioning, restore |
| 7 | [`docs/auth-better-auth.md`](docs/auth-better-auth.md) | Admin auth plane; the device plane is separate |
| 8 | [`docs/TRIAGE.md`](docs/TRIAGE.md) | Disposition of 91 review findings |
| 9 | [`docs/review-*.md`](docs/) | The adversarial reviews, unedited |
| 10 | `SUMMARY.md` | The original design summary this grew from |

## Verify it yourself

```sh
python3 docs/check-schema.py      # applies DDL + every migration; flags Postgres-isms and float money
python3 docs/explain-queries.py   # EXPLAIN QUERY PLAN for the hot queries; fails on a bad seed
```

Both must print `ok` / `OK`. They run on plain SQLite, no dependencies.

## The three decisions that matter most

1. **Catalog sync is a change journal, not a timestamp.** Terminals send `catalog_changes.seq`; the server
   answers with everything after it. D1's single writer makes commit order equal sequence order, which is what
   makes a scalar checkpoint safe.
2. **Bills are immutable and the server verifies, never rewrites.** The Worker recomputes the list price and
   the discount ceiling from its own price table and records the delta. The charged amount stays as the
   terminal reported it — the customer already paid.
3. **Offline caps authority, not the sale.** Past the store's ceiling with no approval grant, the line falls
   back to list price and the sale still completes.

## Status

- Schema: 30 tables, 36 indexes, 7 migrations, all executing clean.
- API: route inventory and payloads written; no implementation.
- Reviews: 91 findings raised, all dispositioned in `TRIAGE.md` (40 acted on, 27 deferred with triggers,
  10 dismissed with reasons).
- Open: D1 sharding decision (one-way door), offline lease length, retention policy.

## Contributing

Design changes go through `docs/DECISIONS.md`: add an entry, state the cost, then change the artifact. A schema
change is a new numbered migration — never an edit to an applied one. Run both harnesses before opening a PR.
