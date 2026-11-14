# Verification prompt for a larger model

Paste the block below into a model with a large context window and repository access. It is written to be
self-contained: it names the files, the constraints, the load-bearing claims to attack, and the output shape.

---

You are auditing a **pre-code design** for an offline-first grocery billing POS. No implementation exists. Your
job is to find where the design is wrong or unbuildable, not to summarise it, not to praise it, and not to add
features.

## Read, in this order

1. `docs/DESIGN-BRIEF.md` — product scope and the constraints everything else obeys
2. `docs/DECISIONS.md` — D1–D17, the decisions with their costs
3. `docs/d1-schema.sql` and `docs/migrations/0002–0007` — the actual DDL
4. `docs/d1-schema.md` — ER diagram, table invariants, index-to-query map
5. `docs/api-routes.md` — routes, payloads, checkpoint contract, error taxonomy
6. `docs/deploy-inventory.md` — resources, provisioning, restore runbook
7. `docs/auth-better-auth.md` — the admin auth plane
8. `docs/TRIAGE.md` — disposition of 91 prior findings
9. `docs/review-sync.md`, `docs/review-schema.md`, `docs/review-security.md` — the adversarial reviews already run
10. `docs/query-catalogue.md`, `docs/d1-limits.md` — the yardsticks

Two harnesses exist and you may run them:

```
python3 docs/check-schema.py      # applies DDL + all migrations, flags Postgres-isms and float money
python3 docs/explain-queries.py   # EXPLAIN QUERY PLAN for the hot queries; fails on a bad seed
```

## Constraints you must hold while judging

- One Cloudflare Worker and one D1 database **per franchisee**. Tenant isolation is the database boundary.
- Terminal is Wails v3 + Go + SQLite; billing must work with no network and no valid token.
- No payments, no customers, no stock enforcement in v1. Bills are immutable; corrections are voids.
- Quantities are integers in base units; money is integer paise.
- Terminals send a **checkpoint** (`catalog_changes.seq`) on every catalog pull.
- D1 is SQLite: single writer per database, 10 GB cap that cannot be raised, 30 s query cap, 100 KB statement,
  100 bound parameters, 1000 queries per invocation.

## Four passes, in this order

**Pass 1 — decision audit.** For every decision D1–D17: `ACCEPT`, `CHANGE`, or `REJECT`, one sentence of
reasoning, and the smallest concrete change if not ACCEPT. Be willing to reject; a table of seventeen ACCEPTs
is a failed audit.

**Pass 2 — implementation attack.** Name the five places where a competent implementer will most likely
diverge from this design and produce a wrong bill, a lost bill, or a wrong price. For each: the exact sequence,
the row-level outcome, and the guard that would have caught it. Prioritise:

- the scalar checkpoint under concurrent catalog writes, and after a Time Travel restore
- per-bill batch push: replay, partial acceptance, `local_seq` collision, `payload_sha256` mismatch
- triggers as the enforcement layer: do they fire on D1, what do they cost per insert, what happens when the
  terminal's SQLite and D1 disagree about a constraint
- money identity: `subtotal + tax + rounding = total` with tax-inclusive items, line discounts, and tiered
  pricing; find a basket where it fails
- the staff-session window: a bill created before a logout but pushed after it; a cashier switching mid-cart
- Better Auth mapped onto our table names: which required column or unique constraint is missing

**Pass 3 — contradictions.** Find every place where two documents disagree, and say which one wins. Known
candidates: `DECISIONS.md` versus the DDL; `query-catalogue.md` versus the chosen journal design; the Obsidian
notes 10 and 11 on manager approval; `api-routes.md` versus `deploy-inventory.md` on migration ordering.

**Pass 4 — build order.** Sequence the work so the riskiest assumption is tested first. For each step: what it
proves, what it costs, and what you would abandon if it fails. Assume two engineers and no deadline pressure.

## Rules

- Cite `file:line` for every claim about the design. No citation, no finding.
- Do not restate the design back to me. Every sentence must be a judgement or a defect.
- Do not propose new products, modules, or dependencies. Schema, query, route and policy changes only.
- If a claim depends on an external fact — a D1 limit, SQLite behaviour, Better Auth's schema, Cloudflare's
  batch semantics — verify it or mark it `UNVERIFIED` with what you would check.
- Distinguish "the design is wrong" from "the design is fine but the implementation will be hard". Both matter;
  they are not the same finding.
- Judge the deferred items in `TRIAGE.md` section F: for each, say whether deferring is safe or whether it is a
  one-way door that must be decided before the first line of code.

## Output

1. A decision table: `ID | verdict | reason | smallest change`.
2. Five failure sequences, numbered, each with the guard.
3. A contradiction list with the winning document.
4. A build order with the first step being the one that could invalidate the design.
5. A short list of what you could not verify, and the cheapest experiment that would settle each.

Do not write code. Do not edit files. Return the report only.
