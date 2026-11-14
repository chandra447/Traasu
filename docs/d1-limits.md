# D1 limits that shape this schema

Verified against Cloudflare's D1 limits page (fetched 2026-11-14). Every number below has a design consequence;
the consequence column is the part that matters.

| Limit | Value | Consequence for us |
|---|---|---|
| Database size | 10 GB (Workers Paid), 500 MB (Free), **not increasable** | One D1 per franchisee. A busy store writes ~1-2 KB per bill; 10 GB is millions of bills, so the cap is a decade-scale concern, not a launch one — but reports must never scan the whole table |
| Single-threaded per database | one writer | Makes a `seq` counter safe without locks, and makes commit order equal sequence order. This is what lets the checkpoint be a scalar |
| Queries per Worker invocation | 1000 (Paid), 50 (Free) | A bill push of 100 bills × ~6 statements must go through `db.batch()`; a batch counts as **one** query against this limit |
| SQL statement length | 100 KB | Build statements per row, never one giant multi-row insert with inlined JSON |
| Bound parameters per query | 100 | Cannot bind 100 bills × 8 columns in one statement. One statement per row, or per bill + per line |
| Max row / string / BLOB | 2 MB | `price_breakdown` JSON and catalog payloads are safe; a base64 image on an item is not |
| Columns per table | 100 | Not close, but it caps "just add a column" thinking on `bill_lines` |
| Query duration | 30 s | A month report over a large `bill_lines` will hit this before it hits the size cap. Pre-aggregate or restrict the range |
| Query result size | 50 MB (from 2026-11-04) | A full catalog pull must paginate; a 5k-item catalog is ~2-3 MB, fine, but a report export must stream or page |
| Time Travel | 30 days | A botched migration is recoverable for a month. Worth stating in the runbook |
| Databases per account | 1,000,000 (Paid), first 100k included | One per franchisee is affordable; the provisioning script is the real cost |

## What this rules out

- **Interactive transactions across requests.** Atomicity comes from `db.batch()`, so a bill + its lines +
  the outbox row must be one batch, not a sequence of awaited statements.
- **A cursor built from `updated_at`.** Single-writer ordering is available; use it instead of wall-clock
  comparisons that break under skew and equal-millisecond writes.
- **Unbounded report queries.** The 30 s and 50 MB caps are the real ceiling, not the 10 GB one. Any report
  that can scan a year of lines needs a pre-aggregated table or a hard range limit.
- **One statement per batch of bills.** The 100-parameter cap forces per-row statements; the 100 KB cap
  forces per-row JSON.

## The numbers worth re-checking before launch

- Whether `db.batch()` still counts as one query against the invocation limit (it did at fetch time).
- Whether read replication is enabled for the tenant, and if so, that the catalog pull reads from the primary
  or tolerates a stale checkpoint. A stale read is safe only because the terminal advances its checkpoint
  after applying — never before.
