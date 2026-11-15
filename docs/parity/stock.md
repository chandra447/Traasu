# Parity & robustness audit — slice `stock`

Batches (D27), books (D28), purchase/opening stock, adjustments and counts, the stock sync cursor and
the register's on-hand estimate, voids and archival vs the stock ledger, per-batch price changes.

Everything below is against `docs/d1-schema.sql` + `docs/migrations/0008-audit-fixes.sql` with
`PRAGMA foreign_keys=ON`, exactly as D1 and the register run it. `python3 check-schema.py`,
`check-invariants.py`, `explain-queries.py` all pass (unchanged; I edited nothing in the repo).

Repro scripts: `docs/parity/repro/stock_repros.py`, `catalog_apply.py`, `estimate_sim.py`
(scratch dir for this agent). Citations by file name under `reference/gofrugal/md/`.

---

## 1. Verdicts

| # | Use case | GoFrugal (page) | Traasu (D#, file) | Verdict | Severity |
|---|---|---|---|---|---|
| S1 | A stock receipt creates a batch with batch no., MRP, price, qty, optional expiry | Sales-Batch-LOV.md; community-batch-and-expiry.md | D27, D28; 0008 `item_batches`, `stock_receipts` | match (Traasu is stricter: one receipt line = one new batch) | — |
| S2 | At billing the price follows the batch; a popup lists the batches | serveeasy-Sales-Batch-LOV.md | D27, D28; `query-catalogue.md` §3 | match | — |
| S3 | MRP is the ceiling a batch may be sold at ("Change Selling" edits price **and** MRP together) | community-change-selling-28-10-2025.md | D27 ("Indian law forbids charging above the printed MRP"); C5 | **defect** | High |
| S4 | An expired batch cannot be sold | community-batch-and-expiry.md ("struck out") | D27; C4 | **risk** | High |
| S5 | Batch pick + prompt when another batch differs in MRP/price | serveeasy-Sales-Batch-LOV.md; D28's table | D28; `query-catalogue.md` §3 | match | — |
| S6 | Batch list sorted FIFO or short-expiry first | community-batch-sorting-6-8-2020.md | D27 ("newest first, owner's requirement"); C8 | **differs-by-design** (D27) — holds for staples, fails for perishables | High |
| S7 | Godown-wise stock (purchase picks a godown; multi-godown) | community-godown-wise-transactions-6-8-2020.md | D28 `books`, `item_batches.book_id` | **differs-by-design** (D28) — holds (one GSTIN/series per store; GoFrugal's per-godown bill prefix is desktop chrome) | Low |
| S8 | Godown transfer | community-godown-wise-transactions…md | D28 "Move stock between books (a transfer, not built yet)" | **gap** (a v1 non-goal: "transfers") — but the shape D28 describes cannot be built | Medium |
| S9 | Purchase entry (supplier, cost, invoice no.) | Making-purchase-entry.md | D28 `POST /v1/admin/stock-receipts`; supplier/cost are non-goals | differs-by-design (D28) — holds | Low |
| S10 | Opening stock for a shop that already has goods | Opening-Stock-Entry.md (bulk, Excel import) | nothing; the receipt is the only path and has no `opening` kind | **gap** (not a listed non-goal) | Medium |
| S11 | Stock updation / physical count | Stock-Updation.md | D27 "A stock count enters the counted number"; C6 | **defect** (the counted number is not stored) | Medium |
| S12 | Short/excess record + enquiry report | Short-Excess.md, Short-Excess-Enquiry.md | `adjustment` + `repacks.loss_qty_base` (D30) | **gap** (mechanics covered, no record/report) | Low |
| S13 | An offline bill whose batch/stock is missing is listed for the operator to fix | 9a24ee6967-Offline-Billing-RPOS7.pdf.md ("Inaccurate", report 3262, `Offline Edit`) | D27/D28 (negative stock, "the owner fixes the count") | **gap** (nothing lists the affected bills or the negative batches) | Medium |
| S14 | Negative stock allowed, never blocks a sale | 9a24ee6967-Offline-Billing-RPOS7.pdf.md ("Allow Negative") | D27, D28; C10 | match | — |
| S15 | Register-side on-hand estimate across two registers | — (GoFrugal syncs bills, not stock) | D27 bullet "The register shows its own estimate"; `query-catalogue.md` §3 | **risk** | Medium |
| S16 | `GET /v1/stock?since=` cursor pull | — | D27; `query-catalogue.md` §3; C10 | match | — |
| S17 | Void puts the goods back (batched and unbatched lines) | — (GoFrugal reverses a bill, not a void document) | `trg_bill_voids_restock`; C1, C1b | **defect** (duplicate movement ⇒ the void is refused) | High |
| S18 | Archival deletes old sale movements while `batch_stock` keeps the balance | — | D22, D27 cost bullet; C9 | **risk** (documented; the void-after-archive restock is unrecorded) | Low |
| S19 | Retire a batch (stop selling without deleting) | — (GoFrugal has no retire; expiry/deactivate) | D27 `retired_at`, `trg_item_batches_no_delete`; C7 | match | — |
| S20 | An item whose batches are all expired/retired can still be billed | community-batch-and-expiry.md (expired stock cannot be sold) | D27/D28; C7 | **defect** (no live batch = no price at all: the sale is blocked) | High |
| S21 | A batch's price set is found by version (D26 stale check) with `batch_id` on the line | — | D26; 0008 `trg_item_prices_version_scope` | match (verified: `(item, version)` is unique across batches) | — |
| S22 | Change selling price per batch | community-change-selling… Q3 | D27; `PUT /v1/admin/items/:id/prices` + `PATCH /v1/admin/batches/:id` | **differs-by-design** (D27) — the split is the defect in S3 | High |
| S23 | Batches and books reach a terminal at all | — | `api-routes.md` §3.3 vs `catalog_changes` CHECK (`d1-schema.sql:262`) | **defect** | **Critical** |
| S24 | The register applies the delta with the same DDL (same triggers/unique indexes) | — | DESIGN-BRIEF ("same DDL"); D28 swap rule | **defect** | High |
| S25 | One stock receipt can write one batch twice / a receipt with no lines | Making-purchase-entry.md (one line per item/batch) | D28 "Every line is a new batch"; C12 | **risk** | Medium |
| S26 | Invalid expiry date | community-batch-and-expiry.md (date format per item) | `item_batches` CHECK (GLOB only); C11 | **defect** | Low |

---

## 2. Findings worth action

### F1 · S23 · **Critical** · Batches and books are in the journal's `CHECK` but in neither the delta enumeration nor any snapshot page

**Scenario.** A register gets its catalog by snapshot on first install, after `410 stale_cursor`, or after
`410 snapshot_invalidated`. `api-routes.md` §3.3 says the snapshot pages cover "items, prices, identifiers,
users, reference (units/tax rates) and store (store, settings, numbering policies, config)" — no `batches`,
no `books`. The delta's `entity` enumeration (same section) is "item, item_price, item_identifier, unit,
tax_rate, user, store, store_settings, numbering_policy, terminal_config" — also no `item_batch`, no `book`,
even though `catalog_changes.entity`'s CHECK (`d1-schema.sql:262`) accepts both and D27/D28 describe them as
catalog documents.

A register with no batches has no price for a batched item (S20, C7 below) and, with no books, the picker's
`JOIN books k ON k.id = b.book_id AND k.active = 1` matches nothing. Every batched item is unbillable until
the first delta that happens to mention it — and a batch that never changes again is never sent.

**Repro.** `stock_repros.py` C7 (same code path as the missing rows):

```
retire b_apr; b_old expired on 2026-01-01      : OK
picker rows for 2026-11-14                      : []
register price lookup for the item, batch NULL  : []
```

and `catalog_apply.py` §2: `books missing on a fresh register too : (0 rows)`.

**Fix.** Add `batches` and `books` to the snapshot page enum and `item_batch`, `book` to the delta `entity`
list in §3.3; both store-scoped, ≤8 MB per page. Order the register's apply as books → batches → prices.

**Cost.** Two page names, two SELECTs, one ordering sentence. No DDL change, no rebuild.

---

### F2 · S24 · **High** · A book reorder can never be applied on a register: no ordering of the two documents satisfies `uq_books_priority`

**Scenario.** D28 requires reordering books through a free priority (`park, move, set` — three updates in one
`db.batch()`, because `uq_books_priority ON books(store_id, priority) WHERE active = 1` refuses a one-statement
swap). The register runs the same DDL and receives *documents at read state*, ordered by `seq`. Going from
Main=1/Godown=2 to Godown=1/Main=2, it receives Main(priority 2) then Godown(priority 1). Neither order can
succeed: applying Main→2 collides with Godown=2; applying Godown→1 collides with Main=1.

**Repro.** `catalog_apply.py` §1:

```
apply in seq order: Main first              : UNIQUE constraint failed: books.store_id, books.priority
apply in seq order: Godown second           : UNIQUE constraint failed: books.store_id, books.priority
retry the same page                         : UNIQUE constraint failed: ... - identical failure, no progress
neither order works: Godown first also fails: ...UNIQUE constraint failed...
```

The pull is one local transaction and the checkpoint advances only after it commits (DESIGN-BRIEF: "advance
checkpoint only after the transaction commits"), so the register re-fetches the same page forever: its catalog
freezes, and when `devices.lease_expires_at` passes (D4) it stops billing.

**Fix.** Send books as **one document per store**: `entity = 'book'`, `entity_id = store_id`, document = the
store's full book list; the register replaces that store's book rows inside the pull transaction (books are
never deleted, `trg_books_no_delete`, so a full replace of one store's rows is safe and idempotent). Same
shape suits store settings.

**Cost.** One document shape, one apply rule, one test. No DDL change.

---

### F3 · S24 · **High** · A price document that arrives before its batch aborts the pull

**Scenario.** `item_prices.batch_id` is guarded by `trg_item_prices_batch_scope`, which needs the batch row to
exist. A page that carries the price row before the batch row (a stock receipt's `db.batch()` is "receipt,
the new batches and their prices" — that order is right; but a `prices` snapshot page, a compaction, or any
Worker that writes prices first) is unappliable, and identically unappliable on every retry.

**Repro.** `catalog_apply.py` §2:

```
price before batch                          : batch_price_scope
batch, then price                           : OK/OK
```

**Fix.** State the apply order in §3.3 (books → batches → prices → identifiers) and assert it in the register's
pull test. If the snapshot uses shadow tables without triggers, say so explicitly, because the swap then has to
insert parents before children for the FKs anyway.

**Cost.** One paragraph + one test.

---

### F4 · S17 · **High** · A bill line can carry two `sale` movements (stock taken twice) and the bill then cannot be voided

**Scenario.** `review-schema.md` D39 proposed `CREATE UNIQUE INDEX uq_inv_movement_line ON
inventory_movements(bill_id, bill_line_no) WHERE bill_id IS NOT NULL` and 0008 did **not** add it — only the
non-unique `idx_inventory_movements_line`. `query-catalogue.md` §5 and `api-routes.md` §3.4 show
`INSERT INTO inventory_movements ...` with no `ON CONFLICT` clause and never state the movement id. So the DDL
accepts a second `sale` movement for the same line (a resend that derives a different id), which takes the
stock out twice; and because `trg_bill_voids_restock` derives `NEW.id || ':' || m.bill_line_no` per sale
movement, the resulting void tries to insert `v1:1` twice and is refused. `d1-schema.md:107` states the
opposite invariant ("`bill_lines` one per line").

**Repro.** `stock_repros.py` C1:

```
movement b1:1 (the sale)                        : OK
movement b1:1 again, different id (a re-push)   : OK
  -> one sale of 1, stock taken twice; on_hand  : 8 (was 10, should be 9)
void the bill                                   : UNIQUE constraint failed: inventory_movements.id
```

C1b shows the correct path (`OK -> on_hand 10` after the void), so the restock logic itself is sound.

**Fix.** In 0009: `CREATE UNIQUE INDEX uq_inv_movement_line ON inventory_movements(bill_id, bill_line_no)
WHERE bill_id IS NOT NULL;`. In §3.4 and `query-catalogue.md` §5: state the movement id
(`<bill_id>:<line_no>`, the void's is `<void_id>:<line_no>`) and the insert as `ON CONFLICT DO NOTHING`, so a
replay is free rather than fatal. The index also enforces D28's "one line stays one batch" (no automatic split).

**Cost.** One index; one Worker insert gains a conflict clause. Pre-launch, so no rebuild cost.

---

### F5 · S3, S22 · **High** · A batch can be priced above its MRP, and MRP is stored twice with no rule between the copies

**Scenario.** D27's whole premise is the printed MRP ceiling. The DDL stores MRP in **two** places —
`item_batches.mrp_minor` (copied to the bill line) and `item_prices.mrp_minor` — with no constraint, trigger or
Worker flag relating either one to `item_prices.price_minor`. Change selling is also split across two routes
with two version counters (`PUT .../prices` and `PATCH .../batches/:id`), so a `409 edit_conflict` on the
second leaves the price raised and the MRP not — the exact illegal state.

**Repro.** `stock_repros.py` C5:

```
close APR's open price, then revise to 12,000 with MRP 10,000: OK / OK
  -> (batch.mrp, price.price_minor, price.mrp_minor): [(10000, 12000, 10000)]
bill b5 charges 12,000 against a printed MRP of 10,000: OK / OK
  -> stored line (price_minor, mrp_minor)      : [(12000, 10000)]
  -> a price row whose own mrp_minor disagrees with its batch: OK / OK -> accepted
```

**Fix.** (a) One owner for MRP: drop `item_prices.mrp_minor` from the batch pricing path (keep the batch's), or
drop the batch's and keep the price row's — not both. (b) One route for "change selling on a batch":
`POST /v1/admin/batches/:id/change-selling {mrp_minor, expiry_date?, prices[], base_version, price_base_version}`
applied in one `db.batch()`, so a partial failure is impossible. (c) A line-level verdict: `bill_lines.price_above_mrp`
(one column) set by the Worker when the charged rate exceeds the batch MRP for the pack size, plus a register
hard-warning; it belongs in `flagged{}` alongside `stale_price`.

**Cost.** One route, one nullable flag column, one catalog field removed. No rebuild (pre-launch).

---

### F6 · S20 · **High** · A batched item with no live batch cannot be priced or billed, which contradicts "stock never blocks a sale"

**Scenario.** The picker excludes retired and expired batches and batches in inactive books; if nothing is
left, it returns zero rows, and there is no item-level price set to fall back to (`item_prices.batch_id IS
NULL` is never written for a batched item), so the line has no price to freeze. Reachable three ways: every
batch expired (grocery with optional expiry), every batch retired, or the store's last active book deactivated
(`books.active = 0` drops every batch out of `JOIN books k ... AND k.active = 1` — nothing forbids
deactivating the last book). A stock receipt line with an empty `prices[]` also makes the newest batch win with
`price_minor NULL` while the older, priced batches sit in the prompt.

**Repro.** `stock_repros.py` C7 and C13:

```
retire b_apr; b_old expired on 2026-01-01      : OK
picker rows for 2026-11-14                      : []
register price lookup for the item, batch NULL  : []
...
live batches while Main is active      : [('b1', 10, 10000)]
deactivate the only book                : OK
live batches now                        : [] -> the item cannot be priced or billed
```

**Fix.** Define the fallback in §3.3/D28: no live batch → bill from the item-level price set with `batch_id`
NULL on the line and the movement (stock untracked for that line), and the Worker records
`flagged.batch_unavailable` (+ a nullable line column) so the owner sees it. Two Worker checks: a receipt line
must carry ≥1 price, and a store's last active book cannot be deactivated.

**Cost.** One query branch in the picker/price lookup, one nullable flag column, two Worker checks.

---

### F7 · S4 · **High** · An expired batch can be billed and nothing records it

**Scenario.** Expiry is enforced only in the register's picker (`expiry_date >= business date`). A bill line
carries `batch_id`, `batch_no`, `mrp_minor` but **not** the expiry, so the server cannot tell that a sale
happened on expired stock — and it does not try: nothing in `flagged{}` or `bill_lines` covers it. Three real
paths: a register clock behind the server (the register uses its own date), a batch whose expiry is corrected
in admin after a cart was priced (the line froze the batch and MRP but not the expiry), and a hand-entered or
stale batch id. For a grocery the consequence is a legal one (selling past `best before`) plus a
shrinkage report that cannot be built.

**Repro.** `stock_repros.py` C4:

```
b_old expiry 2026-01-01, bill dated 2026-11-14 : OK
movement out of b_old                           : OK -> on_hand 3
  -> the register's picker for that day never offers b_old: [('b_apr', 10000, 10, 10000)]
```

**Fix.** Copy `expiry_date` onto `bill_lines` (like `mrp_minor`) at sale time and have the Worker set
`expired_at_sale` when `expiry_date < bills.business_date`. Both are diagnostics, money unchanged (D2).

**Cost.** Two nullable columns on `bill_lines`, one Worker comparison; no rebuild.

---

### F8 · S11 · **Medium** · A physical count is never stored, so the count's own information is thrown away

**Scenario.** `POST /v1/admin/batches/:id/adjustments` accepts a counted `qty_base` and "the Worker writes the
difference". The difference is computed against whatever `batch_stock` says at save time, and only the delta is
written (`reason = 'adjustment'`, free-text `note`). So (a) the number the staff counted exists nowhere and can
never be audited or re-derived; (b) a sale that lands between the count sheet and the save silently becomes
part of the "difference", so the count stops finding the shrink it was meant to find; (c) a count cannot be told
apart from a damage write-off. GoFrugal stores the physical stock and the "Stock Diff" and reports both
(Stock-Updation.md, Short-Excess.md, Short-Excess-Enquiry.md).

**Repro.** `stock_repros.py` C6:

```
book says 10; count sheet printed 10; staff count the shelf: 8 (2 damaged)
meanwhile a register pushes a sale of 2       : OK -> on_hand 8
admin enters counted = 8; the Worker reads current 8 and writes delta = 8 - 8 = 0
  -> on_hand stays at 8, the 2 damaged packs are never written off
  -> the counted number 8 exists in no table: [] (no column)
```

**Fix.** `inventory_movements.counted_qty_base INTEGER` (nullable, `CHECK (>= 0)`), written with the delta;
`reason` gains a `count` value or the note becomes an enumerated `short|excess|damage|count` + free text. Worker
rule: read `on_hand` and write the delta in the same `db.batch()` (D1 is single-writer, so read-and-write is
atomic) and store both numbers.

**Cost.** One nullable column, one route field, one Worker rule. No rebuild.

---

### F9 · S6 · **High risk** · Newest-first means the oldest batch is offered last, at the end of its life

**Scenario.** D27 chose "latest batch first" because the owner asked for it. But `pick` orders by
`on_hand > 0 DESC, priority, received_at DESC`, and the prompt only lists batches at a *different MRP or
price*. So two batches at the same MRP and price (the normal case for a repeat delivery of the same SKU at the
same price) means the older batch is neither picked nor offered: it is invisible until the newest one hits
exactly zero. With steady deliveries the newer batch always has stock, so the older batch drains last — i.e. as
it expires. GoFrugal makes this a per-item setting: `BatchFiFo` or `BatchShortExp`
(community-batch-sorting-6-8-2020.md).

**Repro.** `stock_repros.py` C8:

```
picker: the cashier is offered                : [('m2', 10000, 10, 10000)]
 sell all 10 of M2                             : OK
 picker now                                    : [('m1', 10000, 10, 10000)]
  -> M1 surfaces only when M2 reaches zero, i.e. at the end of its life
```

**Fix.** `store_settings.batch_order` ∈ (`latest`, `expiry`, `fifo`), default `latest` (the owner's ask), and
substitute the ordering key in `pick`: `ORDER BY on_hand > 0 DESC, <order key>, priority`. Also offer the old
batch in the prompt when its expiry is nearer than the picked batch's — one extra `OR` in the prompt clause.

**Cost.** One nullable column, one ORDER BY branch; the sort is already over a handful of rows (§3's note).

---

### F10 · S15 · **Medium risk** · "server − unsynced sales" is under-specified and wrong in two of its three implementations

**Scenario.** D27 and `query-catalogue.md` §3 define the register's estimate as `server value − its unsynced
sales`. The order of the two calls that make a sale "synced" is not specified (the sync contract names a
*catalog* pull, not a stock pull), and the formula has no term for voids. Simulating the documented rule:

**Repro.** `estimate_sim.py`:

```
A  push acked -> synced cleared, no pull yet     local  10   truth   7   error  +3   <-- phantom packs
B  pull applied, bill not yet marked synced      local   4   truth   7   error  -3   <-- subtracted twice
D  void raised offline (goods back on shelf)     local   8   truth  10   error  -2   <-- no void term
E  all 7 pushed, every stock pull failed         local  10   truth   3   error  +7
```

`B` is the double count the brief asks about; `E` is the worst real case (pushes succeed, `GET /v1/stock` is
rate-limited or failing) — the register shows the old server number and the "quantity above on-hand" warning
stops firing. Neither loses money on the server; both mislead the cashier and the prompt.

**Fix.** Write the rule as: `local = server − Σ(unacknowledged sales) + Σ(unacknowledged voids)`, where
"unacknowledged" = *pushed but not yet covered by a stock pull*, cleared inside the same local transaction
that applies the pull (case C in the script: zero error at every step). Also state that the register's local
`trg_movements_batch_stock` must not write the sync cursor: keep the server's `seq` in a separate column
(`server_seq`), or a local bill's own counter will collide with the server's cursor namespace.

**Cost.** One paragraph in D27/§3, one local column. No server change.

---

### F11 · S18 · **Low risk** · Archival deletes the sale movements; a void for an archived bill loses the restock with no record

**Scenario.** D22 deletes a store-month's bills, so the cascade takes `bill_lines` and their `inventory_movements`
(measured, 55 ms). `trg_movements_batch_stock` fires on INSERT only, so `batch_stock` keeps the balance — correct.
But a void that arrives later is parked `bill_archived` and the physical goods are back on the shelf with nothing
recorded anywhere. `d1-schema.md:401` still asserts "`inventory_movements` is a ledger, not a balance: on-hand is
always a projection (Q13), never a stored column", which `batch_stock` contradicts; anyone implementing from that
sentence rebuilds on-hand from a ledger that archival has already truncated (D27 admits this in its cost bullet).

**Repro.** `stock_repros.py` C9:

```
archive that month ... -> movements left: 0 , on_hand: 7
a void arrives for the archived bill            : void_store_mismatch
```

**Fix.** On `bill_archived`, write an `audit_events` row (kind `void_archived_restock_owed`) carrying the batch
and quantity the void would have restocked, and surface it on the stock screen. Correct `d1-schema.md` §Q13 and
the "Everything else is an input" paragraph to say that `batch_stock` is the one stored projection, seeded by
migration and not rebuildable after archival.

**Cost.** One audit row per parked void, one doc paragraph.

---

### F12 · S8 · **Medium** · The transfer shape D28 describes cannot be built on this DDL

**Scenario.** D28: "an `out` movement from the batch in book A and an `in` movement to a matching batch in book
B". No matching batch can exist — `uq_item_batches_no (store_id, item_id, batch_no)` is store-wide, so the same
batch number cannot be created in a second book — and a batch cannot change book (`trg_item_batches_version`
refuses a `book_id` change, and 0008's comment says so). It is deferred (transfers are a v1 non-goal), but D28
presents the shape as settled.

**Repro.** `stock_repros.py` C14:

```
 a second batch 'B1' in the Godown        : UNIQUE constraint failed: item_batches.store_id, item_batches.item_id, item_batches.batch_no
 edit b1's book_id to the Godown          : batch_version_not_bumped
```

**Fix.** Before the transfer ships, widen `uq_item_batches_no` to `(store_id, item_id, batch_no, book_id)` and let
a transfer create the shadow batch in the destination book with the same batch number, MRP and expiry, then
`out` from A and `in` to B (two movements, one `db.batch()`, one client id for idempotency). Or keep the current
unique and state that a transfer renumbers the batch. Either way, fix D28's sentence so nobody builds the
impossible version.

**Cost.** One unique-index replacement (pre-launch, no data); a transfer table when the feature lands.

---

### F13 · S25 · **Medium risk** · A single receipt can write the same batch twice, or no lines at all

**Scenario.** `stock_receipts` has no line table: the entry's lines *are* its `receipt` movements, and nothing
says a receipt writes each batch once. A duplicated line is accepted and doubles the stock. An empty receipt is
also accepted (D30 notes the same for repacks and says the Worker refuses it; no such statement for receipts).

**Repro.** `stock_repros.py` C12:

```
receipt movement, line 1                  : OK
receipt movement, line 2, same batch      : OK
on_hand (one entry of 10, doubled)        : 20
a receipt with no lines at all            : OK (accepted, no trigger)
```

**Fix.** State "one `receipt` movement per batch, ever" and enforce it in the Worker (the batch is new, so this
is a straight count in the same `db.batch()`); refuse a receipt with zero lines. If you prefer DDL:
`CREATE UNIQUE INDEX uq_receipt_batch ON inventory_movements(receipt_id, batch_id) WHERE receipt_id IS NOT NULL`.

**Cost.** One Worker check (or one partial index) + one sentence.

---

### F14 · S26 · **Low** · An impossible expiry date is accepted and never expires

**Scenario.** `item_batches.expiry_date` is validated by GLOB only. `2026-13-45` is accepted, and because the
picker compares strings (`expiry_date >= business date`) it is "in the future" for ever; SQLite's own
`date('2026-13-45')` is NULL, so no later report can make sense of it.

**Repro.** `stock_repros.py` C11:

```
2026-13-45: OK
sqlite date() of it                       : None
the picker treats 2026-13-45 as future    : [('x',)]
```

**Fix.** `CHECK (expiry_date IS NULL OR date(expiry_date) IS expiry_date)` — catches `2026-13-45` (NULL) and
`2026-02-31` (`2026-03-03` ≠ input). A CHECK on an existing table needs a rebuild; pre-launch that is free, so
do it in 0009 rather than a trigger.

**Cost.** One CHECK expression in 0009's table definition.

---

## 3. Gaps (no action requested in this slice, but they are not all non-goals)

- **S10 Opening stock.** GoFrugal has a dedicated Opening-Stock-Entry with bulk load and Excel import
  (Opening-Stock-Entry.md). Traasu's only path is `POST /v1/admin/stock-receipts`, shaped as a purchase with a
  supplier reference and no `kind`, one screen entry at a time. At go-live a five-store chain enters thousands
  of SKUs; that is a day of typing, and until it is done every batched item is unbillable (F6). Not on the
  DESIGN-BRIEF non-goal list. Cheapest fix: `stock_receipts.kind ('purchase'|'opening')` (one nullable column)
  plus a CSV/JSON import that posts through the same receipt route in pages of ~200 lines (the bill route's
  100/1 000 caps exist for the same reason; a receipt route has no cap at all today).
- **S13 Seeing negative stock / "inaccurate" sales.** GoFrugal lists exactly the bills whose batch or stock
  failed (Pending Offline Sales Bill, report 3262, Offline Edit) and lets the operator fix them. Traasu's
  `batch_stock` may be negative by design and the owner is told to "fix the count with an adjustment" — but
  there is no route that lists negative batches, so the only way to find them is item by item through
  `GET /v1/admin/items/:id/batches`. `GET /v1/admin/stock?negative=1` over `batch_stock` joined to
  `item_batches`/`items` costs a query and no DDL; add a per-book breakdown at the same time (the books
  screen shows priorities, never quantities).
- **S12 Short/excess.** The mechanics exist (`adjustment`, `repacks.loss_qty_base` in D30); what is missing is
  the record and the enquiry report (Short-Excess-Enquiry.md). Fold into F8's `counted_qty_base` rather than a
  new table.
- **S7 per-godown bill series.** GoFrugal lets each godown carry its own bill-serial prefix. D28 refuses this
  on purpose (one GSTIN, one series per store) and it is the right call; noted so nobody "restores parity".

---

## 4. What is sound (checked, no finding)

- **The picker itself** (`query-catalogue.md` §3) matches the D28 table case for case: latest sets MRP/price,
  same-MRP batches drain book 1 first, no stock anywhere falls back to book 1 which goes negative, and the
  prompt lists only other MRP/price batches with stock. `check-invariants.py` covers all of it, including the
  April/May soap case.
- **`(item_id, version)` is unique across scopes *and* batches** (`trg_item_prices_version_scope`):
  re-using a version for a second batch is refused (`price_version_scope`), so D26's key lookup
  (`item_id`, scope, `version`) needs no `batch_id` and cannot alias a batch's tier set. Verified.
- **`stock_seq` / `GET /v1/stock`**: one row per batch carrying the latest `seq`, monotonic, store-scoped,
  seeks `idx_batch_stock_store_seq`. Two registers selling the same batch offline both post and `on_hand`
  goes negative without a refusal (C10: `-2`, accepted) — D27/D28's "never blocks a sale" holds.
- **Void restock** for a batched line and for a line with no batch (movement `batch_id NULL` touches no
  balance): correct once the movement is unique (C1b).
- **Retire, versioning, no delete** for batches and books: triggers refuse a silent edit, a `book_id` change
  and a delete, exactly as D27/D28 claim.
- **`trg_item_batches_book_store`** and **`trg_movements_receipt_book`**: a batch cannot belong to another
  store's book, and a receipt movement cannot land in a batch of another book; also stricter than described
  (any movement carrying a `receipt_id` whose reason is not `receipt` is refused).
- **Archival does not change stock**: `trg_movements_batch_stock` is INSERT-only, so deleting sale movements
  leaves `batch_stock` alone (verified, and `check-invariants.py` has the case).

---

## 5. Not checked

- Batch-wise cost price, margin and purchase-price fields: out of scope (purchases and cost are non-goals), so
  GoFrugal's "new selling price must exceed cost price" rule (community-change-selling Q4) is intentionally
  absent — no verdict recorded.
- The register's UI mechanics (the batch popup's layout, F-key behaviour, "Item Detail" rack/box fields) — pure
  desktop chrome.
- Repack and parent/child effects on `batch_stock` (D29/D30): other slices own them; I only used the triggers
  as they affect receipts/voids/archival.
- Capacity of `batch_stock`/`stock_seq` at 10 GB scale: row count is one per batch, which grows with deliveries.
  Not measured; the per-line cost is only asserted in D27's cost bullet.
- Whether GoFrugal tops up an existing batch on a second inward at the same batch number. Nothing in the local
  pages says; 0008 forbids it (one entry = one new batch per line), which I recorded as Traasu being stricter.
- No extra pages fetched from retaileasy/gofrugal: the five local pages covered every GoFrugal behaviour cited.
