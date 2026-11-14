# Capacity — measured, not estimated

Model: **one enterprise = one tenant = one Worker + one D1.** Stores are rows inside that database, not
separate databases. The question this file answers is whether that holds as an enterprise grows.

## Measurement

Seeded the real schema (DDL + all migrations), then inserted 10,000 bills across 5 stores, 5 terminals and
5 cashiers, 4 lines per bill, with the full write path: `bills`, `bill_lines`, `inventory_movements`, and the
`sales_daily` upsert. Measured `page_count × page_size` before and after.

```
10,000 bills × 4 lines in 0.6s
10.7 MB used  →  1,067 bytes per bill
```

## Projection

| Load | Per year | Years to the 10 GB cap |
|---|---|---|
| 5 stores × 600 bills/day, 4 lines | 1.17 GB | **8.6** |
| 5 stores × 600 bills/day, 10 lines | ~2.9 GB | **3.4** |
| 10 stores × 1,000 bills/day, 6 lines | ~3.5 GB | **2.9** |

The cap is not the constraint anyone thought it was. A five-store enterprise at realistic grocery basket sizes
has three to eight years of headroom before storage matters, and the answer at that point is **archival**, not
sharding: move bills older than the retention window to R2 and keep the aggregates in `sales_daily`.

## What this corrects

An earlier review claimed a five-store franchisee would hit 10 GB in "about 10 months". That implied roughly
11 KB per bill — about ten times the measured figure. It was an estimate presented as arithmetic, and it drove
a recommendation to shard by store before the first tenant. That recommendation is withdrawn.

## What still needs a decision

Not sharding. **Retention.** Pick the window (24 months is a reasonable default for a grocery), then:

1. Nightly job exports bills older than the window to R2 as Parquet or JSONL, partitioned by store and month.
2. Delete them from D1 in the same job, after the export is verified.
3. `sales_daily` stays — it is per item per day, bounded by items × days, not by bills.
4. The restore path: re-import a month from R2 into a scratch D1 if an audit needs it.

## When to revisit

- Any single enterprise projecting above 6 GB: start archival early, do not wait for the cap.
- A tenant with more than ~20 stores: re-measure, because the per-store assumption in the table above is linear
  and reality will not be.
- If `bill_lines` grows to 15+ lines per bill on average (a wholesale counter, not retail), re-run the
  measurement before trusting the table.

## Reproduce

The benchmark is the script in this file's history: seed 5 stores / 5 terminals / 5 cashiers / 2,000 items,
insert 10,000 bills with 4 lines each and a movement per line, then compare `page_count`. It runs in under a
second on SQLite, so there is no excuse for estimating.
