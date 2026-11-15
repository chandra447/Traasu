# Architecture review — D1, tenancy, money, auth (2026-11-14)

Adversarial pass over the whole design after D17–D21, followed by experiments on real Cloudflare D1, a local
D1 (miniflare) and the terminal's SQLite driver. Every claim below was measured or reproduced; the few that
were not are listed at the end. Dispositions point at the migration or document that closes each finding.
Earlier reviews (`review-sync.md`, `review-schema.md`, `review-security.md`) are not repeated.

## Measurements

| Question | Result | Where |
|---|---|---|
| Bytes per realistic 4-line bill | **4,961** on D1 (3,000 bills, 0.71 → 15.60 MB); 4,573 local; 9,659 local at 10 lines | `capacity.md`, `capacity-bench.py` |
| Does D1 free space on DELETE? | Yes, at once: 301 → 151 MB; 900 bills 15.60 → 12.67 MB. `VACUUM` refused, not needed | D22 |
| Archival delete cost | 8,048 ms and 73,463,700 rows read for 900 bills without an index on `inventory_movements(bill_id, bill_line_no)`; 55 ms and 33,600 rows with it | 0008 |
| D1 calls per Worker invocation | 1,000; a `db.batch()` counts once (100 × 14 statements OK, 1 × 5,000 OK, 1,001 batches fail) | D12, `d1-limits.md` |
| Rows written per bill | ~42 including index entries (125,750 for 3,000 bills) | D24 |
| Free plan | 500 MB per database, 100k rows written and 5M read per day; both daily quotas exhausted by the test | D24 |
| Better Auth 1.7.23 on our tables | refused at startup (columns), then `cannot store TEXT value in INTEGER column` (ISO dates); works on separate `auth_*` tables with TEXT dates | 0007, D17 |
| Cached `betterAuth()` across hosts | tenant B's owner and session written into tenant A's D1 | D20 |
| Terminal driver | `modernc.org/sqlite` v1.62.0 = SQLite 3.54.1; full DDL applies | — |

## Failure sequences found

1. **Cross-tenant: Host vs token.** The device middleware compared the claim with a `TENANT_ID` environment
   constant that one Worker no longer has. Deriving the HKDF key from the claim makes A's token verify at B;
   the seed gave every tenant the same store and terminal ids, so the claims resolve in B's database; a
   missing `devices` row read as "not revoked". Variant: a module-level Better Auth instance (reproduced).
   *Closed:* tenant resolution in D20, random provisioning ids (0001 seeds), per-request auth instance.
2. **Money identity.** Round-half-up on both GST halves aborted a ₹10.29 sale on the terminal; the line
   identity omitted IGST; the header was never reconciled with its lines; the discount identity had a
   bypass; a concurrent replay double-counted `sales_daily`. *Closed:* D23 and 0008 triggers; rollup by
   trigger (D13).
3. **Staff sessions.** Duplicate session ids were acked but never updated, so the server's copy stayed open
   and the next session failed `uq_staff_sessions_live`, parking every bill behind it; a 2 s clock step and
   a cart finished after switch-user aborted the sale on the terminal. *Closed:* D16 revision, 0008 drops the
   two triggers, session upsert in `api-routes.md`.
4. **Lagging tenant.** Release ran deploy → migrate; no migration set `schema_version` (1 after 0007);
   columns the API depended on (`unverified_shift_id`, `assertion_verified_at`) existed in no migration.
   *Closed:* migrate → deploy with `min_schema` (D21, `release-management.md`), every migration sets its
   version, 0008 adds the columns.
5. **Restore loses bills.** A Time Travel restore drops bills the terminals have already marked synced;
   the epoch bump reset only the catalog. *Closed:* epoch change also triggers a re-push of bills, voids and
   sessions since the restore point (`api-routes.md` §4); terminals keep 30 days of synced bills (D22).

## Contradictions resolved

| Was | Now |
|---|---|
| Per-tenant Worker, KV and secrets (`deploy-inventory.md`, brief, summary, schema header) vs one Worker (D20) | one Worker everywhere; per-tenant Worker only as the contracted exception |
| Deploy → migrate (`release-management.md`) vs migrate → deploy (`deploy-inventory.md`) | migrate → deploy |
| JWT 15 min vs 5 min | 5 min |
| Session window rejects (D16, 0006, `api-routes.md` §9) vs accept and flag (`api-routes.md` §3.4) | accept and flag |
| No rollup and no immutability triggers (`d1-schema.md`) vs D13, D15 | D13, D15 |
| Custom PBKDF2 admin sessions (`api-routes.md`, `query-catalogue.md`) vs Better Auth (D17) | Better Auth, separate tables |
| Journal of references then fetch by id vs embedded documents | embedded |
| Month/year report rule vs 92 days | 92 days |
| ±1-day business date vs offline-safe check | offline-safe check plus the cutover on the bill |
| `POST /v1/bills/voids` vs `/v1/bills/:id/void`; `bill_voids` vs `bills.voided` | the route; `bill_voids` is the record and a trigger sets the flag |
| Revoked terminal keeps billing (`d1-schema.md`) vs D4 | D4 |
| `UNIQUE(credential_prefix)` claimed but non-unique | unique (0008) |
| `synchronous=NORMAL` (`SUMMARY.md`) vs D14 | FULL |
| `capacity.md` 1,067 B/bill and a benchmark "in this file's history" | 4,961 B/bill, script committed |
| `explain-queries.py` "fails on a bad seed" while `INSERT OR IGNORE` dropped items, prices and identifiers | plain INSERT, empty-table check |

## Decisions recorded

D12, D13, D15–D21 revised; D22 archival, D23 money rules, D24 Workers Paid, D25 one-way doors. See
`DECISIONS.md`.

## Not verified

| Claim | Cheapest check |
|---|---|
| `PRAGMA defer_foreign_keys` inside a remote `db.batch()` behaves as in SQLite (verified locally only) | one batch on a scratch D1: line, then header, then commit; and an orphan line that must fail |
| Daily-quota enforcement on Free for queries that read rows (our over-quota probes read none) | moot on Paid (D24) |
| `auth_rate_limit` counters under load (table and mapping verified; counters not exercised) | burst of sign-in failures on a local D1 |
| Better Auth scrypt CPU on Workers | 50 concurrent logins on a deployed Worker |
| Google OAuth redirect-URI limit per client | Google Cloud console |
| GST record-retention period | the owner's accountant |
| Lines per bill for this chain | owner's estimate or one week of receipts |
