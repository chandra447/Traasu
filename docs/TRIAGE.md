# Triage — three adversarial reviews against the artifacts

Sources: `review-sync.md` (26 defects, 13 blockers), `review-schema.md` (48 defects, 24 S1),
`review-security.md` (17 findings, 2 critical). This file is the lead's ruling, not a summary.

The two reviews ran against the **brief**, while `d1-schema.sql` was being written. Several blockers were
therefore already closed by the schema before the review landed. I checked each one against the DDL rather
than trusting either side.

## Scoreboard

| Source | Raised | Closed by the artifact | Act now | Defer | Dismiss |
|---|---|---|---|---|---|
| Sync | 26 | 6 | 11 | 6 | 3 |
| Schema | 48 | 9 | 21 | 12 | 6 |
| Security | 17 | 6 | 8 | 2 | 1 |

## A. Already closed — verified in `d1-schema.sql`

| Finding | Evidence |
|---|---|
| Sync #1 "no cursor allocator" | `catalog_seq` single-row allocator + `catalog_changes(seq PK)` |
| Sync #2 "read-modify-write race" | `catalog_seq` reserves a block in the same batch; D1 is single-writer |
| Sync #7 "no tombstones" | `catalog_changes.op IN ('upsert','delete')` |
| Sync #9 "`store_id NULL` defeats the unique key" | `uq_item_prices_key` uses `IFNULL(store_id,'')` |
| Sync #12 "`bill_lines` has no identity" | `PRIMARY KEY (bill_id, line_no)` |
| Sync #26 "NORMAL loses the last commit" | not closed by schema; see B7 — listed here only because `devices.lease_expires_at` was mistaken for it |
| Schema "float money", "missing PK", "cascade on money parents" | every table `STRICT`, `bill_lines` PK, `ON DELETE RESTRICT` on money parents |
| Security F1 "terminal defines its own baseline" | `computed_list_price_minor`, `price_mismatch` on `bill_lines` |
| Security F3 "revoked terminal bills forever" | `devices.lease_expires_at` exists; the *policy* is still unenforced (B6) |

## B. Must land before any code

> **Status:** `docs/migrations/0002-sync-and-money-fixes.sql` and `0003-voids-and-coherence.sql` implement the
> schema half of B1–B5, B8 and B11–B13. Both apply cleanly on top of `d1-schema.sql` and are validated by
> `check-schema.py`. B6, B7, B9, B10 and B14 are terminal/API work, not schema.

### B1. Per-bill atomic, idempotent push (sync #13, #14, #16)

One `db.batch()` per bill, not per 100 bills. Pre-check `id` and `(store_id, terminal_id, local_seq)`, then
insert bill + lines + rollup in one batch. Lines use `ON CONFLICT (bill_id, line_no) DO NOTHING`; a replayed
bill must not duplicate lines. Add `bills.payload_sha256` so a replay with a different body is detectable
rather than silently ignored.

Why: one malformed bill currently rolls back 99 good ones, and a lost response duplicates every line.

### B2. `sync_epoch` and a full-resync path (sync #3, #4, #5)

`ALTER TABLE schema_meta ADD COLUMN sync_epoch INTEGER NOT NULL DEFAULT 1`. Bump it on any Time Travel restore
or destructive migration. `/v1/catalog` returns `epoch`; a terminal whose stored epoch differs discards its
catalog and applies a full snapshot. Retention: `catalog_seq.pruned_below` exists; when
`since < pruned_below`, return `410 stale_cursor` with the epoch, never a silent partial.

### B3. Price contract carries scope and validity (sync #11, schema)

The catalog payload must include `store_key` (the `IFNULL(store_id,'')` sentinel, or `''` for the default),
`valid_from`, `valid_to`, and `version` for every price row. Precedence stated once: store row wins per
`(item, qty_base)`, franchise default is the fallback. Without this a terminal cannot tell a removed override
from an absent one.

### B4. Bill header money identity (schema D1, D2)

```sql
ALTER TABLE bills ADD COLUMN subtotal_minor INTEGER NOT NULL DEFAULT 0;
ALTER TABLE bills ADD COLUMN tax_minor      INTEGER NOT NULL DEFAULT 0;
ALTER TABLE bill_lines ADD COLUMN taxable_minor INTEGER NOT NULL DEFAULT 0;
ALTER TABLE bill_lines ADD COLUMN price_includes_tax INTEGER NOT NULL DEFAULT 1;
ALTER TABLE bill_lines ADD COLUMN hsn TEXT;
```

with `CHECK (total_minor = subtotal_minor + tax_minor + rounding_minor)` and
`CHECK (line_total_minor = taxable_minor + cgst_minor + sgst_minor + cess_minor)`.

Why: `line_total_minor` currently means "tax-inclusive" for one item and "pre-tax" for another, and the header
cannot be reconciled against its lines. A bill with `total_minor = 0` and 40,000 of lines passes today.

### B5. `sales_daily` rollup (sync #21, #18)

```sql
CREATE TABLE sales_daily (
  store_id TEXT NOT NULL, business_date TEXT NOT NULL, item_id TEXT NOT NULL,
  qty_base INTEGER NOT NULL, amount_minor INTEGER NOT NULL,
  PRIMARY KEY (store_id, business_date, item_id)
) STRICT;
```

Upserted in the same batch as the bill. Month report reads 30 × items; day report still reads lines. A rebuild
statement regenerates it from `bill_lines` after a restore.

Why: a month report over `bill_lines` on a single-threaded D1 blocks every terminal's push and approaches the
30 s query cap.

### B6. Enforce the offline lease and revocation (security F3)

The column exists; the rule does not. Terminal refuses new bills when `now > lease_expires_at` or when it has
learned it is revoked. Default 7 days, configurable per store in `terminal_config`.

### B7. `synchronous=FULL` on the billing connection

WAL + `NORMAL` can lose the last committed bill on power loss. A grocery does ~1 bill/s; the measured cost of
`FULL` (≈41 bills/s) is irrelevant at that rate. Correctness wins.

### B8. Business date: server offset and a provisional watermark (sync #23, #24)

`business_date` is validated within ±1 day of the server's date (DECISIONS D11). Reports expose
`provisional_until` = the newest `received_at` seen, so an owner can tell "the day is closed" from "a terminal
has not synced yet".

### B9. Catalog completeness (sync #25)

`store_settings` (business day start, rounding) and `numbering_policy` are in `catalog_changes.entity` but must
also be in the pull payload, or the terminal cannot compute the business date it is required to send.

### B10. Report and export hygiene (security F4)

CSV export escapes leading `= + - @`; `override_note` and `item_name` render as text, never HTML; the CSRF
token is signed and bound to the session, not a readable double-submit value.


### B11. Voids need their own stream (schema agent, found while finishing)

A void is a new fact about an old bill, but the push is insert-only, so a void can never reach the server.
`bill_voids` (migration 0003) is an append-only stream that syncs exactly like bills. The terminal outbox now
has two tables, and `POST /v1/bills/voids` is a second route.

### B12. A bill must not claim another store's terminal

SQLite cannot add a composite FK by `ALTER`, so migration 0003 adds a trigger: a bill whose `terminal_id` does
not belong to its `store_id` is rejected on both D1 and the terminal.

### B13. Tax and discount identities

`igst_minor` (mutually exclusive with CGST/SGST), `list_amount_minor` with a discount identity trigger, and
`price_store_id` on the line so a store override applied at the wrong store is detectable.

### B14. Terminal-side rules that are not schema

`synchronous=FULL` on the billing connection (B7), the offline lease check (B6), `store_settings` in the
catalog payload (B9), and CSV/XSS hygiene (B10). These live in Go and the admin SPA.

## C. Deferred, with the trigger that reopens them

| Item | Trigger |
|---|---|
| ~~Shard D1 by store (sync #17)~~ | **withdrawn** — measured at 1,067 bytes/bill, a five-store enterprise reaches 10 GB in ~8.6 years. See `capacity.md`. The real task is retention + R2 archival, not sharding |
| Read-replication sessions / `first-primary` (sync #19) | only if read replication is enabled for a tenant |
| Argon2id for owner passwords (security) | before the second tenant; PBKDF2 is acceptable for one |
| Owner TOTP MFA | before go-live for any tenant with real money |
| Two-step enrolment approval (security F9) | before the first tenant outside the owner's own shop |
| Per-store D1 registry and R2 archive | with the sharding decision |

## D. Dismissed

| Finding | Why |
|---|---|
| Sync #6 "seq 10 before 9" | D1 is single-writer; commit order equals sequence order. A replica read cannot reorder the WAL. The residual risk is a *stale* read, which is safe because the terminal advances its checkpoint only after applying |
| Sync #16 "batch sizing ignores limits" | true of the brief's "batches of about 100", but the fix is B1 (per-bill batch), not a size calculator |
| Sync #22 "`GROUP BY item_name` splits renamed items" | the report groups by `item_id`; `item_name` is display only. Verified in `query-catalogue.md` |
| Schema "AUTOINCREMENT reuse" | we use an explicit allocator, not `AUTOINCREMENT` |
| Schema "seq gaps are a bug" | gaps are harmless for a cursor; only ordering matters |
| Security F12 "50 MB result cap" | a 5k-item catalog is ~2-3 MB; the cap matters for exports, which page |

## E. What this changes in DECISIONS.md

- D1 gains the epoch and the resync path (B2).
- D2 gains the header identity (B4) — verification needs a place to put the numbers.
- D3 unchanged.
- D4 becomes enforced, not just modelled (B6).
- New D12: per-bill atomic push (B1).
- New D13: `sales_daily` rollup (B5).
- New D14: `synchronous=FULL` (B7).

## F. Schema review D1–D48 — disposition

Every defect from `review-schema.md` is accounted for. "Closed" means a migration or a doc change in this
repo; "partial" means the cheap half landed and the rest is a named follow-up; "open" means deliberately not
done yet, with the trigger in `C`.

**Closed (14):** D1, D2, D3, D11, D12, D13, D18, D20, D26, D31, D37, D38, D40, D47.
Migrations 0002–0005 add the money identity, tax-inclusion copy, price basis, void stream, immutability
triggers, composite-store trigger, assertion column, `sales_daily`, the corrected outbox index, and price-row
immutability. D38 is the query catalogue rewrite; D47 is the migration runner itself.

**Partial (7):**

| Defect | Landed | Remaining |
|---|---|---|
| D4 tax rounding | `igst_minor`, exclusivity trigger | no stored per-rate tax sum on the bill |
| D9 rounding | identity check includes `rounding_minor` | rounding is not reported as its own line |
| D17 version scope | `price_store_id` on the line | version is still not globally unique per scope |
| D19 stale price | catalogue query fixed to resolve scope | server must use that query, not the naive one |
| D27 device identity | lease, unique prefix | `install_id` is not unique per device |
| D32 discount-as-list | `list_amount_minor` + identity trigger | `price_source` still terminal-asserted |
| D36 override report | partial index on live overrides | the query itself must filter `voided = 0` |

**Open — deferred (27):** D5, D6, D7, D8, D10, D14, D15, D16, D21, D22, D23, D24, D25, D28, D29, D30, D33,
D34, D35, D39, D41, D42, D43, D44, D45, D46, D48.

The three that will bite first, in order:

1. **D16/D17 half-open price revisions** — a tier set can be split across versions, so a terminal applying
   v7 may hold one tier at v6. Fix: a `revision_id` on every tier of one write, and apply by revision.
2. **D42 business day not derivable from the row** — add `business_day_start_minutes` snapshot to the bill, or
   the day report changes meaning when a store changes its cutover time.
3. **D45 positive-only quantities** — returns will need negative lines or a separate return document; decide
   before the first return, not after.

## G. What is still not verified

- No `EXPLAIN QUERY PLAN` against a *seeded, large* database. The harness seeds one bill; plans on 10 rows and
  plans on 10 million are not the same claim.
- No load test of the per-bill push against a real D1.
- The mermaid ER was parsed by mermaid's own parser (schema agent), not rendered in this repo's viewer.

## H. Architecture review, 2026-11-14

`review-architecture.md` ran against the design after D17–D21, with experiments on real D1. Its five failure
sequences and fifteen contradictions are closed by migration 0008, the rewritten 0007, the revised D12–D21
and the new D22–D25; the review lists each one with where it was closed.

Changes to this file's earlier rulings:

- §C "Per-store D1 registry and R2 archive — with the sharding decision": R2 archival is v1 (D22); the
  store-group split stays the fallback (D18).
- §C "Argon2id for owner passwords": moot; Better Auth hashes with scrypt (D17).
- §C "Owner TOTP MFA": Better Auth `twoFactor`; table `auth_two_factor` (0007).
- §C "Two-step enrolment": in the route table now (D5 revision).
- §F open items closed by D25: D30, D33, D42, D44, D45, D46. D16/D17 price revisions remain open, to be
  decided before the price editor is built.
- §G "No EXPLAIN against a seeded, large database": bytes per bill and archival delete cost are now
  measured on real D1; query plans are still checked on a small seed (`explain-queries.py`).
