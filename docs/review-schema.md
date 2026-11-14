# Schema review — adversarial

Reviewed: `docs/DESIGN-BRIEF.md`, notes 03 (`D1 schema impact`) and 06 (`BRD alignment`), the current
`docs/d1-schema.sql`, and `docs/query-catalogue.md` as the yardstick. Where the DDL and the catalogue disagree,
the disagreement is itself a defect.

Stance: a wrong total or a lost bill costs the owner money and cannot be argued away with "diagnostic only".
Every item below has a row-level example — rows in, wrong rows out. Fixes are schema or query changes only: no
new service, no new dependency, no worker-side machinery.

Severity: **S1** wrong money, illegal invoice, or silent loss · **S2** needs manual repair or will hit a hard
limit · **S3** support cost.

**Verdict: ISSUES — 48 numbered defects, 23 of them S1. Zero blockers: every fix below is a schema or query
change.**

What the schema already gets right, so it is not re-litigated: every table is `STRICT` (a REAL in
`total_minor` is rejected — verified), `bill_lines` has `PRIMARY KEY (bill_id, line_no)`, `uq_item_prices_open`
forbids two open prices per tier, `uq_bills_invoice_no` exists, money-bearing parents are `ON DELETE RESTRICT`,
`catalog_changes` is the only cursor, `bill_lines.item_id` and `bills.store_id`/`terminal_id`/`user_id` are
`ON DELETE RESTRICT`, and `rounding_minor` is not a float. The defects are the gaps around that
skeleton.

---

## 1. Money and quantity

### D1. `line_total_minor` has no tax-inclusion contract, and no bill-level identity exists — S1

**Scenario.** An item is priced at MRP, tax included; another is priced pre-tax. Both produce
`line_total_minor` values that mean different things, and nothing on the line says which.

**Rows.** TEA: `price_includes_tax=1`, price 10000, `tax_rate_bp=500` → `taxable=9524`, `cgst=238`, `sgst=238`,
`line_total_minor=10000`. SOAP: `price_includes_tax=0`, price 10000, `tax_rate_bp=1800` → `cgst=900`,
`sgst=900`, `line_total_minor=11800`. Bill: `total_minor` is either 20000 (sum of pre-tax prices) or 21800
(what the customer paid). Reports query 7/8 sum `line_total_minor` and call it revenue; the invoice needs the
taxable value. One of the two readings is wrong on every bill, and `bills` has no `subtotal_minor`/`tax_minor`
to disambiguate or to reconcile against the lines. A pushed bill with `total_minor=0` and lines summing 40000
passes every constraint.

**Fix.** Define `line_total_minor` as the tax-inclusive amount charged for the line. Add
`taxable_minor INTEGER NOT NULL CHECK (taxable_minor >= 0)` and
`CHECK (line_total_minor = taxable_minor + cgst_minor + sgst_minor + cess_minor)`.
Add to `bills`: `subtotal_minor`, `tax_minor` with
`CHECK (total_minor = subtotal_minor + tax_minor + rounding_minor)`. The header identity is enforceable in one
row; the header-vs-lines cross-check is a one-statement verification after each push batch (code, not schema),
and the columns make that check possible at all.

### D2. `price_includes_tax` is not copied onto the line, so historic money changes meaning — S1

**Scenario.** The schema's stated rule is that name, price and tax are copied so reports never re-derive from
mutable tables. `items.price_includes_tax` and `items.hsn` are the tax fields **not** copied, and the first is
the one that decides how to read the copied numbers.

**Rows.** Bill line from January: `line_total_minor=10000`, `cgst=238`, `sgst=238`. In March the owner flips
the item to `price_includes_tax=0`. A reprint or a credit note now reads the same untouched row as taxable
10000 + 476 tax = 10476, and the January GST summary disagrees with the invoice. The line itself never changed.

**Fix.** `ALTER TABLE bill_lines ADD COLUMN price_includes_tax INTEGER NOT NULL DEFAULT 1 CHECK (price_includes_tax IN (0,1))`
and copy `hsn` too (`bill_lines.hsn TEXT`). Backfill from `items` in the migration, then drop the default so a
writer must supply it.

### D3. Prices have no quantity basis, so no line can be re-derived and the discount cap is uncomputable — S1

**Scenario.** `price_minor` and `list_price_minor` are bare integers. For a tier they mean "price of
`item_prices.qty_base` units"; for a manual line they might mean the whole line; for a 1.75 kg tiered sale
there is no single price at all. `price_breakdown` is opaque.

**Rows.** SUGAR tiers: 1000 g → 4000, 500 g → 2500, 100 g → 700. Sell 1750 g → greedy 1000 + 500 + 250, the
250 pro-rated from the 100 g tier = 1750, `line_total_minor=8250`. What goes in `price_minor`? 4000 makes the
receipt read ₹40 × 1.75 kg = ₹70 against an ₹82.50 charge. 471 (rounded paise/gram) loses the tier structure
and is a float in disguise. Report 10 prints `list_price_minor` beside `price_minor` as if they were
comparable. `allow_discount_pct=10` cannot be enforced because the list value of the decomposition is not
stored: check against `price_minor` and an 8250 charge on a 4000 "price" looks like no discount at all; check
against the charged amount and every discount is 0%. `policy_violation` is then the terminal's opinion, not a
fact any report can reproduce.

A second row shows double rounding. A pro-rated line of 1750 g from a 100 g / 699 tier: rounded once at the
line, `1750 × 699 / 100 = 12232.5` → half-up 12233. If the implementation first rounds the per-gram rate to 7
paise and then multiplies, the line is 12250 — 17 paise more for the same goods, and the error grows with
quantity. Nothing in the schema forbids storing a rounded unit rate in `price_minor` and letting the next
calculation use it, because `price_minor` has no denominator and no "display only" marker.

**Fix.** Add to `bill_lines`:
`price_qty_base INTEGER CHECK (price_qty_base IS NULL OR price_qty_base > 0)` (the quantity the quoted price
refers to; `NULL` only for decomposed lines), `list_amount_minor INTEGER NOT NULL CHECK (list_amount_minor >= 0)`
(the list value of the whole line), and
`CHECK (line_total_minor = list_amount_minor - discount_minor)`,
`CHECK (price_source <> 'list' OR price_minor = list_price_minor)`,
`CHECK (json_type(price_breakdown,'$.total') IS 'integer')`,
`CHECK (json_extract(price_breakdown,'$.total') IS list_amount_minor)`.
`price_minor`/`list_price_minor` immediately become display metadata for single-basis lines, and the
breakdown reconciles to the money. State the arithmetic rule once: every amount is computed from integer
quantity and integer price in one expression and rounded half-up exactly once with `(2*a + b) / (2*b)` for
positive integers; no stored rate is ever multiplied again.

The new `computed_list_price_minor`/`price_mismatch` pair does not close this. The Worker's baseline is one
number compared to the terminal's `list_price_minor`, and neither carries the quantity it refers to. For the
1750 g sale above, two correct implementations can produce different numbers and `price_mismatch` fires on a
legitimate line; a lying terminal that gets the basis wrong is equally hard to convict. The baseline needs the
same `price_qty_base`/`list_amount_minor` shape as the claim.

### D4. Tax is rounded three times with no stored sum and no interstate path — S1

**Scenario.** `cgst_minor` and `sgst_minor` exist; `igst_minor` does not. Nothing requires the two halves to be
equal, and no row stores a tax total that can be checked against the taxable value.

**Rows.** Taxable 7024 at 5% (`rate_bp=500`). Implementation A computes tax once (351.2 → 351) then splits
175/176: CGST ≠ SGST, which an intra-state invoice does not allow. Implementation B computes each half (175.6 → 176, 176): sum 352,
effective rate 5.011%, and a bill-level recompute from the taxable sum yields 351 — a one-paisa disagreement
between the invoice and the return. Both pass today. A store in another state must issue IGST; with only
`cgst_minor`/`sgst_minor` columns it cannot, and `stores` has no `gstin`/`state_code` to decide.

**Fix.** Add `igst_minor`, `taxable_minor`, `stores.gstin`, `stores.state_code`, and
`bills.place_of_supply_state`. Enforce `CHECK (igst_minor = 0 OR (cgst_minor = 0 AND sgst_minor = 0))` and
`CHECK (cgst_minor = sgst_minor)`. Round each statutory component **once**, from the taxable value, with
integer half-up: `cgst = (2*taxable*rate_bp + 20000) / 40000`, `sgst = cgst`, `cess = (2*taxable*cess_bp + 10000) / 20000`.
Then derive `taxable` last for inclusive items so the line identity in D1 closes exactly. Never recompute bill
tax from `SUM(taxable) × rate`; the line components are the truth and the bill columns are their sums.

### D5. `price_breakdown` accepts floats and can contradict the money — S2

**Scenario.** `CHECK (json_valid(...))` is the only guard. JSON numbers may be REAL, and the breakdown is never
reconciled to `line_total_minor`.

**Rows.** `{"total":50.0,"components":[{"qty":500,"price":25.0}]}` is accepted; `json_extract(...,'$.total')`
returns a REAL and any report or audit tool that reads it sums floats. A breakdown with `"total":4950` beside
`line_total_minor=5000` is also accepted — the audit trail states one amount, the money another.

**Fix.** The two `json_type`/`json_extract` CHECKs in D3. Make `$.total` mandatory (that CHECK fails when the
key is absent, because `json_type` returns NULL and `NULL IS 'integer'` is 0 — verified).

### D6. `dimension` and `base_unit` are independent, so a base-unit typo is a 1000× error — S1

**Scenario.** `items.base_unit` has a plain FK to `units(unit)`. Nothing ties it to `dimension`, and nothing
requires the base unit to have `factor_to_base = 1`.

**Rows.** RICE: `dimension='mass'`, `base_unit='kg'` (the admin picked the display unit). Prices are authored
in grams (1 kg tier = `qty_base 1000`), quantities are entered in grams, and the display layer divides by the
kg factor: a 1000 g sale displays as 1,000 kg, or a 1 kg sale bills for 1,000,000 g. The FK passes; every
CHECK passes. A second row: `dimension='count'` with `base_unit='g'` passes, and pieces are priced per gram.

**Fix.** Pure declarative, verified to reject both rows:
```sql
CREATE UNIQUE INDEX uq_units_group ON units(unit, unit_group);
CREATE UNIQUE INDEX uq_units_factor ON units(unit, factor_to_base);
-- on items:
base_factor INTEGER NOT NULL DEFAULT 1 CHECK (base_factor = 1),
FOREIGN KEY (base_unit, dimension) REFERENCES units(unit, unit_group),
FOREIGN KEY (base_unit, base_factor) REFERENCES units(unit, factor_to_base),
FOREIGN KEY (default_sales_unit, dimension) REFERENCES units(unit, unit_group)
```

### D7. The kg→g conversion crosses a float; `INTEGER` storage does not fix the input — S2

**Scenario.** The schema stores integers correctly, so the loss happens before the INSERT: the scale or the
admin UI sends a decimal quantity and the client multiplies.

**Rows.** `1.005` kg in JavaScript: `1.005 * 1000 = 1004.9999999999999`. `Math.trunc` gives **1004 g**; at
₹400/kg that is 40 paise per weighed sale, and the gram counter is now permanently off. The same class:
`Math.round(1.005 * 100) = 100`, so an imported price of ₹1.005 becomes 100 paise, not 101. A driver that
bypasses the conversion and writes the float is now rejected by `STRICT` — that part is right.

**Fix.** The API accepts `qty_base` only, and converts decimal strings by digit shifting (`"1.005"` → 1005),
never by `Number × factor`. Add `CHECK (units.precision BETWEEN 0 AND 6)` (present) and document that
`precision` is display-only. Add `items.base_factor` (D6) so the factor 1000 case is structurally impossible.

### D8. Report 9 divides in integer arithmetic and mixes tax bases — S1

**Scenario.** Query catalogue 9 returns `SUM(line_total_minor) / NULLIF(SUM(qty_base),0)` as "average price".

**Rows.** One month for TAMARIND: `SUM(line_total)=8250`, `SUM(qty)=1750` → SQLite integer division returns
**4** (paise per gram), i.e. ₹40/kg, while the actual effective rate is ₹47.14/kg — a 15% understatement in an
owner-facing column. If some lines are pre-tax and some inclusive (D1), the numerator sums two different
things. If the item is sold by the piece and by weight (possible today: `dimension` is copied per line, but the
report groups by `item_id` only), pieces and grams are added together.

**Fix.** Multiply before dividing and fix the scale explicitly:
`SUM(line_total_minor) * 1000 / NULLIF(SUM(qty_base),0)` for mass/volume, `/ SUM(qty_base)` for count,
grouped by `(item_id, dimension)`. Add `AND b.voided = 0` (see D36) and state that the result is display
money, never posted.

### D9. Rounding is stored but neither constrained nor reported — S2

**Scenario.** `rounding_minor` is on the bill; nothing ties it to `store_settings.rounding_mode`, and no report
reads it.

**Rows.** Lines sum 7376. `rounding_mode='nearest_rupee'`, `total_minor=7400`, `rounding_minor=24` is correct.
A bug writes `total_minor=7376, rounding_minor=24` (the charge rounded twice) or `rounding_minor=99` with mode
`'none'`; both pass the `BETWEEN -100 AND 100` CHECK. Report 7 sums `line_total_minor` → ₹73.76 while the
owner's bank/cash total is ₹74.00; summed over 500 bills/day the day report is ₹120 light with no row that
explains it. The bill does not copy `rounding_mode`, so after a settings change the delta cannot be audited.

**Fix.** `bills.rounding_mode` (FK to a `rounding_modes` code table, not a CHECK — see D44),
`CHECK (rounding_mode <> 'none' OR rounding_minor = 0)`,
`CHECK (rounding_mode <> 'nearest_rupee' OR rounding_minor BETWEEN -50 AND 50)`.
Add a `rounding` line to the day summary (a view over `bills` summing `rounding_minor`) so the report's
"net sales" and "cash due" are two named numbers instead of one silently merged one.

### D10. Two scans of the same item produce a different total and the schema cannot say why — S1

**Scenario.** Tiered pricing is per line. Whether the cart merges two scans into one line changes the money,
and a line has no pack identity.

**Rows.** Bill A: one line SUGAR `qty_base=1000` → 4000. Bill B: two lines SUGAR `qty_base=500` each → 2500 +
2500 = 5000. Identical goods if the customer took two 500 g packs; ₹10 wrong if it was one 1 kg pack. If the
cart keys by `item_id` (natural when nothing distinguishes scans), Bill B is silently merged into Bill A and
the customer is undercharged ₹10. The month report shows the same `SUM(qty_base)` for both bills and different
revenue, so the discrepancy is invisible in exactly the report that would catch it.

**Fix.** Never merge scans; `line_no` already allows it. Add `bill_lines.scanned_code TEXT` (the alias/EAN as
scanned) and `package_qty_base INTEGER CHECK (package_qty_base IS NULL OR package_qty_base > 0)`. The line then
states "one 500 g pack at ₹25" instead of only "500 g", and a customer dispute is answerable.

---

## 2. Immutability

### D11. A void cannot reach the server, and it can be anonymous — S1

**Scenario.** Bills are immutable and pushed once with `ON CONFLICT(id) DO NOTHING`. A void is an update to an
existing row. There is no void event and no second push path.

**Rows.** Bill `b1`, ₹4,000, synced at 10:00 (`synced_at` set). Voided at 15:00: `voided=1, voided_at=...,`
locally. Re-pushing it hits `ON CONFLICT(id) DO NOTHING`; the Worker returns the id as existing; nothing
updates `bills.voided` on the server. The cloud day report keeps the ₹4,000; the terminal's report drops it;
there is no row anywhere that records the disagreement. If the terminal does not re-enqueue at all (there is no
`dirty` flag), the void never leaves the machine. The schema also accepts `voided=1, voided_by=NULL,
void_reason=NULL` — verified — so an anonymous void of a ₹4k bill is legal.

**Fix.** Voids become append-only events with their own sync unit:
```sql
CREATE TABLE bill_voids (
  bill_id       TEXT PRIMARY KEY REFERENCES bills(id) ON DELETE CASCADE,
  store_id      TEXT NOT NULL REFERENCES stores(id) ON DELETE RESTRICT,
  voided_by     TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  voided_at     INTEGER NOT NULL,
  reason        TEXT NOT NULL CHECK (length(trim(reason)) > 0),
  synced_at     INTEGER
) STRICT;
```
Push with `ON CONFLICT(bill_id) DO NOTHING` (idempotent, append-only) and maintain `bills.voided` from it in the
same batch: `UPDATE bills SET voided=1, voided_at=..., voided_by=... WHERE id=? AND voided=0`. Terminal
retention may delete a synced void with its bill (D14); an unsynced one blocks the prune. Add
`CHECK (voided = 0 OR (voided_by IS NOT NULL AND voided_at IS NOT NULL AND void_reason IS NOT NULL))` now.
Reports switch to `WHERE NOT EXISTS (SELECT 1 FROM bill_voids v WHERE v.bill_id=b.id)`; keep the partial index
on `bills` only if the materialisation is trustworthy.

### D12. "Immutable once written" is a comment; the money row is updated by three paths — S2

**Scenario.** `bills` carries `synced_at`, `received_at`, `invoice_no`, `staff_inactive` — all server-written
updates on the same row as `total_minor`. There is no trigger.

**Rows.** A retry path ships `UPDATE bills SET synced_at=? WHERE store_id=?` instead of `WHERE id=?`: 4,000
bills are touched and, on a longer typo, `total_minor` is in the same statement's reach. A void path that
writes `voided=1, total_minor=0` ("hide it from reports") is accepted by every constraint.

**Fix.** Freeze the business columns with a trigger on both copies (written against the schema after D1 adds
`subtotal_minor`; drop that line if you apply this first):
```sql
CREATE TRIGGER bills_freeze_money BEFORE UPDATE ON bills
WHEN OLD.total_minor IS NOT NEW.total_minor
  OR OLD.subtotal_minor IS NOT NEW.subtotal_minor
  OR OLD.created_at IS NOT NEW.created_at
  OR OLD.store_id IS NOT NEW.store_id
  OR OLD.terminal_id IS NOT NEW.terminal_id
  OR OLD.user_id IS NOT NEW.user_id
  OR OLD.business_date IS NOT NEW.business_date
BEGIN SELECT RAISE(ABORT,'bills: money and identity are immutable'); END;

CREATE TRIGGER bill_lines_immutable BEFORE UPDATE ON bill_lines
BEGIN SELECT RAISE(ABORT,'bill_lines are immutable'); END;
```
Put `received_at`/`invoice_no`/`staff_inactive`/`synced_at` in `bill_server_state(bill_id PK, ...)` so the
`UPDATE` grant surface on money rows is empty. `stale_price` is a Worker diagnostic, not line data: move it to
`bill_line_diagnostics(bill_id, line_no, stale_price, price_mismatch, computed_list_price_minor, checked_at,
PRIMARY KEY (bill_id, line_no))`, or the blanket trigger blocks the very updates the sync contract requires.
The schema doc declines triggers because the server updates `invoice_no` and `voided` after insert; that is an
argument for moving those fields to `bill_server_state` and `bill_voids`, not for leaving `total_minor` writable.
With the side tables the trigger has no exception list to drift.

### D13. `invoice_no` is assigned by an update after insert, which contradicts D12 — S2

**Scenario.** `invoice_no` is nullable and server-assigned; the only way to set it is an `UPDATE` after the
insert. Under D12's trigger that update aborts. Without the trigger, the legal series is mutable.

**Rows.** Two pushes race (two Worker invocations; Cloudflare D1 serialises, but the second sees the first's
row): both compute `MAX(invoice_no)+1 = 432`, one wins the unique index, the other's whole 100-bill batch fails —
`batch()` is atomic, so 99 good bills are parked. A crash between the counter bump and the insert leaves a hole
in a series that `numbering_policy` has no `gapless` flag to describe.

**Fix.** Assign inside the same batch, before the insert, from the counter row:
`UPDATE numbering_policy SET next_value=next_value+1 WHERE id=? RETURNING next_value` (never `MAX()+1`), then
insert. Add `numbering_policy.gapless INTEGER NOT NULL DEFAULT 0` so the hole policy is explicit, and
`UNIQUE(store_id, invoice_no) WHERE invoice_no IS NOT NULL` is already present — keep it.

### D14. Retention deletes the evidence the ledger needs — S2

**Scenario.** Synced bills are pruned after ~30 days. `inventory_movements.bill_id → bill_lines` is
`ON DELETE CASCADE`, and `bill_lines.bill_id → bills` is `ON DELETE CASCADE`.

**Rows.** Day 1: bill `b1` synced, movements written. Day 31: the prune deletes `b1`; its lines and its
movements go with it. The local stock projection loses 30 days of history in one tick, and a void raised on
day 35 for a day-20 bill has no row to reference (the bill is gone), so the correction cannot be recorded at
all. The cloud still has the bill and no void.

**Fix.** A ledger must outlive the document that caused it: drop the FK from `inventory_movements` to
`bill_lines` (SQLite cannot drop an FK in place, so this is a table rebuild — cheap now, a migration of a large
table once the ledger has history), keep `bill_id`/`bill_line_no` as a soft reference, add `store_id NOT NULL` (D28) and
`UNIQUE(bill_id, bill_line_no)`, and let retention delete bills and lines only. Prune only when no void is
pending, and only after the correction window (a late void must still have a bill to point at):
```sql
DELETE FROM bills
WHERE synced_at IS NOT NULL AND synced_at < ?
  AND NOT EXISTS (SELECT 1 FROM bill_voids v WHERE v.bill_id = bills.id AND v.synced_at IS NULL);
```
If reports are cloud-only past retention, say so in the terminal UI instead of silently under-reporting.

### D15. Deleting an item cascades away its price and identifier history — S3

**Scenario.** `item_prices.item_id` and `item_identifiers.item_id` are `ON DELETE CASCADE`. `bill_lines`
RESTRICT protects sold items only.

**Rows.** `DELETE FROM items WHERE id='i9'` on an item created with a typo: its five price versions and its
EAN row disappear. The EAN is now free to be attached to the wrong product, and the catalog audit cannot say
which item owned it. Any bill that references the item would have blocked the delete — but the item was never
sold, so nothing does.

**Fix.** RESTRICT both; deletions are `active=0`. If a hard purge is ever wanted, it belongs to a maintenance
job that writes `audit_events` first.

---

## 3. SCD2 discipline

### D16. A price revision can be half-open: one tier at v6, another at v5 — S1

**Scenario.** One `PUT` is supposed to close every open row of a scope and open the next version for every
tier. `uq_item_prices_open` is per `(item, scope, tier)`, so it cannot see a partial write.

**Rows.** SUGAR open rows after a botched update: 1000 g at v6 ₹4100, 500 g at v5 ₹2500, 100 g at v5 ₹700. A
1250 g sale decomposes using v6 and v5; `bill_lines.price_version=6` states one version for a line that used
two. `price_breakdown` has no version per component. The "price valid at created_at" question now has no single
answer, and the stale check (D19) compares against one of them arbitrarily.

**Fix.** Enforce revision coherence with a trigger (verified to abort the mixed insert):
```sql
CREATE TRIGGER item_prices_revision_coherent BEFORE INSERT ON item_prices
WHEN EXISTS (
  SELECT 1 FROM item_prices p
  WHERE p.item_id = NEW.item_id
    AND IFNULL(p.store_id,'') = IFNULL(NEW.store_id,'')
    AND p.valid_to IS NULL
    AND (p.version <> NEW.version OR p.valid_from <> NEW.valid_from))
BEGIN SELECT RAISE(ABORT,'item_prices: mixed open revision'); END;
```
The legitimate writer closes all open rows and inserts all tiers in one batch; a partial write now fails
instead of shipping. The normalised alternative is an `item_price_revisions` header row that every tier
references — more correct, a larger change; the trigger gets the invariant today.

### D17. `price_version` does not identify the scope, and the same version can exist twice — S1

**Scenario.** `version` is per item, not per (item, scope). And even if it were per scope, `bill_lines` copies
only the integer.

**Rows.** Store override `(item, S1, 1000 g, v5, ₹3900)` and franchise default `(item, NULL, 1000 g, v5, ₹4000)`
both exist. A bill line with `price_version=5, price_minor=3900` is indistinguishable from one with
`price_version=5, price_minor=4000` by version alone. Query 15 has no store filter and orders by `valid_from`,
so it may compare the S1 bill against the default row — every override line in that store is flagged stale, or
a real mismatch passes.

**Fix.** Add `bill_lines.price_store_id TEXT REFERENCES stores(id)` (no CHECK with a subquery — SQLite forbids
it; the FK is the check), `''`/`NULL` meaning the franchise default — the scope the price came from. The stale
check becomes
`EXISTS (SELECT 1 FROM item_prices p WHERE p.item_id=l.item_id AND IFNULL(p.store_id,'')=IFNULL(l.price_store_id,'') AND p.version=l.price_version)`
plus a validity-range warning, with no clock dependency (D19).

### D18. Validity windows are unvalidated at the boundary — S2

**Scenario.** `valid_from` comes from the server, but nothing rejects a value in the wrong epoch, and
`valid_to` can already be in the past.

**Rows.** An import sends epoch **milliseconds** (`1730000000000`) into `valid_from`; the row is "open" from
the year 56,000. Every lookup at `now` returns nothing; the till shows no price for the item (₹0 line or a
blocked sale). A second row: `valid_from=2000, valid_to=2001` — born expired; identical outcome. A third: a
backdated row with a *later* `valid_from` than the current revision wins the resolver and the price moves
backwards. The resolver orders by `valid_from`, so `version` is not the order it says it is.

**Fix.** `CHECK (valid_from BETWEEN 1600000000 AND 4102444800)` and
`CHECK (valid_to IS NULL OR valid_to BETWEEN valid_from + 1 AND 4102444800)`. Add
`CHECK (created_at BETWEEN 1600000000 AND 4102444800)`. Reject `valid_to <= server_now` at the catalog
endpoint; the CHECK cannot reference `now()`. Also require the new `version` to be greater than every version
of that scope and `valid_from` no older than the open revision's (a BEFORE INSERT trigger), or resolve by
`version` instead of `valid_from` — today the two disagree and the backdated row wins.

### D19. The stale-price check never fires for tiered items, and mixes clock domains — S1 (diagnostic), S2 (trust)

**Scenario.** Catalogue query 15 matches `qty_base = <sold quantity>`. Sold quantities are not tier
quantities.

**Rows.** The 1.75 kg SUGAR line has `qty_base=1750`; tiers are 1000/500/100. Verified against the seeded
schema: the query returns **0 rows** for `qty_base=1750`, so `stale_price` stays 0 for every tiered sale — the
majority of a grocery catalog. Add the clock: `created_at` is the terminal's clock, price windows are the
server's. A terminal 2 days fast resolves "the price at created_at" to a row it never had.

**Fix.** Make the check structural, not temporal: the line carries `price_store_id` (D17) and `price_version`;
verify the revision exists (`EXISTS` on `(item_id, scope, version)`) and that its `valid_from <= received_at`.
If a real time check is wanted, clamp the bill's clock server-side:
`CHECK (received_at IS NULL OR created_at <= received_at + 300)`. Put `stale_price` in
`bill_line_diagnostics` (D12) and set it in a single statement over a batch, not a per-line query (D38).
The schema doc's Q4 is two scope calls and is better than catalogue query 15, but it still returns one version
for the whole item. A line priced from a store override on one tier and the franchise default on another has
two versions and records neither; per-component `store_id`/`version` in `price_breakdown` is the only exact
form (D17).

### D20. Price rows are editable in place, so a version's meaning can change — S2

**Scenario.** `item_prices` has no update guard. Closing a row (`valid_to`) is a legitimate update; rewriting
`price_minor` on a closed row is not.

**Rows.** A "typo fix" updates the closed v4 row from 4000 to 4100. Every historical bill that recorded
`price_version=4, price_minor=4000` now disagrees with the row its version points at; the stale check reports
false positives, and any support tool that resolves a bill through `item_prices` prints the wrong price.
Money in `bill_lines` is safe; the audit trail is not.

**Fix.**
```sql
CREATE TRIGGER item_prices_close_only BEFORE UPDATE ON item_prices
WHEN OLD.item_id IS NOT NEW.item_id OR OLD.store_id IS NOT NEW.store_id
  OR OLD.qty_base IS NOT NEW.qty_base OR OLD.version IS NOT NEW.version
  OR OLD.price_minor IS NOT NEW.price_minor OR OLD.valid_from IS NOT NEW.valid_from
  OR OLD.valid_to IS NOT NULL
BEGIN SELECT RAISE(ABORT,'item_prices: only an open row may be closed'); END;
```

---

## 4. Identifier collisions

### D21. The alias namespace ignores `kind`, so a PLU and a short code cannot coexist — S2

**Scenario.** `uq_item_identifiers_alias(store_id, alias) WHERE active=1` makes aliases, SKUs, EANs and PLUs
one namespace. The resolver has no precedence rule.

**Rows.** Store adds PLU `123` for BANANAS while a local short code `123` already maps to BREAD. The insert
fails inside a batch — the whole catalog write for that item is rolled back — or, if `123` was to be reused,
the second product can never be scanned. If both existed under different kinds, a cashier typing `123` gets
whichever row the resolver's `LIMIT 1` returns.

**Fix.** `CREATE UNIQUE INDEX uq_ident_kind ON item_identifiers(store_id, kind, alias) WHERE active=1;` and a
written precedence (ean/plu before alias/sku) applied in the terminal resolver and the server's search
endpoint. Keep the old index only if the single namespace is a decision — then say so where the resolver
documents it.

### D22. EAN is store-scoped, so one barcode means two products — S1

**Scenario.** Identifiers are per store; a barcode is a fact about the packet, not about the shop.

**Rows.** EAN `8901234567890` → TAMARIND in S1 and → TAMARIND SEEDLESS in S2. Both inserts succeed. A customer
carries a packet from S1 into S2 and it scans as a different product; a cross-store report shows one barcode
under two `item_id`s; a recall by barcode finds half the stores. `kind='ean'` is the one kind whose whole value
is global uniqueness.

**Fix.** `CREATE UNIQUE INDEX uq_ident_ean ON item_identifiers(alias) WHERE kind='ean' AND active=1;` (one
tenant, so global here is right). If a store genuinely needs a different product for a known EAN, that is a
supplier data error and should fail loudly.

### D23. A weight-embedded barcode cannot be represented — S1

**Scenario.** `item_identifiers.qty_base` is a fixed quantity or `NULL`. Variable-weight schemes encode the
quantity in the code.

**Rows.** EAN-13 `2 01234 5 01234 8` (prefix 2, item 01234, weight 01234 g). The item has
`qty_base=500` for the tray, so scanning bills 500 g at ₹400/kg = ₹200 instead of ₹493.60 — ₹293.60 lost on
one scan, invisible in reports because qty and money agree. With `qty_base=NULL` the cashier is prompted, types
"1" meaning one tray, and the mass item records 1 g = ₹0.40. No prefix rule or parse scheme is stored, so two
items sharing a prefix decode to whichever row matches.

**Fix.** Add `qty_source TEXT` (`fixed | embedded | ask` — a code table, D44) with
`CHECK (qty_source <> 'fixed' OR qty_base IS NOT NULL)`, and a rule table:
```sql
CREATE TABLE barcode_rules (
  prefix      TEXT PRIMARY KEY CHECK (length(prefix) BETWEEN 1 AND 8),
  scheme      TEXT NOT NULL,              -- e.g. 'ean13_weight_g'
  item_id     TEXT REFERENCES items(id) ON DELETE RESTRICT,   -- NULL = prefix selects item from the code
  qty_digits  INTEGER NOT NULL CHECK (qty_digits BETWEEN 1 AND 8),
  qty_unit    TEXT NOT NULL REFERENCES units(unit) ON DELETE RESTRICT,
  check_digit INTEGER NOT NULL DEFAULT 1 CHECK (check_digit IN (0,1))
) STRICT;
```
Store the raw code in `bill_lines.scanned_code` (D10) and the parsed `qty_base`, so the receipt is explainable.
Keep the code as text — leading zeros are significant.

### D24. Reassigning an alias rewrites history — S1

**Scenario.** `item_identifiers` has no validity window; a reassignment is an in-place `UPDATE` with
`updated_at` overwritten. `bill_lines` does not record the code that was scanned.

**Rows.** `T500` → TAMARIND 500 g until 14:00, then → TAMARIND SEEDLESS (₹15 more, 5% tax). A terminal offline
since 13:00 still resolves `T500` to the old item and bills it until 18:00. The bills record the old `item_id`;
reports attribute a day of sales to the wrong SKU and the invoice carries the wrong tax rate. Because the
identifier row was overwritten, no query can explain which mapping was in force — and the only evidence, the
scan, was never stored.

**Fix.** Give identifiers validity: `valid_from INTEGER NOT NULL`, `valid_to INTEGER`,
`CHECK (valid_to IS NULL OR valid_to > valid_from)`, and
`UNIQUE(store_id, kind, alias) WHERE active=1 AND valid_to IS NULL`. Reassignment = close + insert in one
batch, which already emits two `catalog_changes` rows. Add `bill_lines.scanned_code`/`identifier_id`.

### D25. Identifiers are store-only, so a new store opens unable to scan — S2

**Scenario.** `item_identifiers.store_id` is `NOT NULL` and there is no franchise-default row. Prices have a
`NULL` default; identifiers do not.

**Rows.** S9 opens with 5,000 items inherited through `item_prices.store_id IS NULL`. Zero identifier rows
exist for S9; every scan falls through to name search. The cashier picks "TOOR DAL" among five variants and
bills the wrong one; the owner's fix is to fan out 5k×N rows by hand.

**Fix.** Mirror the price pattern: `store_id TEXT NOT NULL DEFAULT ''` (`''` = franchise-wide) with
`UNIQUE(store_id, kind, alias) WHERE active=1`, resolution
`WHERE store_id IN (?, '') AND kind=? AND alias=? AND active=1 ORDER BY store_id='' LIMIT 1` (store row wins).
EAN rows are forced to `''` by D22. This also removes the NULL-in-unique trap for identifiers.

---

## 5. Tenancy leakage

### D26. Nothing stops a bill from naming another store's terminal — S1

**Scenario.** `bills.store_id` and `bills.terminal_id` are separately FK'd. There is no relationship between
them, and no `(store_id, terminal_id)` constraint. `devices` has the same hole.

**Rows.** A device row left over from a re-enrolment says `store_id=S1, terminal_id=T2` (T2 belongs to S2).
Bills push with S1 (from the device row) and display number `T2-000431`. S1's day report gains ₹4,000 that S2
sold; S2's loses it. `uq_bills_store_terminal_seq` is satisfied because `(S1, T2, 431)` is unique. Nothing in
the database says the pair is impossible. The same row shape lets `shifts` and `devices` mix stores.

**Fix.** One index plus composite FKs (verified: a `(S2, T1)` bill is rejected):
```sql
CREATE UNIQUE INDEX uq_terminals_store ON terminals(store_id, id);
-- bills, devices, shifts:
FOREIGN KEY (store_id, terminal_id) REFERENCES terminals(store_id, id) ON DELETE RESTRICT
```
Bind the device too: `FOREIGN KEY (device_id, terminal_id) REFERENCES devices(id, terminal_id)` needs
`UNIQUE(id, terminal_id)` on `devices`; then a bill's device and terminal cannot disagree either.
The practical effect: the Worker can no longer write a bill into the wrong store even if a body field or a
token claim is wrong — the insert fails.

### D27. Device identity is not bound to a terminal, an install, or a unique prefix — S1

**Scenario.** `devices` has no `UNIQUE(install_id)`, no `UNIQUE(terminal_id)`, and `credential_prefix` is a
plain index. The brief's "install_id detects cloned disks" depends on all three. `lease_expires_at`
(DECISIONS D4) answers "how stale is this device's authority"; it does not make the identity unique.

**Rows.** A disk is cloned (or a re-enrolment happens without revoking the old row): two `devices` rows share
`install_id`, both are live, both mint 15-minute tokens, both push bills for the same terminal with overlapping
`local_seq`. The unique `(store, terminal, local_seq)` rejects one bill; because the batch is atomic, a good
bill is refused and parked. Query 11 (`WHERE credential_prefix=?`) can also match two rows; the implementation
compares the first and the second device can never authenticate.

**Fix.**
```sql
CREATE UNIQUE INDEX uq_devices_install ON devices(install_id);
CREATE UNIQUE INDEX uq_devices_prefix  ON devices(credential_prefix);
CREATE UNIQUE INDEX uq_devices_terminal ON devices(terminal_id) WHERE revoked_at IS NULL;
```
Enrolment revokes the previous live device for that terminal in the same batch, and a token mint checks
`revoked_at IS NULL`.

### D28. The audit log and the stock ledger have no store — S2

**Scenario.** `audit_events` has `actor_device_id` but no `store_id`/`terminal_id`.
`inventory_movements` has `bill_id` but no `store_id`, and adjustments have `bill_id NULL`.

**Rows.** An opening-stock adjustment of 50 kg is `inventory_movements(bill_id=NULL, direction='in', item=X)`.
There is no store anywhere in the row: a per-store stock projection cannot include it, and no query can say
which shop's shelf it was. An audit row for "price override approved" can only be scoped by joining
`actor_device_id → devices → store`, which is not the store where the action happened if the device was
re-enrolled into another store, and is no store at all for an admin-plane action.

**Fix.** `audit_events.store_id TEXT NOT NULL REFERENCES stores(id)` and
`inventory_movements.store_id TEXT NOT NULL REFERENCES stores(id)`. For movements, the store is copied from the
bill at write time and deliberately has **no FK to `bills`**: the ledger outlives the bill under retention (D14),
and an immutable copied `store_id` cannot cross stores. Add
`UNIQUE(bill_id, bill_line_no) WHERE bill_id IS NOT NULL` so a replayed push cannot duplicate stock-out rows.

### D29. A cashier can be attributed to a store they never worked at — S2

**Scenario.** `user_stores` exists, but no FK or check connects `bills.user_id` to it.

**Rows.** Ravi works at S1. A mis-set admin UI, or a support script, writes a bill at S2 with `user_id=ravi`.
`bills.user_id` FK passes (the user exists), the store FK passes (the store exists). S2's "sales by cashier"
report credits Ravi; S1's shift reconciliation loses a sale it never had.

**Fix.** `UNIQUE` on `user_stores(user_id, store_id)` already exists as the PK; add
`FOREIGN KEY (user_id, store_id) REFERENCES user_stores(user_id, store_id) ON DELETE RESTRICT` to `bills` and
`shifts`. Add `user_stores.active` because the composite FK otherwise makes a store assignment undeletable
once a bill references it — which is the correct historical semantics, but should be deliberate.

### D30. Deleting a device or a shift silently erases attribution on money rows — S2

**Scenario.** `bills.device_id ON DELETE SET NULL`, `bills.shift_id ON DELETE SET NULL`,
`audit_events.actor_user_id ON DELETE SET NULL`, `sessions.user_id ON DELETE CASCADE`.

**Rows.** A terminal is decommissioned with `DELETE FROM devices`: 40,000 bills lose `device_id`. The cloned-disk
investigation can no longer answer "which device pushed this bill". A departing staff account is deleted: 3,000
audit rows lose their actor and the week's sessions vanish. `bill_voids.voided_by` would inherit the same
problem if it were not RESTRICT.

**Fix.** RESTRICT on every reference to `devices`, `shifts`, `users`; revoke and deactivate instead of delete.
If deletion must exist, it is a purge job that writes an `audit_events` tombstone first.

---

## 6. Attribution

### D31. `auth_method='password'` is an unverifiable claim, and `pin_offline` can name a user with no PIN — S1

**Scenario.** The bill is pushed with a device token. The server has no per-user evidence for the sale; the
only claim is a string on the row. The DDL also ships `pin_hash, pin_salt` to **every** terminal in the store
via the staff roster pull. DECISIONS D7 promises `staff_assertion_id` (online) and `unverified_staff`
(offline); neither column exists in `d1-schema.sql`, so the decision cannot be implemented without the schema
change below.

**Rows.** A cloned disk (D27) bills 200 sales as `auth_method='password', user_id=<owner>` — the owner's
attribution report shows them as online-authenticated, and there is no `session_id` to contradict it. A
4-digit PIN with a known hash is 10,000 candidates; PBKDF2 at 600k iterations is ~1 s each, so a stolen
terminal can recover every cashier's PIN in minutes on a GPU and bill as anyone. And nothing stops
`auth_method='pin_offline'` for a cashier whose `pin_hash IS NULL` — verified that such a bill inserts.

**Fix.** Add `bills.auth_session_id TEXT REFERENCES sessions(id)`,
`CHECK (auth_method <> 'password' OR auth_session_id IS NOT NULL)`, and
`bills.auth_pin_version INTEGER` (the user's `pin_updated_at` at sale). Move the verifier out of the general
`users` pull: a `user_pin_verifiers(user_id, verifier, alg, updated_at)` row delivered only to a terminal after
that user logs in locally, not to every terminal with the roster. Add a trigger (verified) rejecting
`pin_offline` for a user without a PIN:
```sql
CREATE TRIGGER bills_pin_needs_pin BEFORE INSERT ON bills
WHEN NEW.auth_method = 'pin_offline'
BEGIN SELECT RAISE(ABORT,'pin_offline without a PIN on the user')
  WHERE NOT EXISTS (SELECT 1 FROM users u WHERE u.id = NEW.user_id AND u.pin_hash IS NOT NULL); END;
```
The stronger fix (a per-bill MAC keyed by a terminal-held secret) is a protocol change, not a schema review
item; until then, mark `pin_offline` as low-confidence in the attribution view rather than treating it as a
login.

### D32. A discount recorded as a list sale bypasses the override report entirely — S1

**Scenario.** `override_by` is required only when `price_source IN ('override','manual')`. A discount is a
separate column and is not tied to either.

**Rows.** Verified accepted: `price_source='list', list_price_minor=4000, price_minor=4000,
discount_minor=9900, line_total_minor=100, override_by=NULL, policy_violation=0`. A ₹99 discount on a ₹40 item,
recorded as a list sale, invisible to report 10 (which filters on `price_source IN ('override','manual')`), with
no approver and no violation flag. Also: `price_source='manual'` requires `override_by` but not
`override_reason`; `override_reason='other'` with `override_note=NULL` is accepted.

**Fix.**
```sql
CHECK (discount_minor = 0 OR override_by IS NOT NULL),
CHECK (discount_minor <= list_amount_minor),
CHECK (price_source <> 'manual' OR override_reason IS NOT NULL),
CHECK (override_reason <> 'other' OR (override_note IS NOT NULL AND length(trim(override_note)) > 0))
```
and put discounts in report 10's predicate (`price_source IN ('override','manual') OR discount_minor > 0`).

### D33. Approval grants exist but gate nothing, and are not single-use — S1

**Scenario.** `approval_grants` has `max_overrides`, `max_discount_pct`, expiry and revocation. There is no
`used_at`, no `bill_id`/`line_no`, and no link from `bill_lines`, so a grant can be replayed.

**Rows.** A manager approves one ₹100 override at 10:00 (`max_overrides=1`). At 11:00 the same grant id is
attached to 40 lines — nothing counts usage because no usage row exists. The owner's "who approved this"
report can only show the overrides themselves; `bill_lines.override_by` names the *cashier*, not the approver,
so the approver is unrecorded. `user_id ON DELETE CASCADE` then deletes the grant along with the manager,
erasing the only evidence that an approval ever existed.

**Fix.** Add `bill_lines.approval_grant_id TEXT REFERENCES approval_grants(id) ON DELETE RESTRICT`, and on the
grant: `used_at INTEGER`, `used_bill_id TEXT`, `used_line_no INTEGER`,
`UNIQUE(used_bill_id, used_line_no)`, `CHECK (used_at IS NULL OR used_at >= issued_at)`,
`CHECK (used_at IS NULL OR used_at <= expires_at)`. Enforce
`CHECK (policy_violation = 0 OR approval_grant_id IS NOT NULL)` and
`CHECK (discount_minor <= allow_discount ...)` cannot span tables — the cap check is a Worker assertion, but it
needs `list_amount_minor` (D3) and the grant id to exist at all. Change `approval_grants.user_id` to RESTRICT.

### D34. Attribution survives only while the account does — S2

**Scenario.** `users` has no `deleted_at`; `active` exists but nothing prevents `DELETE`. `sessions.user_id` is
`CASCADE`, `audit_events.actor_user_id` is `SET NULL`, `approval_grants.user_id` is `CASCADE`,
`bills.user_id` is RESTRICT (the one that is right).

**Rows.** Deleting Ravi, who billed 412 bills: `bills` blocks it, good. Deleting a *staff* user who only did
admin work: their sessions disappear, their audit rows become anonymous, and the grants they issued are
deleted. The owner's loss-prevention review of last month now has blanks where it previously had names.

**Fix.** Users are never deleted: `active=0` plus `deactivated_at`. RESTRICT on `sessions.user_id`,
`audit_events.actor_user_id`, `approval_grants.user_id`. Purge sessions by `expires_at`, not by user.

### D35. The receipt does not copy the cashier's name — S3

**Scenario.** Bills copy the item name and the price; the cashier is referenced by id only.

**Rows.** Ravi K. is renamed "Ravi Kumar" in the admin. Reprinting an old bill shows the new name beside the
old signature line. A receipt is an attestation of who billed it; the name is history, not a join.

**Fix.** `bills.cashier_name TEXT NOT NULL` copied at insert. Same argument for `voided_by_name` if void
reprints are printed.

---

## 7. Index and constraint gaps

### D36. The override report scans every bill and includes voided ones — S1

**Scenario.** `idx_bills_store_date` is partial on `voided=0`. Query 10 does not filter `voided`.

**Rows.** Verified with 200 bills: query 10 as written → `SCAN b` (full table scan, then a temp B-tree for the
ORDER BY). Adding `AND b.voided=0` → `SEARCH b USING COVERING INDEX idx_bills_store_date`. It is also wrong,
not just slow: overrides on a voided bill remain in the review list, so the owner chases a discount that was
already reversed, and the count of "overrides this month" is inflated by every voided bill.

**Fix.** Add `AND b.voided = 0` to query 10 (and to any report that reads `bills`). If overrides must be
reviewable for voided bills, that is a separate predicate with its own index, not the default.

### D37. Report 9 scans every line ever written, not just the month — S1

**Scenario.** The `IN (SELECT id FROM bills WHERE ...)` shape makes SQLite drive from `bill_lines`. DECISIONS
D10 caps the range (a year is a 413), which bounds the worst case but not a month at 25M lines.

**Rows.** The 200-bill seed (600 lines) produced
`SEARCH bill_lines USING INDEX idx_bill_lines_item (ANY(item_id) AND bill_id=?)` + `LIST SUBQUERY 1` — the line
index is walked in `item_id` order, i.e. **every line in the database**, and each is checked against the month's
bill set. At 25M lines a month report reads the whole table and dies on the 30 s cap. Rewritten as
`JOIN bills b ON b.id=bl.bill_id WHERE b.store_id=? AND b.business_date BETWEEN ? AND ? AND b.voided=0`,
the plan is `SEARCH b USING COVERING INDEX idx_bills_store_date` → `SEARCH bl USING COVERING INDEX
idx_bill_lines_report (bill_id=?)`, which touches only the month.

**Fix.** Rewrite the query with the join, add `voided=0` (D36), and use the multiply-before-divide form (D8).

### D38. The query catalogue documents a pull the schema deliberately does not implement — S2

**Scenario.** The catalogue's pull is a self-contained journal (`payload_json`) pruned against per-terminal
checkpoints. The schema and DECISIONS D1 chose the opposite: a reference journal re-read in batches, with
`catalog_seq.pruned_below` driving a `410 cursor_expired` and a full refresh.

**Rows.** Verified against the DDL: query 1 selects `catalog_changes.payload_json` → `no such column`; query 13
selects `users.tenant_id` → `no such column`; query 14 prunes against `terminal_checkpoints` → `no such table`.
A Worker written from the catalogue fails at deploy, or, "fixed" by adding `payload_json`, duplicates every
document into the journal and contradicts the 90-day prune budget. The chosen design itself is sound: a
500-change page expands to a handful of `IN (...)` reads per entity type, well inside the 1,000-query cap.

**Fix.** Fix the catalogue, not the schema. Query 1 selects `seq, entity, entity_id, op, version`; the Worker
then fetches referenced documents in batches of 100 per entity type (DECISIONS D1). Query 13 drops `tenant_id`.
Query 14 prunes by age and advances the floor in the same batch:
```sql
CREATE INDEX IF NOT EXISTS idx_catalog_changes_changed ON catalog_changes(changed_at);
-- boundary := SELECT MAX(seq) FROM catalog_changes WHERE changed_at < :cutoff;
DELETE FROM catalog_changes WHERE seq <= :boundary;
UPDATE catalog_seq SET pruned_below = :boundary WHERE id = 1 AND :boundary > pruned_below;
```
`GET /v1/catalog?since=X` returns 410 with `reset_to = pruned_below - 1` when `X < pruned_below`; the snapshot is
the existing catalog read with `since = last_seq`. Say explicitly that query 5 is an untargeted
`ON CONFLICT DO NOTHING` followed by `SELECT id ... IN (...)` for the accepted set — a targeted conflict clause
lets a duplicate `local_seq` fail the whole atomic batch.

### D39. Missing uniqueness and NOT NULLs that reports depend on — S2

**Scenario.** A handful of row shapes are accepted that the reports assume cannot exist.

**Rows.**
- Two bills with the same `display_no` — verified accepted (2 rows for `S1-T2-000431`). A local counter reset
  after a restore prints duplicate receipt numbers; the customer's copy cannot be matched to a bill.
- `inventory_movements` has no unique on `(bill_id, bill_line_no)`; a re-pushed bill that inserts movements
  again doubles stock-out.
- `item_prices.created_by` is nullable: a price change with no author.
- `shifts` has no `UNIQUE(terminal_id) WHERE closed_at IS NULL`: two open shifts; a bill's `shift_id` can name
  a shift from another terminal (no composite FK — D26). It also accepts `closed_at` set with `closed_by` NULL
  (nobody closed it) and `closed_by` set with `closed_at` NULL (closed by a ghost).
- `approval_grants.revoked_at` has no `revoked_by`, so a revoked grant cannot say who revoked it.
- `bills.device_id`/`shift_id` are nullable with no rule that a bill created by a device has one; `user_id` is
  correctly NOT NULL.

**Fix.**
```sql
CREATE UNIQUE INDEX uq_bills_display_no ON bills(store_id, display_no);
CREATE UNIQUE INDEX uq_inv_movement_line ON inventory_movements(bill_id, bill_line_no)
  WHERE bill_id IS NOT NULL;
CREATE UNIQUE INDEX uq_shift_open ON shifts(terminal_id) WHERE closed_at IS NULL;
-- shifts: CHECK ((closed_at IS NULL) = (closed_by IS NULL))
-- approval_grants: revoked_by TEXT REFERENCES users(id), CHECK ((revoked_at IS NULL) = (revoked_by IS NULL))
```
`item_prices.created_by` NOT NULL (the seed/provisioning path inserts prices as the owner).

### D40. The unsynced index is on the wrong column — S2

**Scenario.** Query 6 is `SELECT id FROM bills WHERE synced_at IS NULL ORDER BY local_seq LIMIT 100`;
`idx_bills_unsynced` is `ON bills(created_at) WHERE synced_at IS NULL`.

**Rows.** Verified plan: `SCAN bills USING INDEX idx_bills_unsynced` + `USE TEMP B-TREE FOR ORDER BY`. The
index finds the right rows and then the sort throws the order away. A backlog of 5,000 unsynced bills is sorted
on every sync tick; the terminal's outbox is the one queue that must be cheap.

**Fix.** `CREATE INDEX idx_bills_unsynced ON bills(local_seq) WHERE synced_at IS NULL;` — `local_seq` is
monotonic per terminal, which is the order the push wants.

### D41. Two due indexes are absent, and one is dead — S2

**Scenario.** The price lookup and the catalog pull.

**Rows.** Query 3's predicate is `(item_id, qty_base, store scope, valid_from)`; the only validity index is
`(item_id, store_id, valid_from)`, so every lookup scans all tiers of the item (bounded, acceptable) but cannot
serve `qty_base`. Query 3's `store_id IS ?3 OR store_id IS NULL` compiles to `MULTI-INDEX OR` with two index
searches plus a temp B-tree (verified), while the `store_id IN (?, '')` form is a single search — the `''`
sentinel (D43) buys this for free. Conversely `idx_bill_lines_override(override_by, bill_id)` is not used by
report 10, which filters `price_source`. Note 03's `idx_prices_version(version)` is dead under
`catalog_changes` and is correctly absent from the current DDL — do not reintroduce it. None of these are urgent
at 5k items; the report query (D36/D37) is.

**Fix.** If the `''` sentinel is adopted, `CREATE INDEX idx_prices_at ON item_prices(item_id, qty_base, store_id, valid_from)`
and drop the `MULTI-INDEX OR` shape from query 3. Replace `idx_bill_lines_override` with
`(price_source, bill_id) WHERE price_source <> 'list' OR discount_minor > 0` only if report 10 is served from
lines; otherwise leave it out and let `bills` drive.


### D42. The business day a bill lands in is not derivable from the row — S1

**Scenario.** `business_date` is a free `'YYYY-MM-DD'` string. The only validation is DECISIONS D11's Worker rule
("more than one day from the server's date → reject"). A clock 12 hours wrong is inside that window; a
legitimately offline bill older than a day is outside it.

**Rows.**
- Clock 12 h fast: a sale at 23:30 IST on Nov 14, terminal clock reading 11:30 on Nov 15 →
  `business_date=2026-11-15`, `created_at` consistent with the wrong clock. The one-day rule passes. Nov 14 is
  ₹4,000 short, Nov 15 is ₹4,000 long, and at a month end the sale moves GST period. Nothing on the bill proves
  which cutoff produced the date.
- Offline three days: `created_at=2026-11-11`, `received_at=2026-11-14`, `business_date=2026-11-11`. The one-day
  rule rejects a legitimate bill — multi-day offline billing is the product.
- 4 am cutoff: an order at 01:00 IST on Nov 15 belongs to Nov 14. The row stores a date and no cutoff, so no
  query can re-derive whether the terminal applied it.

**Fix.** Store the shift that produced the date, not just the date:
`bills.business_day_shift_minutes INTEGER NOT NULL` (UTC offset minus cutoff: 90 for IST with a 240-minute
cutoff), `stores.utc_offset_minutes INTEGER NOT NULL DEFAULT 330`, and
`CHECK (business_date = date(created_at,'unixepoch','+'||business_day_shift_minutes||' minutes'))`
(verified: 22:00 UTC → Nov 14, 23:00 UTC → Nov 15 for +90). Add
`CHECK (received_at IS NULL OR created_at <= received_at + 300)` for forward skew — that is what catches the
12-hour-fast clock at sync — and drop the "one day from the server" rule: compare to `created_at`, not to today,
so a three-day-old offline bill is accepted.

---

## 8. Migration risk

### D43. The NULL sentinel and the store-only identifier are one-way doors — S2

**Scenario.** `item_prices.store_id IS NULL` is the franchise default. Changing it later to `''` (which is what
makes the unique indexes and the query planner work — the current DDL already wraps the key in `IFNULL(store_id,'')`
for exactly this reason)
means rewriting every price row, every identifier row, every client query and every terminal's local copy in
lockstep.

**Rows.** Two stores, 5k items, 20 tiers: 200k price rows to rewrite in one Cloudflare D1 database (a batch cannot hold that, and D1 has
no interactive transaction) and 8 terminals to resync. The alternative is to keep the `OR ... IS NULL` pattern
forever, which is what defeats the single-index lookup today.

**Fix.** Decide before the first terminal ships: `store_id TEXT NOT NULL DEFAULT ''` on `item_prices` and
`item_identifiers`, `''` = franchise default. The unique indexes drop `IFNULL`, queries become
`store_id IN (?, '')`, and the sentinel is uniform across the two tables (today prices use NULL and identifiers
use NOT NULL — the asymmetry that produced D25). If NULL stays, write it down as permanent.

### D44. Growing CHECK enums force table rebuilds, and one of them is on `bill_lines` — S2

**Scenario.** SQLite has no `ALTER TABLE ... DROP CONSTRAINT` (verified: the statement fails). Every
`CHECK (x IN (...))` is a one-way door; adding a value means create-copy-drop-rename.

**Rows.** `bill_lines.price_source IN ('list','tier','override','manual')` — promotions (BRD-10) add `'promo'`
→ rebuild the largest table in the database, on Cloudflare D1 and on every terminal, with no interactive transaction.
`catalog_changes.entity IN (...)` — every future catalog table (promotions, `item_names`, customers) rebuilds
the change log. `users.role IN ('owner','staff','cashier')` — approvals want `'manager'`. `approval_grants.scope
IN ('approve')` is certain to grow. `item_identifiers.kind` — `'upc'`, `'gs1'` are plausible.

**Fix.** Convert the growth-prone enums to code tables with an FK — `price_sources(code)`,
`catalog_entities(code)`, `roles(code)`, `approval_scopes(code)`, `identifier_kinds(code)`,
`movement_kinds(code)`. Adding a value is then an `INSERT`. Keep CHECKs for values that are structurally fixed
(`price_includes_tax IN (0,1)`, `voided`) and for arithmetic identities, which are forever.

### D45. Positive-only quantities and totals make returns a rebuild — S1 for the next phase

**Scenario.** `bill_lines.qty_base > 0`, `total_minor >= 0`, `line_total_minor >= 0`. BRD-13 returns are
explicitly future work, and the brief's own note 06 says returns need a linked document later.

**Rows.** A customer returns 1 of 3 items. There is no way to express it: a void reverses the whole bill (and
must be re-entered by hand, which is where new errors are born); a negative line is rejected by
`qty_base > 0`; a credit note is rejected by `total_minor >= 0`. Dropping either CHECK later rebuilds `bills`
and `bill_lines`.

**Fix.** Change to `CHECK (qty_base <> 0)` and `CHECK (line_total_minor <> 0 OR ...)`; add now
`bills.doc_type TEXT NOT NULL DEFAULT 'sale' REFERENCES doc_types(code)` with `'sale'` seeded, and
`bills.parent_bill_id TEXT REFERENCES bills(id)`. A return is then a new `doc_type` plus a new code row, and
the sign convention is documented (`sale` positive, `return` negative, `quantity` sign equals `doc_type` sign).
This is the cheapest insurance in the document: two columns and a small table.

### D46. `catalog_changes` cannot describe a new catalog table without a rebuild — S2

**Scenario.** `entity` is a CHECK list. Adding a catalog table (promotions, `item_names`) needs a new value and
therefore a rebuild of the prunable-but-large append-only log.

**Rows.** BRD-10 promotions land as `promotions + promotion_items`; the first `promotions` change row fails the
CHECK. The quick fix is a rebuild on every tenant database; the slow fix is no promotions.

**Fix.** Drop the CHECK or make it `REFERENCES catalog_entities(code)`. The writer already knows the entity
name; the pull endpoint maps it to a document type.

### D47. A single DDL file applied unchanged cannot add a column to a live terminal — S2

**Scenario.** The header says the file is applied by `wrangler d1 migrations apply` **and, unchanged, by the
terminal's Go migration runner**. `CREATE TABLE IF NOT EXISTS` makes a fresh install idempotent; it does nothing
to an existing database.

**Rows.** v1.1 adds `bill_lines.price_qty_base`. The terminal's runner executes the file: the table already
exists, so the `CREATE` is skipped and the column is absent. Every insert now fails, or the new code reads a
column that is not there. `schema_meta.schema_version` is written but nothing branches on it.

**Fix.** Keep one canonical schema for fresh installs and a versioned migration list for existing ones:
`schema_migrations(version INTEGER PRIMARY KEY, applied_at INTEGER NOT NULL, checksum TEXT NOT NULL)`, with
`ALTER TABLE` steps per version and a startup check that `schema_version = max(version)`. `schema_meta` already
exists; give it the history.

### D48. Tenders are additive only if `total_minor` keeps its meaning — S3

**Scenario.** The note-06 plan is "add `bill_tenders`, backfill one row per bill". That is additive and safe —
unless `total_minor`'s meaning changes at the same time, or a CHECK is added that only holds for single-tender
bills.

**Rows.** Today `total_minor` is the amount due. Later a split payment records 5,000 cash + 4,000 UPI; a
`bill_tenders` row per mode sums to `total_minor` — no rewrite of `bills`. But if v1 had defined `total_minor`
as "cash received" (no `paid_minor`, no distinction), the backfill would have to invent one. It does not, and
the money-contract fix in D1 (the header identity) nails the meaning down.

**Fix.** Write the definition into the schema, not the brief: `total_minor` is the amount due for the bill,
independent of how it is settled. No tender columns now. When tenders arrive, they are a child table with
`CHECK (amount_minor >= 0)` and a per-bill sum check in the push endpoint.

---

## 9. Tooling notes (not defects)

- `docs/check-schema.py` warns that `units`, `tax_rates` and `users` have "no cursor-ish column". They are
  catalog tables served by `catalog_changes`, so the warning is a false positive; the heuristic should be
  removed or the warning will train people to ignore it.
- `docs/explain-queries.py` still cannot seed the current DDL: its `store_settings` insert omits `updated_at` and
  uses `rounding_mode='total_nearest_rupee'` (not in the CHECK), its `item_identifiers` insert omits `id` and
  `updated_at`, and its `item_prices` insert omits `created_at`. Every one is wrapped in `INSERT OR IGNORE`, so
  SQLite silently drops the row instead of raising — the price and alias tables are empty while the tool prints
  "OK". It also runs with foreign keys **off**, so bills for `'str_1'`/`'trm_1'`/`'usr_1'` (none of which
  exist) are accepted. Fix the seed before trusting any plan it prints; the plans in this review come from a
  seed built with `PRAGMA foreign_keys=ON`.
- `EXPLAIN QUERY PLAN` is the acceptance test the README asks for. Two plans in this review depend on a
  predicate being present (`voided=0`, D36) and one on a join shape (D37); both are cheap to assert in CI
  against a seeded database.

---

## Top five fixes

1. **Make money self-describing and arithmetically closed.** Copy `price_includes_tax` and `hsn` onto
   `bill_lines`; add `taxable_minor`, `list_amount_minor`, `price_qty_base`, `igst_minor`; add
   `subtotal_minor`/`tax_minor` to `bills`; enforce
   `line_total = taxable + cgst + sgst + cess`, `line_total = list_amount - discount`,
   `cgst = sgst`, and `total = subtotal + tax + rounding`. Fixes D1–D5, D9 and the arithmetic behind D8.
   Without it, the same bill means different things to the report, the invoice and the reprint.

2. **Add the composite tenancy key.** `UNIQUE(store_id, id)` on `terminals` and
   `FOREIGN KEY (store_id, terminal_id) REFERENCES terminals(store_id, id)` on `bills`, `devices` and `shifts`,
   plus `UNIQUE(install_id)`, `UNIQUE(credential_prefix)`, and one live device per terminal. Fixes D26–D28 and D30.
   A cross-store bill is silent, unrecoverable, and the one defect that makes the owner distrust every report.

3. **Make the price history coherent and the line self-describing.** One revision per `(item, scope)` via the
   trigger in D16, `price_store_id` and `price_qty_base` on the line, and a stale check that verifies a
   revision rather than guessing at a tier by sold quantity. Fixes D16–D20. Today catalogue query 15 returns
   zero rows for every tiered sale (verified) — the diagnostic is decorative.

4. **Make voids and immutability real.** `bill_voids` as the append-only event with its own idempotent push,
   `voided_by`/`reason` NOT NULL, the freeze triggers, server-only fields out of `bills`, and `invoice_no`
   assigned before insert from the counter row. Fixes D11–D13. A void that never reaches the server is a
   permanent, unexplained gap between the shop's report and the cloud's.

5. **Fix identifier resolution before the first scan.** A global `UNIQUE(alias) WHERE kind='ean'`, `qty_source`
   plus `barcode_rules`, and validity on identifier rows (D21–D24). A weight barcode that bills 500 g for a
   1234 g tray loses ₹293 in one scan, and a reassigned alias sells the wrong item all afternoon; neither shows
   up in a report, because qty and money agree.
