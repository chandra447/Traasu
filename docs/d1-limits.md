# D1 limits that shape this schema

Every number below has a design consequence; the consequence column is the part that matters. These were
verified on a real D1 database on 2026-11-14 (account `d5d2318e…`, Workers Free plan), not read off the
documentation, except where the row says "documented".

| Limit | Value | Consequence for us |
|---|---|---|
| Database size | 10 GB (Paid), 500 MB (Free), **not increasable** (documented) | One D1 per franchisee. A measured 4-line bill costs 4,961 bytes on D1 and 5,172 on the post-0008 schema locally, so 5 stores × 600 bills/day is 5.4 to 5.7 GB/year and reaches 10 GB in 1.8 years without archival. Archival ships in v1 (D22), not later |
| Single-threaded per database | one writer | Makes a `seq` counter safe without locks, and makes commit order equal sequence order. This is what lets the checkpoint be a scalar |
| D1 API calls per Worker invocation | 1,000 | A bill push goes through `db.batch()`, which counts as **one** call whatever the statement count. 100 bills = 100 calls. Measured table below |
| SQL statement length | 100 KB (documented) | Build statements per row, never one giant multi-row insert with inlined JSON |
| Bound parameters per query | 100 (documented) | Cannot bind 100 bills × 8 columns in one statement. One statement per row, or per bill plus per line |
| Max row / string / BLOB | 2 MB (documented) | `price_breakdown` JSON and catalog payloads are safe; a base64 image on an item is not |
| Columns per table | 100 (documented) | Not close, but it caps "just add a column" thinking on `bill_lines` |
| Query duration | 30 s (documented) | A month report over a large `bill_lines` hits this before the size cap. Pre-aggregate or restrict the range |
| Query result size | 50 MB (documented) | A full catalog pull must paginate; a 5k-item catalog is ~2-3 MB, fine, but a report export must stream or page |
| Space on DELETE | reclaimed immediately; `VACUUM` is refused inside a transaction and is not needed | Archival deletes are the storage lever. 300 rows × 1 MB filled 301 MB; deleting half brought it to 151 MB with no maintenance step |
| FK cascade cost | every child lookup needs an index on the child's FK columns | Without `inventory_movements(bill_id, bill_line_no)`, deleting 900 bills took **8,048 ms and read 73,463,700 rows**. With it: **55 ms and 33,600 rows** |
| Free plan daily quotas | 5M rows read, 100k rows written per day (documented) | The audit run exceeded both. Production requires Workers Paid (D24) |
| Time Travel | 30 days (documented) | A botched migration is recoverable for a month. Pair it with the terminal re-push, not with the R2 archive |
| Databases per account | 1,000,000 (Paid), first 100k included (documented) | One per franchisee is affordable; the provisioning script is the real cost |

## Measured: the invocation limit

The documented Free limit is 50 D1 calls per invocation. It did not apply on this account; the ceiling was
1,000 either way, and a batch cost one call.

| Case | Result |
|---|---|
| 1,000 single queries | OK |
| 1,001 single queries | fail: `Too many API requests by single Worker invocation` |
| 1,001 batches of 1 statement | fail |
| 100 batches × 14 statements | OK |
| 1 batch × 5,000 statements | OK |

So a request may structure work as either 1,000 calls or as batches whose statement count is bounded by
the 100 KB statement cap and the 2 MB row cap, not by the call budget. The bill push uses one batch per
bill and at most 100 bills per request: 100 calls against a budget of 1,000.

## Measured: delete, cascade, and what a bill costs

| Measurement | Result |
|---|---|
| 3,000 realistic 4-line bills (UUIDv7 ids, `payload_sha256`, `invoice_no`, `staff_session_id`, JSON `price_breakdown`, HSN, one movement per line, `sales_daily` upsert) | 0.71 MB → 15.60 MB, **4,961 bytes/bill** |
| Rows written per bill, index entries included | ~42 (125,750 rows for 3,000 bills) |
| Archive delete of 900 bills (cascade to lines and movements, `sales_daily` kept) | 15.60 → 12.67 MB, about 65% of their footprint |
| Second 900 bills | 13.07 → 9.41 MB |
| Same delete without the child index | 8,048 ms, 73,463,700 rows read |
| Same delete with `inventory_movements(bill_id, bill_line_no)` | 55 ms, 33,600 rows |
| `VACUUM` after the deletes | refused: `cannot VACUUM from within a transaction`; not needed |

The cascade is the item that is easy to miss. D1 enforces foreign keys, so deleting a `bill_lines` row
makes SQLite look for children in every table that references it. Without an index that lookup scans the
whole child table once per deleted line, which is 73 million rows for a 900-bill store-month, and the
30 s query cap is not the limit that stops you there; the row-read budget is.

## What this rules out

- **Interactive transactions across requests.** Atomicity comes from `db.batch()`, so a bill, its lines,
  its movements and its header must be one batch, not a sequence of awaited statements.
- **A cursor built from `updated_at`.** Single-writer ordering is available; use it instead of wall-clock
  comparisons that break under skew and equal-millisecond writes.
- **Unbounded report queries.** The 30 s and 50 MB caps are the real ceiling, not the 10 GB one. Reports
  are capped at 92 days, and multi-day ranges read `sales_daily`.
- **One statement per batch of bills.** The 100 KB statement cap and the 100-parameter cap force per-row
  statements. Batching removes the call limit, not the parameter limit.
- **An archival delete without the child index.** 65 rows deleted per bill means a store-month can read
  tens of millions of rows without it. The index is part of the archive design, not an optimisation.
- **Free plan anything.** 500 MB per database and 100k rows written per day are below one busy tenant's
  first year, and the capacity experiment exceeded both quotas in one run. Workers Paid is a launch
  prerequisite (D24).

## Still worth re-checking before launch

- Whether read replication is enabled for a tenant, and if so, that the catalog pull reads from the primary
  or tolerates a stale checkpoint. A stale read is safe only because the terminal advances its checkpoint
  after applying, never before.
- The 1,000-call ceiling and the one-call batch rule were measured on a Free-plan account. Re-run the same
  five cases once on Paid, because the documented Free number (50) was already wrong on this account and
  the paid boundary is the one production runs against.
