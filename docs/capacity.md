# Capacity, measured on D1

Model: **one enterprise = one tenant = one D1.** The Worker is shared and routed by host (`DECISIONS.md`
D20); stores are rows inside the tenant's database, not separate databases. The question this file answers
is how long that database lasts and what happens when it fills.

Every number below comes from the 2026-11-14 runs against a real D1 database, or from the same schema on
local SQLite, including the committed benchmark for the post-0008 shape. Nothing here is extrapolated from
a page count without saying so.

## What was measured

Real D1, schema applied as migrations, 3,000 realistic 4-line bills: UUIDv7 ids, `payload_sha256`,
`invoice_no`, `staff_session_id`, JSON `price_breakdown`, HSN per line, one `inventory_movements` row per
line, `sales_daily` upsert per bill. Measured `page_count × page_size` before and after: 0.71 MB → 15.60 MB,
so **4,961 bytes per bill** (the headline number), about 42 rows including index entries (125,750 rows for
3,000 bills). That run predates 0008.

Local SQLite 3.51.0 (Python), modernc.org/sqlite v1.62.0 (SQLite 3.54.1, the terminal driver), and the
committed benchmark on the post-0008 schema, same write path:

| Schema | Shape | Bytes per bill | Source |
|---|---|---|---|
| pre-0008 | 4 lines | **4,961** | real D1, 3,000 bills |
| pre-0008 | 4 lines | 4,573 | local, Python and modernc agree |
| pre-0008 | 4 lines, no `inventory_movements`, no `idx_bill_lines_report` | 3,437 | local |
| pre-0008 | 10 lines | 9,659 | local |
| pre-0008 | 10 lines, no movements and no report index | 7,039 | local |
| post-0008 | 4 lines | 5,172 | `docs/capacity-bench.py 20000`, local |
| post-0008 | 4 lines, movements off | 3,961 | same |
| post-0008 | 10 lines | 11,840 | same |
| post-0008 | 10 lines, movements off | 8,809 | same |

0008 costs 13% at 4 lines (4,573 → 5,172) and 23% at 10 lines: new nullable columns on `bills` and
`bill_lines`, the rebuilt non-partial `idx_bills_store_date`, and the extra index entries. The D1 headline
stays 4,961 because the D1 run was on the pre-0008 schema; scale the post-0008 local numbers by the same
+8% when a decision depends on it.

The movement row and the line-report index are about a quarter of the bill (24% of the 4-line bill, 26%
of the 10-line bill, with 0008 applied). They are not optional: the report index is what makes a single-day
report index-only, and the movement is the stock ledger.

## Projection

| Load | Bills/year | Bytes/bill | Per month | Per year | No archival, 10 GB lasts |
|---|---|---|---|---|---|
| 5 stores × 600/day, 4 lines | 1,095,000 | 5,172 | 0.47 GB | 5.66 GB | **1.8 years** |
| 5 stores × 600/day, 10 lines | 1,095,000 | 11,840 | 1.08 GB | 12.96 GB | **0.8 years** |
| 10 stores × 1,000/day, 6 lines | 3,650,000 | ~6,300 (est) | 1.9 GB | 23 GB | **~5 months** |

Rows 1 and 2 use the committed benchmark on the post-0008 schema. The D1 headline (4,961 pre-0008) lands on
the same 1.8 years for row 1, which is why the schema drift does not change the decision.

Read the third row as an estimate, not a measurement: 6 lines sits between the two measured shapes, and the
2 GB/month is rounded from 23 GB/year.

Two rows of context, because they decide the architecture rather than the retention policy:

- Pooled (one database, `tenant_id` on every row) at 4,961 bytes: 50 stores × 600 bills/day ≈ 54 GB/year,
  so the pooled database fills in **~67 days**. That is `DECISIONS.md` D19, and the measurement supports it.
- Workers Free caps a database at 500 MB, which the 5-store case reaches in about five weeks. Workers Paid
  is a prerequisite (D24), and the Free plan's daily row quotas (5M read, 100k written) were exceeded by
  this very experiment.

## Retention, and why archival is v1

Deleting archived bills frees about **65%** of their footprint, because lines and movements go and
`sales_daily` stays. `sales_daily` is per item per day, bounded by items × days rather than by bill count,
so it stops growing with history once the catalog is stable. That is the whole reason the rollup exists.

D1 reclaims space on delete immediately (300 rows × 1 MB filled 301 MB; deleting half left 151 MB), so
there is no maintenance window and `VACUUM` is unnecessary. It is refused inside a transaction anyway.

Sizing rule from D22 and D18: keep the projected database at or below **70% of 10 GB**, which is 7 GB.

| Shape | Growth | 7 GB window |
|---|---|---|
| 5 stores × 600/day, 4 lines | 0.47 GB/month | **≈15 months** |
| 5 stores × 600/day, 10 lines | 1.08 GB/month | **≈6 to 7 months** |

So a five-store grocery with a normal basket keeps about 15 months of bills in D1, and the same shop with
ten-line averages keeps six or seven. `retention_months` is per tenant and the owner sets it in the admin
panel; the number above is the ceiling that keeps the database off the wall, not a policy.

The job (`deploy-inventory.md` §7, D22): per tenant, monthly, outside shop hours, at most 500 bills per
statement, one store-month at a time. Export the month to
`archive/<tenant>/<store>/<yyyy-mm>.jsonl.gz` (bills, lines, voids), verify row counts and the sum of
`total_minor` against D1, advance `schema_meta.archive_before`, delete that month's `bill_voids`, then
delete the bills and let lines and movements cascade. `sales_daily` stays. A `BEFORE DELETE` guard raises
`bill_not_archivable` for any bill newer than the watermark. R2 keeps at least 6 years, since GST record
retention outlives the database. An audit that needs a month loads it from R2 into a scratch D1; it is
never merged back.

The delete itself is a schema requirement, not an optimisation: without an index on
`inventory_movements(bill_id, bill_line_no)`, archiving 900 bills took 8,048 ms and read 73,463,700 rows.
With the index, 55 ms and 33,600 rows (`d1-limits.md`).

## The fallback

If a tenant projects above 70% of 10 GB even at 12 months retention, split it into one D1 per group of
stores (D18). Every billing row already carries `store_id`, and the catalog is store-scoped at the query,
so the split is a copy and a routing change, not a schema change. It costs a second binding, a second row
in `tenants.json`, and a migration per new database. Not v1, and not needed at the measured sizes.

Then re-measure: one store's bills are not the fleet average, and a wholesale counter is not a grocery.

## What was corrected

The earlier version of this file reported 1,067 bytes per bill from 10,000 bills × 4 lines in 10.7 MB, and
concluded 8.6 years of headroom with retention as a later decision. That figure does not reproduce on
either engine: the local floor for a complete 4-line bill was 4,573 bytes on the pre-0008 schema, and D1
charged 4,961. A bill whose lines, movements and rollup are written costs four to five times that number,
so 1,067 was measuring something that was not a complete bill, and the benchmark script it came from was
never committed.

Consequences, all applied here:

- 10 GB arrives in **1.8 years**, not 8.6, at 5 stores × 600 bills/day. Retention is now a v1 feature with
  a monthly job, a per-tenant window and a delete guard, not an open question.
- The "10 months" review this file used to dismiss implied about 11 KB per bill. The measurement says
  4,961 bytes, so that review was wrong by roughly half, not by ten times. Its sharding-by-store
  recommendation stays withdrawn, because the real answer is archival plus one database per enterprise.
- The 500 MB Free cap is reachable in weeks at these sizes, which is why Workers Paid is stated as a
  prerequisite rather than an upgrade path.

## When to revisit

- Any tenant above 70% of 10 GB at 12 months retention: shorten the window, or move to the store-group
  split. Alert at 70% exists so this is a decision, not an incident.
- More than ~20 stores, or an average above 15 lines per bill: re-measure. The table is linear by
  assumption and a wholesale counter will break that assumption.
- Any schema change that touches `bill_lines` or adds an index there: re-run the measurement. 0008 alone
  moved the 4-line bill from 4,573 to 5,172 bytes and the 10-line bill from 9,659 to 11,840.
- After the first real tenant month, compare actual growth to 0.47 GB/month. If actual is higher, the
  retention window was set too long on the day it was sold.

## Reproduce

```sh
python3 docs/capacity-bench.py 20000
```

The script seeds the real schema including 0008, writes bills with the full write path (headers, lines,
movements, `sales_daily`), and prints bytes per bill at 4 and 10 lines, with and without movements, on
local SQLite. It also prints the per-store growth at 5 stores × 600 bills/day and the years to 10 GB. This
is its expected output on 20,000 bills:

```
 4 lines, movements=yes   5172 B/bill   5.66 GB/year   1.8 years
 4 lines, movements=no    3961 B/bill   4.34 GB/year   2.3 years
10 lines, movements=yes  11840 B/bill  12.96 GB/year   0.8 years
10 lines, movements=no    8809 B/bill   9.65 GB/year   1.0 years
```

The D1 figures came from the audit run on a real database with the pre-0008 migrations applied, and the
archive deltas (15.60 → 12.67 MB for 900 bills, 13.07 → 9.41 MB for the next 900) from the same run. D1
runs about 8% above local at the 4-line shape; use local for ratios, and D1 for anything that decides a
capacity number.
