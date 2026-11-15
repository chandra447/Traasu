# Billing parity and robustness audit — Traasu vs GoFrugal RetailEasy (RPOS7)

Scope: scanning, price resolution, MRP, discounts/overrides/grants, GST, numbering, offline billing, replays,
voids/returns, tenders, offers/coupons/price levels. Design only (no implementation exists).

Evidence: every defect below is reproduced against the real DDL
(`docs/d1-schema.sql` + `docs/migrations/0002`–`0008`, all applied, `PRAGMA foreign_keys=ON`) by

```
docs/parity/repro/billing-repro.py      (26 bills, 16 cases, ~1 s)
```

Case numbers in the excerpts are that script's (`=== n.`). Nothing in the repo was modified.
GoFrugal pages are cited by file name under `reference/gofrugal/md/`.

## 1. Verdict table

| # | Use case | GoFrugal (page) | Traasu (D#, file) | Verdict | Severity |
|---|---|---|---|---|---|
| 1 | Scan alias/barcode → line | Barcode-Profile-Configuration.md; Item-Master-Configuration.md | `item_identifiers`, query-catalogue §2 | match | — |
| 2 | Weighing-scale barcode (embedded weight/qty) | Barcode-Profile-Configuration.md (Wt prefix + weight + precision); Item-Master-Configuration.md | `item_identifiers.qty_base`, `items.scale_qty` — no profile, no parse rule | gap | High |
| 3 | Store override vs franchise default price | Price-Level.md (customer-type levels); community-change-selling (batch price edit) | D9, D26; query-catalogue §3 `ORDER BY store_id IS NULL` | match | — |
| 4 | One `price_version` must name one tier set | n/a (GoFrugal stores the rate used per line) | D26; query-catalogue §3, §9 item 12 ("precedence is per tier") | defect | High |
| 5 | `pricing_mode` (fixed_only/tiered/linear) and fractional qty | Item Master selling unit; Sales-Batch-LOV | DESIGN-BRIEF only; no rule in DECISIONS/api-routes/query-catalogue | defect | Medium |
| 6 | Line's `price_store_id` scoped to the bill's store | location-scoped prices | D9/F7; 0003 `price_store_id` | defect | High |
| 7 | MRP is a hard ceiling | D27's own premise; community-batch-and-expiry; community-change-selling | D27; `items.mrp_minor`, `item_batches.mrp_minor`, `item_prices.mrp_minor`, `bill_lines.mrp_minor` | defect | High |
| 8 | Price revision expiry leaves no hole | Datewise-Pricing.md (auto-revert) | `item_prices.valid_to`, 0005 `trg_item_prices_close_only` | defect | Medium |
| 9 | Batched-item price + batch picker | Sales-Batch-LOV; community-batch-sorting | D27, D28; query-catalogue §3 picker | match (detail: ParityStock) | — |
| 10 | Discount recorded honestly (`price_source`, `override_by`) | no ceiling/approval page in the local set (Security-Manager.md is user privileges only) | D3, D25 D33/D32 (TRIAGE §F: open); `bill_lines` CHECK | defect | High |
| 11 | Override ceiling and approval grants offline | offline billing disables session/credit features (Offline-Billing PDF, "Configuration limitations") | D3, D8; `/v1/approvals`, `approval_grants` | differs-by-design — holds | — |
| 12 | Bill-level discount / free item | Offer-Management.md (Disc % on BILL, Free, Special price) | no rule; only per-line `discount_minor` | gap | Medium |
| 13 | Coupons | Discount-Coupon1.md (master/serial/issue/redemption, cross-outlet) | nothing | gap (v1 non-goal-adjacent: needs customers) | Low |
| 14 | Price levels per customer type | Price-Level.md | nothing | gap (v1 non-goal: customers) | Low |
| 15 | MarkUp/MarkDown selling-price derivation | MarkUp-MarkDown.md | nothing | gap (v1 non-goal: purchases) | Low |
| 16 | GST inclusive is the retail rule | Tax-based-on-Item-Master.md ("Tax on MRP [Exclusive] (Not for sales)") | D23; api-routes §9.5 | match | — |
| 17 | `price_includes_tax = 0` on an item | n/a (exclusive options are "Not for sales") | DESIGN-BRIEF item fields; 0002 column; PATCH items | defect | High |
| 18 | CGST/SGST split, IGST exclusivity, cess | GST-Reports.md | D23; 0008 `trg_lines_tax_split`, 0003 `trg_lines_tax_exclusive` | match | — |
| 19 | Stored tax agrees with stored `tax_rate_bp` | GST-Reports.md (rate-wise totals) | `bill_lines.tax_rate_bp` vs amounts — no rule | defect | High |
| 20 | Bill-total rounding | — (round-off not documented in the local pages) | D23; `store_settings.rounding_mode`; TRIAGE §F D9 (partial) | risk | Medium |
| 21 | Per-GST%-rate statutory report | GST-Reports.md | no route; api-routes §3.6 is per-item only | gap | Medium |
| 22 | Invoice number / prefix at the counter | Bill-Prefix-Master.md; Tender-Management.md; Offline-Billing PDF ("configure counter wise bill series") | D25 Q11; `numbering_policy`, api-routes §9.6 | defect | Critical |
| 23 | Offline billing: bill that cannot be posted | Offline-Billing PDF (Accurate/Inaccurate, Pending Offline Sales Bill 3262) | D2, D3, D12, D26 (accept + flag, never park) | differs-by-design — holds | — |
| 24 | Offline for days / offline lease | no equivalent cap (PDF lists no lease) | D4 (7-day lease) | risk | Medium |
| 25 | Clock skew moves a sale's business date | PDF "Risks" (client/server date, "Take server system date as billing date" must be off) | D11 revised; `business_day_start_minutes` on the bill | risk | Medium |
| 26 | Two registers sell the last pack | PDF (negative stock mode) | D27/D28; `batch_stock` may go negative | match | — |
| 27 | Replays and lost responses | PDF "Risks": duplicated bill number and stock collapse | D12, D13; `ON CONFLICT DO NOTHING`; `uq_bills_store_terminal_seq` | defect (stock ledger) | Critical |
| 28 | Partial push (one bad bill in 100) | — | D12 one `db.batch()` per bill | match | — |
| 29 | Voids vs sales cancel/return | GST-Reports.md (sales **and SR** amounts); PDF risk list (cancel/edit/return) | D15, D25; `bill_voids`, triggers | differs-by-design — holds | — |
| 30 | Void after the month is closed | — | no period lock anywhere | risk | Medium |
| 31 | Returns / refunds | GST-Reports.md (SR register); no dedicated page in the local set | DESIGN-BRIEF non-goal; D25 D45 | gap (v1 non-goal) | — |
| 32 | Tenders and the bill model | Tender-Management.md; FAQs-Tender-Management.md | non-goal; `bills.total_minor` only | gap (v1 non-goal) — model does not block | — |
| 33 | `local_seq` after a reinstall | counter-wise bill series per counter | `uq_bills_store_terminal_seq` | risk | Medium |
| 34 | `display_no` is the number on the receipt | — | `bills.display_no` not in `trg_bills_money_frozen` | risk | Low |

## 2. Findings worth action

### A. The legal invoice number exists only after sync — offline receipts have none (Critical)

**Scenario.** `numbering_policy` has two scopes, and its own comment says store scope is "the server-side legal
invoice series" while register scope is "the terminal-local display series" (`d1-schema.sql:96`). api-routes §9.6:
"Invoice numbers are server-assigned per store from `numbering_policy`; the terminal treats `invoice_no` as opaque
and keeps `display_no` locally." So a bill printed in the shop carries `display_no`, and `invoice_no` is written
when the Worker receives the push (Q11, `d1-schema.md:602`). A register that is offline for a day issues a day of
receipts with no statutory serial; a B2B customer cannot take that slip as a tax invoice, and the shop's invoice
series is not the series the customer saw. GoFrugal's offline billing refuses to start until counter-wise bill
series are configured (Offline-Billing PDF, Configuration Settings, note after step 4) precisely so the counter
can issue a numbered bill while disconnected.

Two further consequences of server-side allocation, both reproduced:

1. **A concurrent duplicate burns a number.** Two Worker invocations pass the pre-check for the same bill; the
   header insert hits `ON CONFLICT DO NOTHING` but the counter `UPDATE` in the same batch still commits.
   Repro case 9: invoices 000001–000003, then a burned 000004, next real bill 000005.
2. **A yearly reset is impossible with a static prefix.** Reset the counter and insert the first bill of the new
   year: `UNIQUE constraint failed: bills.store_id, bills.invoice_no`, because the example prefix is the literal
   string `KUK/2026/`. `reset_cadence`/`last_reset_on` are referenced nowhere outside the DDL (grep), and Q11 has
   no reset clause.

```
=== 9. Invoice numbering: a concurrent duplicate burns a number; a yearly reset cannot work
    three bills: ['KUK/2026/000001', 'KUK/2026/000002', 'KUK/2026/000003']
    the losing concurrent push incremented the counter, then its header inserted nothing: counter 4, highest number issued 000003
    next real invoice: ['KUK/2026/000001', 'KUK/2026/000002', 'KUK/2026/000003', 'KUK/2026/000005']
    first invoice of the reset year, same static prefix: UNIQUE constraint failed: bills.store_id, bills.invoice_no
    bills in the table with no legal invoice number (printed offline): (11,)
```

**Fix.** Let the register allocate `invoice_no` at sale time from the legacy-'register' policy (prefix, padding,
reset), offline and gap-free; the server validates uniqueness (`uq_bills_invoice_no` already exists, per store) and
keeps `received_at`. `trg_bills_invoice_no_once` only blocks *changing* an assigned number, so the terminal can
write it at INSERT with no DDL change. Require the register prefixes within a store to be distinct — otherwise the
server's unique index rejects the second register's first bill — and let the store scope cover admin-originated
documents and the fallback when a register has never been provisioned. Make the prefix a template (`KUK/{FY}/`)
expanded by the allocator, and state the reset boundary (Indian FY, 1 April) in `numbering_policy`.

**Cost.** One rule in D25/api-routes, a Go allocator and a reset function, one template column or convention. No
table rebuild. Half a day; it removes a statutory failure mode that only appears in the shop, never in a test.

### B. The stock ledger accepts duplicate movements, and a duplicated line can never be voided (Critical)

**Scenario.** D12 promises that a lost response cannot duplicate lines; `bill_lines` and `sales_daily` are safe
(`ON CONFLICT DO NOTHING`, trigger-maintained rollup). `inventory_movements` is not: its `id` is a fresh UUID and
there is **no unique constraint on `(bill_id, bill_line_no)`**. A retry with a new movement id inserts a second
`sale` row, `trg_movements_batch_stock` decrements again, and `batch_stock` is wrong. Worse, the void restock
trigger derives its movement id as `NEW.id || ':' || m.bill_line_no` (0008), so two sale rows for one line produce
the same id twice and the **void is refused outright** — the sale can never be corrected.

```
=== 7. Replay: lines and the rollup are idempotent, the stock ledger is not
    on hand after one sale: (9,)
    replay of the same bill with a fresh movement id (a retry after a lost response): OK
    on hand after the replay (one pack left the shop): (8,)
    sale movements for the one line: (2,)
=== 8. Void: ...
    void the bill whose one line carries two sale movements (the replayed b_rep):
      UNIQUE constraint failed: inventory_movements.id
```

**Fix.** Exactly the unlanded half of schema review D28 (TRIAGE §F lists it open):

```sql
CREATE UNIQUE INDEX IF NOT EXISTS uq_movements_line
  ON inventory_movements(bill_id, bill_line_no) WHERE bill_id IS NOT NULL;
```

plus a deterministic push id (`bill_id || ':' || line_no`) and, in `trg_bill_voids_restock`, derive the restock id
from the movement (`NEW.id || ':' || m.id`) so a line with several movements would still reverse fully rather than
abort. Pre-code, so the index fails on nothing.

**Cost.** One index, one trigger line, one Worker convention, one harness case. An hour.

### C. MRP is a legal ceiling in D27's premise and is enforced nowhere (High)

**Scenario.** D27 opens with "Indian law forbids charging above the printed MRP". The design then stores MRP in
four places — `items.mrp_minor`, `item_batches.mrp_minor`, `item_prices.mrp_minor`, `bill_lines.mrp_minor` — with
no precedence rule, no comparison between them, and no check on a line. The catalog can hold a selling price above
the batch MRP; the line can carry `price_minor 20000` with `mrp_minor 10000`; and a batched line can omit MRP
entirely. GoFrugal treats MRP as the batch's authoritative field and edits price/MRP together
(community-change-selling).

```
=== 3. MRP is a legal ceiling in D27 but no rule, trigger or Worker check enforces it
    batched item price above the batch MRP: OK
    bill line at Rs200 with the line's own mrp_minor = Rs100: OK
    stored line: (20000, 10000, 1)
    the same bill with mrp_minor NULL (no MRP claimed at all): OK
    three MRP sources disagree and nothing reconciles them: (10000, 10000, 12000)
```

There is also a units hole: `item_batches.mrp_minor` has no basis. For a 25 kg sack billed in grams, is ₹1,500 the
MRP of the sack or per kg? `bill_lines.mrp_minor` inherits the ambiguity, so a comparison cannot even be written
until the basis is defined.

**Fix.** Define `mrp_minor` as the MRP of `price_basis_qty_base` units (the same basis as the price row, mandatory
when `price_includes_tax` is meaningful), and enforce three things: (a) a line trigger
`WHEN NEW.mrp_minor IS NOT NULL AND (NEW.list_amount_minor - NEW.discount_minor) * NEW.price_basis_qty_base >
NEW.mrp_minor * NEW.qty_base` → abort; (b) the Worker sets `policy_violation` for the same condition plus a missing
MRP on a batched line; (c) on batch/price write, `price_minor <= mrp_minor` when both exist. Drop
`item_prices.mrp_minor` or define it as a copy of the batch's.

**Cost.** One column-semantics paragraph, two triggers, one Worker predicate, tests. Half a day; without it the
batch model's stated reason to exist is decorative.

### D. `price_includes_tax = 0` is an item the DDL can create and cannot bill (High)

**Scenario.** `items.price_includes_tax` is in the brief, copied onto the line (0002), and editable through
PATCH items. D23 defines one money rule, tax-inclusive, and api-routes §9.5 admits: "If the brief intended
exclusive tax, money and report totals need a schema change." Until then an honest exclusive bill cannot satisfy
both identities: `line_total = list_amount − discount` and `line_total = taxable + tax` together force
`list_amount = taxable`, i.e. no tax, unless `discount` is negative (forbidden).

```
=== 2. Exclusive-priced item (items.price_includes_tax = 0): a correct sale cannot be stored
    honest exclusive bill: taxable 10000 + 18% = line_total 11800, list_amount 10000: discount_identity
    same with a negative discount to make the identity hold: CHECK constraint failed: discount_minor >= 0
    only storable shape: list_amount secretly redefined as inclusive (11800 != catalog 10000): OK
    stored list_amount vs catalog tier price: (11800, 10000, 0)
```

The only storable shape silently redefines `list_amount_minor` as tax-inclusive, which contradicts D23 ("list_amount
… at the applied price source"); a Worker that compared `list_amount` to the catalog decomposition (finding G
proposes exactly that) would flag every such bill. GoFrugal itself scopes exclusive tax to purchases: the item-master
options are labelled "Tax on MRP [Exclusive] (Not for sales)" / "Tax part of MRP [Inclusive]"
(Tax-based-on-Item-Master.md). So inclusive-only is the right retail rule; the flag is the defect.

**Fix.** v1 has no exclusive pricing: refuse `price_includes_tax = 0` on item create/PATCH (trigger or route), and
document "all selling prices are MRP-inclusive, as in GoFrugal's sales path". If wholesale exclusive pricing is
wanted later, that is the schema change §9.5 names — decide it before immutable bills exist.

**Cost.** One route validation + one trigger (or a CHECK via table rebuild, not needed pre-code). An hour.

### E. Per-tier price precedence makes `price_version` name the wrong tier set (High)

**Scenario.** D26: "`price_version` and `price_store_id` … together name exactly one tier set." api-routes §9.12:
"Price precedence is per tier: store row wins, default falls back." Those cannot both hold when a store override
covers only some tiers — and nothing requires an override to repeat the default's tier set. A 1.5 kg sale then
prices its 1,000 g part from the store's v3 and its 500 g part from the default's v1, and the line can record only
one `(version, store)`.

```
=== 14. A store override that does not repeat every default tier: two scopes in one line, one version
    query 3 for a 1000 g tier in store s1: [('s1', 1000, 3, 3900)]
    query 3 for a 500 g tier in store s1: [(None, 500, 1, 2500)]
    rows that (item, store, version=3) returns: [(1000, 3900)]
    rows that version=1, store NULL returns: [(500, 2500), (1000, 4000)]
    a line naming v3/store s1 with the true 6400 list amount: OK
```

The Worker's D26 check (find the tier set by key, compare the claimed list price) therefore sees 3,900 for a line
that lists 6,400: either it flags a correct bill as `price_mismatch`, or it must recompute with per-tier fallback
and no longer knows which set the register used. `computed_list_price_minor` and `policy_violation` are unreliable
for every store that has an override.

**Fix.** Require a store override to be a complete tier set: on PUT prices for a store scope, copy the default's
open `qty_base` list and reject a set whose tier sizes differ (route validation now; a trigger can check coverage
against the open default rows). Then one `(version, price_store_id)` really is one set and D26's check is exact.
Cheaper alternative: `price_breakdown.parts[]` gains `{store, version}` per part and the Worker verifies each — more
code, no rule.

**Cost.** One route validation + a test; half a day.

### F. A line can name another store's override, and the check passes (High)

**Scenario.** D9/F7 scope the catalog by store so one terminal never holds another store's prices. The line's
`price_store_id` is free text with no tie to `bills.store_id` (SQLite cannot add a composite FK, and lines are
inserted before the header, so a trigger cannot see the bill). A terminal can bill store s1 at store s2's override,
and the Worker's lookup by key finds that row — so no flag.

```
=== 11. Another store's price override can be applied, and the Worker lookup by key finds it
    line at store s1 that names s2 as its price scope: OK
    worker lookup by (item, price_store_id, version) finds the other store's row: ('s2', 3000)
```

**Fix.** In the push handler: `price_store_id IS NOT NULL AND price_store_id <> bills.store_id` → reject the line's
price with `price_mismatch` (accept the bill, flag it), and count it in report 10. One predicate; a Worker
post-insert check is the only place it can live.

**Cost.** One predicate, one test. An hour.

### G. A discount can be recorded as a list sale, and a low `list_amount` hides it completely (High)

**Scenario.** Review-schema D32 is still open (TRIAGE §F: "price_source still terminal-asserted"). The DDL only
requires `override_by` when `price_source IN ('override','manual')`, so a Rs50 discount is stored with
`price_source='list'`, no approver, and does not appear in the override report. The second half is worse: nothing
ties `list_amount_minor` to the price or to the decomposition, so a terminal can write a low `list_amount` and a
matching `line_total`; the discount then computes to 0, the percentage ceiling sees 0%, and `price_mismatch` (which
compares the *rate*) stays 0.

```
=== 4. A discount recorded as a list sale: no override_by, invisible to the override report
    override report predicate (price_source IN override/manual) sees it?: (0,)
    lines carrying a discount with no approver: (1,)
=== 4.1. A low list_amount hides the discount and defeats any ceiling computed from it
    Rs 1 charged for Rs 65 of tamarind, claimed as a list sale: OK
    stored line: (4000, 1000, 1500, 100, 0, 0)
    what the catalog says that version costs: (6500,)
```

This is exactly the F2 threat D3 exists to close: offline, a modified terminal gives unlimited discount and the
server's own numbers say "list price, no discount".

**Fix.** (a) Trigger/CHECK: `discount_minor = 0 OR override_by IS NOT NULL` (and
`discount_minor <= list_amount_minor`); (b) the Worker recomputes `list_amount_minor` from the version's tiers
(Σ parts for tiered, `price × qty / basis` for linear) and sets `price_mismatch` on a difference — D2 already
recomputes the list price; it must recompute the list *amount* too; (c) add `discount_minor > 0` to report 10's
predicate.

**Cost.** One trigger, one Worker computation (the decomposition already exists in the register; port it), one
index/predicate. One day.

### H. The tax amount is never tied to the tax rate on the same line (High)

**Scenario.** D23 claims the money rules live in one place, and it does enforce the additive identities and the
split. It does not check that the tax carried equals `f(taxable, tax_rate_bp)`. Two wrong bills are accepted: an
18% line with zero tax, and a 5% line carrying 18%. Item history is not stored (D26), so the server cannot check
the *rate* against the item version the line claims — but it does not need to for this check: the line's own
`tax_rate_bp`, `taxable_minor` and total are enough.

```
=== 5. The stored tax is never tied to the stored tax_rate_bp
    18% line (tax_rate_bp 1800) with zero tax and no tax in the total: OK
    5% line (tax_rate_bp 500) carrying 18% tax: OK
      stored b_tax0: rate 1800bp on taxable 10000 carries tax 0: ACCEPTED
      stored b_tax18: rate 500bp on taxable 10000 carries tax 1800: ACCEPTED
    a line claiming an item_version the server never issued: OK
```

The consequence is the monthly GST return, not the customer: a shop can file tax it never collected, or keep tax
it did. GoFrugal's filing report is rate-wise (GST-Reports.md), so the rate column is load-bearing there too.

**Fix.** Worker check, no DDL (rounding must match the register exactly): for `price_includes_tax=1`,
`expected_tax = round(line_total*vat/(10000+vat))`; for 0, `round(taxable*vat/10000)`; flag `tax_mismatch` when
`cgst+sgst+igst+cess <> expected_tax`, and require `cgst = floor(tax/2)`. Add the case to
`check-invariants.py`'s pattern. Also worth storing `item_version`'s tax fields on the line (already there:
`tax_rate_bp`, `hsn`) and having the Worker flag when they differ from the current item — a diagnostic, not a
rejection.

**Cost.** One function shared by the Worker and the register, one flag column (or reuse `policy_violation`). Half
a day.

### I. `pricing_mode` has no definition, and the price query has no row for a fractional quantity (Medium)

**Scenario.** `fixed_only | tiered | linear` appears only in DESIGN-BRIEF and the item CHECK. Nothing in DECISIONS,
api-routes or query-catalogue says how each mode prices, what `list_amount_minor` must equal for each, or whether a
`fixed_only` item may be sold in a fraction of a pack. Query-catalogue §3 looks up `qty_base = ?2` exactly, so it
returns nothing for 1.35 kg of a linear item — the normal loose-goods sale — while an exact pack size resolves.

```
=== 15. linear / fractional quantities: the register price query has no row for them
    query 3 for a 1000 g sale of a linear item: [(4800, 1)]
    query 3 for a 1350 g sale of a linear item: []
    the same sale priced by pro-rating and stored (5% inclusive: tax 309, taxable 6171): OK
    a fixed_only item sold in a fraction of a pack is not refused either: OK
```

The money is storable once computed (`price_basis_qty_base` carries the basis), so this is a rule hole, not a
blocker: two implementations will disagree on the same cart.

**Fix.** One paragraph in D26/query-catalogue: `fixed_only` = qty must be a whole multiple of an open tier (else
the register prompts for the pack count); `tiered` = greedy decomposition over open tiers, `list_amount = Σ parts`;
`linear` = exactly one open tier at `price_basis_qty_base`, `list_amount = price × qty / basis`, prorated and
rounded once; `price_basis_qty_base` is the tier's basis in every mode. Add the linear case to §3.

**Cost.** Documentation + two register cases + one harness case. Two hours.

### J. Closing a price without a successor leaves a hole in the tier set (Medium)

**Scenario.** `valid_to` is the only expiring dimension; `trg_item_prices_close_only` allows a close and nothing
requires the successor version in the same batch. A datewise promo (GoFrugal's Datewise-Pricing, which reverts
automatically) written as "close v1, open v2" is fine; written as "close v1 at time T" it silently removes a tier
from that scope from T onward. The register's query then returns no row for that tier, so a 1.5 kg sale of an item
priced per 1,000 g and 500 g has no 500 g price.

```
=== 16. Closing the last open price of a scope leaves the item with no price at all
    close both default tiers of tam at t=500 (the promo ended, no successor written): OK
    query 3 in store s1 for a 500 g sale at t=600: []
```

**Fix.** Writer rule (Worker, in the same batch): closing any open row of a scope requires inserting the next
version for every tier of that scope; a datewise price is inserted with `valid_from`/`valid_to` in one row and is
never closed by hand. Add an admin check "items with no open price in a store" for the repair path.

**Cost.** One validation + one report query. Two hours.

### K. Rounding is invisible to the report, so the day's sales ≠ the day's cash (Medium — known, TRIAGE §F D9)

**Scenario.** `total_minor = Σ lines + rounding_minor` and `sales_daily.amount_minor = Σ line_total_minor`, so the
day report (which reads lines, D10) is the pre-rounding value while the drawer holds the rounded one. At 600
bills/day that is up to ₹300/day (50 paise a bill) of unexplained difference, and `rounding_mode` is not copied
onto the bill, so after a settings change the delta cannot be explained.

```
=== 13. Rounding: the day's bill totals do not equal the day's line totals
    day report value from lines (D10) vs money the customer paid: (106200, 106171, -29)
    rounding_mode is not copied onto the bill: (0,)
```

**Fix.** The review's own fix: `bills.rounding_mode` (copied at sale time), a `rounding` row in the day summary
(`SUM(rounding_minor)` over bills), and `CHECK (rounding_mode <> 'none' OR rounding_minor = 0)`. Payments are out
of scope, but the report must reconcile to `bills.total_minor`, which is what the drawer holds.

**Cost.** One column (0009), one report field, one harness case. Two hours.

### L. No statutory rate-wise report (gap, Medium)

**Scenario.** GoFrugal's filing reports are "Sales and SR register-GST% wise" and "summary-GST% wise"
(GST-Reports.md). Traasu's only report cut is per item (`GET /v1/admin/reports/sales-by-item`, api-routes §3.6).
The data is on the immutable lines (`tax_rate_bp`, `hsn`, `cgst/sgst/igst/cess`), so this is a missing route, not
a missing model. DESIGN-BRIEF's non-goals list does not exclude it, and D23 says the per-rate summary is derived
from lines — but nothing serves it.

**Fix.** `GET /v1/admin/reports/gst-summary?from&to&store_id`, grouped by `(tax_rate_bp, hsn)`, from
`bill_lines JOIN bills` with `voided = 0`, capped at 92 days (D10). Returns taxable, CGST, SGST, IGST, cess, total.
No DDL.

**Cost.** A few hours. It is the report the shop files from every month.

### M. Offers, coupons and price levels: gap, additive, with one rule to write now (gap)

GoFrugal has a full offer engine (Offer-Management.md: 7 offer types, 46 schemes; discount % on bill, item
discount, free item, special price, happy hours, first N customers, customer type), coupons with serials and
cross-outlet redemption (Discount-Coupon1.md), and customer-type price levels computed off MRP/selling/purchase
cost (Price-Level.md). Traasu has per-item `allow_discount_pct`, manual overrides, and nothing else. Coupons and
price levels need customers, which v1 excludes; bill-amount offers do not, and every grocery runs them.

Capacity check, reproduced (case 6): the line/money model carries the two shapes without breaking D23 —

* a free item is a normal line with `discount_minor = list_amount_minor` (accepted; bill total 0);
* a bill-level discount is only storable **apportioned to lines**. Apportioned, the bill is accepted; carried as a
  header figure it is refused (`CHECK constraint failed: rounding_minor BETWEEN -100 AND 100`), and a negative
  "discount line" is refused by `qty_base > 0`.

```
=== 6. Offers: a free item and an apportioned bill-level discount fit; an unapportioned one does not
    free line (100% discount) at an 18% rate: OK
    bill total for the free-item bill: (0,)
    bill-level Rs50 off apportioned to the discounted line: OK
    an unapportioned Rs50 bill discount parked in rounding_minor: CHECK constraint failed: rounding_minor BETWEEN -100 AND 100
    a discount line as a negative-quantity line: CHECK constraint failed: qty_base > 0
```

That is the correct GST behaviour (a pre-supply discount reduces taxable value, so it must reduce each line's
taxable value by rate), but it is accidental: nothing states it, and a header-level `bill_discount_minor` added
later would break `total = Σ lines + rounding`.

**Fix (now, one paragraph).** "A bill-level discount is apportioned across lines, pro-rata to `list_amount`, before
tax; there is no header discount column; the offer identifier and its rule go into `price_breakdown` JSON." Then
the offer engine is additive: a new `offers` table, a register rule, no change to bills/lines. Free-item tax is
zero (verified) because the discount reduces the taxable value, which is also what the law expects.

**Cost.** Documentation now; the engine is post-v1 work with no migration.

### N. Weighing-scale barcodes cannot be resolved (gap, High)

GoFrugal's barcode profile embeds weight/qty in the printed barcode per item class ("Wt - Weight Prefix",
"Wt - Weight", precision; Barcode-Profile-Configuration.md). Traasu's `item_identifiers` resolves an exact alias to
`(item_id, qty_base)`, so a scale prints a different 13-digit code for every weighing and no row exists; the item
flags `scale_qty`/`prompt_qty` exist but no rule uses them (grep: they appear only in the catalog example). A
grocery weighing loose vegetables, rice or dal at the counter cannot scan its own labels. This is not in the
non-goals list.

**Fix.** A `barcode_profile` in `terminal_config.config_json` (prefix, item-code length, value kind
weight|price|qty, precision) and a documented register parse: prefix → item (via its `plu`/`sku`), embedded value →
`qty_base` or the line amount, then the normal price lookup at that quantity. No DDL change (`config_json` is
already in the catalog and `catalog_changes`).

**Cost.** One rule + one register parser + tests. A day, and it is the difference between scanning and typing for
the busiest counter in the shop.

### O. Offline lease, clock skew, and closed periods (risk, Medium each)

* **Lease (D4).** A register stops billing after 7 days without a successful sync. GoFrugal has no such cap; its
  offline billing runs until the server is back. A shop on a flaky line for a week gets a hard stop — a
  sale-blocking behaviour in a design whose stated rule is "the server must not abort a sale the shop made". The
  lease is the *right* instrument for revocation (F3); 7 days is the wrong constant for Indian retail. Make it
  per-store in `terminal_config`, default 14, and warn at 5 days and 2 days. Cost: policy + one field, already
  modelled.
* **Clock skew.** `business_date` is self-consistent by construction (D11), so a terminal whose clock is a year
  slow files a sale into a closed period and the DDL accepts it (case 12: both a year-old and a future
  `created_at` accepted). GoFrugal makes the same trade-off — offline billing cannot be enabled while "Take server
  system date as billing date" is on, so the counter clock is authoritative offline too — and lists the
  consequence as a business risk (Offline-Billing PDF, Risks). Add: the first online sync after a long gap compares
  the terminal clock with `server_time` and, past a threshold, raises an `audit_events` row plus a `clock_skew`
  flag on the bills it pushes; never rewrite the date. Cost: one flag, one audit kind.
* **Void after close.** `bill_voids` decrements the rollup of the bill's own `business_date` with no period lock,
  and `voided_at` is the terminal's clock (`NEW.created_at`), so a void can even be back-dated before the sale.
  GoFrugal's day-end/close concept bounds this. Cheapest: `store_settings.closed_through`; a void whose bill date is
  on or before it is accepted, recorded, and flagged `void_after_close`; the month report shows the adjustment
  separately. Cost: one column, one rule.

### P. `local_seq` after a reinstall, and a mutable `display_no` (risk, Medium/Low)

A re-installed register starts at 1 again and every bill is rejected `duplicate_seq` forever (case 10:
`UNIQUE constraint failed: bills.store_id, bills.terminal_id, bills.local_seq`); bills are parked, never dropped,
so the shop's records never reach the server. GoFrugal's counter-wise series assumes the counter keeps its series.
Seed `local_seq` on enrolment from `MAX(local_seq)` for that `(store, terminal)` (a field in the enrol/catalog
response), or issue a new terminal id per install. Separately, `display_no` is not in `trg_bills_money_frozen`, so
the number printed on the customer's slip can be edited after insert (case 10: `OK`); add it to the freeze list.

**Cost.** One response field and one trigger column list. An hour.

## 3. What holds up (checked, no action)

* **Money identities.** The inclusive example (1,500 g, ₹40/1,000 g, 5%) stores exactly as documented, header
  6,190 + 310 = 6,500 (case 1); an unapportioned header discount is refused by the rounding bound (case 6); a
  header total that disagrees with its lines is refused (case 8's negative bill). The discount-identity bypass,
  the odd-paisa split rule and IGST/CGST exclusivity are already asserted by `check-invariants.py` ("discount with
  list_amount 0", "split 23/26 violates floor rule", "IGST plus CGST on one line"), which still passes; my cases
  4/4.1 show only what that identity does **not** cover (a discount with a plausible `list_amount`).
* **Replay of the header.** Lines `DO NOTHING`, the header `DO NOTHING`, and the rollup is trigger-maintained, so
  a duplicate cannot double-count revenue (case 7: `sales_daily` unchanged by the replay). Only the stock ledger
  is exposed (finding B).
* **Void machinery.** Void flips the bill, decrements the rollup, refuses a second void, refuses a whitespace
  reason, refuses un-void, and refuses a cross-store void (case 8; also `check-invariants.py`).
* **Tenders are not blocked.** `bills.total_minor` is the single reconciliation point and no column assumes a
  payment shape. A later `bill_tenders(bill_id, tender_id, amount_minor)` with `Σ amount = total_minor` is purely
  additive, and GoFrugal's tender-wise prefix (Bill-Prefix-Master.md) becomes one more dimension on
  `numbering_policy` — no rebuild of `bills`. The only thing to avoid now is writing a header-level discount
  column, which would break `total = Σ lines + rounding` (finding M).
* **Returns are not blocked either, but are not representable as negative bills.** `qty_base > 0`,
  `line_total_minor >= 0`, `total_minor >= 0` and `uq_bill_voids_bill` make a negative or partial reversal
  impossible by construction (case 8: a negative bill is refused). That is deliberate (D25 D45); a return must be
  its own document type that references the original bill, and it will not touch `bills`/`bill_lines`.
* **Offline posture vs GoFrugal.** GoFrugal parks an "Inaccurate" offline bill (report 3262) and will not return
  to online mode while bills are pending; Traasu accepts what the shop sold and flags it (D2, D3, D12). Traasu's
  choice is the better one for a shop and is deliberate. It also inherits the risk GoFrugal names — a restored
  backup duplicating a bill number and collapsing stock — and closes it better: client UUID + `ON CONFLICT`,
  plus `uq_bills_store_terminal_seq`.

## 4. Not checked

* The batch picker's exact ordering and the book-1-first rule beyond confirming it matches D28 (ParityStock owns
  batch/stock depth); I did not re-derive the picker's `IFNULL(price, ...)` behaviour for a batch with **no** price
  row — query-catalogue §3's `LEFT JOIN` would return a NULL price and the register has no documented fallback.
  Flagged there rather than duplicated here.
* GoFrugal's sales-return *screen* (no page in the local set; only GST-Reports.md's "SR" columns and the offline
  PDF's risk list). All return statements are grounded on those two only.
* Loyalty beyond the gap row (Creating-Loyalty-Program.md, Loyalty.md are local; not needed for a non-goal).
* Concurrency inside one D1 database under real load; the numbering race is reproduced at the SQL level, not
  through two live Worker invocations.
* `check-schema.py`, `check-invariants.py` and `explain-queries.py` pass on the unmodified repo (run after this
  audit; output `ok` / `OK` / `OK`). My proposed triggers/indexes are not applied, so each would need one new case
  in the harness before it can be said to work.
