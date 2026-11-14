# Verification prompt — v2

Paste the block below into a model with a large context window and repository access. It supersedes v1: the
compute model, the release process, the auth plane and the capacity numbers have all changed since.

---

You are auditing a **pre-code design** for an offline-first grocery billing POS. No implementation exists. Your
job is to find where the design is wrong or unbuildable — not to summarise it, not to praise it, and not to add
features.

## Read, in this order

1. `docs/DESIGN-BRIEF.md` — scope and the constraints everything obeys
2. `docs/DECISIONS.md` — **D1–D21**, each with its cost
3. `docs/d1-schema.sql` and `docs/migrations/0002–0007` — the DDL
4. `docs/d1-schema.md` — ER diagram, invariants, index-to-query map
5. `docs/api-routes.md` — routes, payloads, checkpoint contract, error taxonomy
6. `docs/release-management.md` — one Worker, N databases, stages, migration loop
7. `docs/deploy-inventory.md` — resources, provisioning, restore
8. `docs/auth-better-auth.md` — admin auth plane
9. `docs/capacity.md` — the measured storage numbers
10. `docs/TRIAGE.md` — disposition of 91 earlier findings
11. `docs/review-sync.md`, `docs/review-schema.md`, `docs/review-security.md` — **historical**, run against an
    earlier version. Do not repeat their findings; judge whether the fixes hold and whether the new decisions
    introduced new ones.
12. `docs/query-catalogue.md`, `docs/d1-limits.md`, `brand/README.md`, `brand/typography.md`

Harnesses you may run:

```
python3 docs/check-schema.py      # DDL + all migrations apply; flags Postgres-isms and float money
python3 docs/explain-queries.py   # EXPLAIN QUERY PLAN for the hot queries; fails on a bad seed
```

## What changed since the last review — do not re-litigate these without new evidence

| Decision | Evidence it rests on |
|---|---|
| D18 one enterprise = one D1, stores are rows | measured 1,067 bytes/bill, ~8.6 years to the 10 GB cap at 5 stores × 600 bills/day |
| D19 sharded by enterprise, not pooled | a pooled D1 hits 10 GB in 312 days at five enterprises |
| D20 one Worker, N databases | per-tenant Workers were judged hand-holding, not isolation |
| D21 control-plane D1 for metadata, routing compiled | D1 is binding-only; there is no runtime open-by-id |
| D17 Better Auth for admin, custom device plane | Better Auth has native D1 support and Hono mounting |

If you think one of these is wrong, say so with a number or a documented behaviour, not a preference.

## Constraints you must hold

- One Worker serves every tenant, routed by hostname. One D1 per enterprise. Tenant isolation is the database
  boundary.
- Terminal is Wails v3 + Go + SQLite; billing must work with no network and no valid token.
- No payments, no customers, no stock enforcement in v1. Bills immutable; corrections are voids.
- Quantities are integers in base units; money is integer paise.
- Terminals send a checkpoint (`catalog_changes.seq`) on every catalog pull.
- D1: single writer per database, 10 GB cap that cannot be raised, 30 s query cap, 100 KB statement, 100 bound
  parameters, 1000 queries per invocation.

## Four passes

**Pass 1 — decision audit.** For every D1–D21: `ACCEPT`, `CHANGE`, or `REJECT`, one sentence of reasoning, and
the smallest concrete change if not ACCEPT. A table of twenty-one ACCEPTs is a failed audit.

**Pass 2 — implementation attack.** The five places where a competent implementer produces a wrong bill, a lost
bill, a wrong price, or a cross-tenant leak. For each: the exact sequence, the row-level outcome, and the guard
that would have caught it. Prioritise:

- **Host routing vs token claims.** Routing is by `Host`; the token carries `tenant_id`. What happens when a
  request arrives on tenant B's hostname carrying tenant A's token, or vice versa? Name the check that must
  exist and where.
- **Per-tenant signing keys via HKDF(master, tenant_id).** What does a leak of the master imply? Does a token
  minted for A verify at B if the claim check is ever skipped?
- **The migration loop that stops on failure.** Tenants end up on different schema versions. Find a query or a
  route that breaks for the lagging tenant.
- **The capacity measurement.** 4 lines per bill, 600 bills/day/store, no audit growth, no WAL, no vacuum. Say
  which assumption is most likely wrong and by how much, and recompute the years-to-cap if it is.
- **Better Auth mapped onto our table names.** `sessions` has `token_hash`; Better Auth looks sessions up by a
  `token` column. Name every required column or unique constraint that is missing or wrong.
- **The staff-session window and the triggers.** A bill created before a logout but pushed after it; a cashier
  switching mid-cart; a trigger that fires on D1 but not on the terminal's SQLite build.
- **Money identity** under tax-inclusive items, line discounts and tiered pricing — find a basket that fails
  `subtotal + tax + rounding = total`.

**Pass 3 — contradictions.** Every place two documents disagree, and which one wins. Known candidates:
`DECISIONS.md` vs the DDL; `query-catalogue.md` vs the journal design; `release-management.md` vs
`deploy-inventory.md` on what is per-tenant; `auth-better-auth.md` vs the `sessions` table.

**Pass 4 — build order.** Sequence the work so the riskiest assumption is tested first. For each step: what it
proves, what it costs, what you would abandon if it fails. Assume two engineers, no deadline.

## Rules

- Cite `file:line` for every claim about the design. No citation, no finding.
- Every sentence is a judgement or a defect. Do not restate the design.
- No new products, modules or dependencies. Schema, query, route and policy changes only.
- External facts — D1 limits, SQLite behaviour, Better Auth's schema, Cloudflare batch semantics, HKDF
  properties — verify or mark `UNVERIFIED` with what you would check.
- Distinguish "the design is wrong" from "the design is fine but the implementation will be hard".
- Judge the deferred items in `TRIAGE.md` section F: for each, say whether deferring is safe or whether it is a
  one-way door that must be decided before the first line of code.

## Output

1. Decision table: `ID | verdict | reason | smallest change`.
2. Five failure sequences, numbered, each with its guard.
3. Contradiction list with the winning document.
4. Build order, first step being the one that could invalidate the design.
5. What you could not verify, and the cheapest experiment that would settle each.

Do not write code. Do not edit files. Return the report only.
