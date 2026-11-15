# Parity audit — `catalog` slice

Traasu design (no code) vs GoFrugal RetailEasy RPOS7. Every "defect" below has a reproduction against the
real DDL: `docs/parity/repro/`repro_catalog.py` (in-memory sqlite3, 0001 +
0002–0008 applied in order, `PRAGMA foreign_keys=ON`). Full output:
`docs/parity/repro/`out.txt`. Script sections are cited as `C1`…`C19`.

Repo checks re-run before writing (unmodified): `check-schema.py`, `check-invariants.py`, `explain-queries.py`
all pass.

## 1. Use-case table

| # | Use case | GoFrugal (page) | Traasu (D#, file) | Verdict | Sev |
|---|---|---|---|---|---|
| 1 | Units: dimension, base unit, sales unit, qty decimals | `Item-Master-Configuration` (Is Weighable / Sell By weights-units); `FAQs-Weighing-Scale-Barcode` #14 ("Decimals Required (Qty) = 3") | `items.dimension/base_unit/default_sales_unit` (`d1-schema.sql:203`); `units` (`:53`); D27, D29 | **defect** | High |
| 2 | Base unit frozen once the item has sold | Item master field (implicitly immutable in practice) | `trg_items_version` (`0008:225`) + prose "freeze once a bill line references the item" (`d1-schema.md` items row) | **defect** | High |
| 3 | HSN per item, carried onto the bill | `Tax-based-on-Item-Master` (Tax tab) | `items.hsn` (`:216`); `bill_lines.hsn` (`0002:34`) | match | — |
| 4 | Per-item tax treatment: rate, inclusive vs exclusive | `Tax-based-on-Item-Master` ("Tax part of MRP [Inclusive]") | `items.tax_rate_id`, `items.price_includes_tax`; `bill_lines.tax_rate_bp/price_includes_tax` (`0002:32`) | match | — |
| 5 | Tax-rate master history | `Tax-based-on-Item-Master`, `GST-Reports` | `tax_rates` (`d1-schema.sql:61`) — one mutable row, no validity | **risk** | High |
| 6 | MRP: item vs pack vs batch; price above MRP | `community-batch-and-expiry` (item-level "selling price, MRP", batch MRP at purchase); `Margin-Settings-for-the-imported-items` | `items.mrp_minor`, `item_prices.mrp_minor`, `item_batches.mrp_minor`; D27 ("Indian law forbids charging above the printed MRP") | **defect** | High |
| 7 | Pricing modes (`fixed_only`/`tiered`/`linear`) vs GoFrugal price levels / markup / datewise | `Price-Level`, `MarkUp-MarkDown`, `Datewise-Pricing` | `items.pricing_mode` (`:206`); D26; `query-catalogue` §3–§4 | **risk** | Medium |
| 8 | Item aliases: alias / SKU / EAN / PLU, per store | `FAQs-Weighing-Scale-Barcode` #9 ("Company Alias Code"); `Barcode-Profile-Configuration` | `item_identifiers` (`:241`); D9; `query-catalogue` §2 | match | — |
| 9 | Identifier format validation (GTIN check digit, PLU length) | codes generated from a profile, not typed (`Barcode-Profile-Configuration`) | `item_identifiers.alias` (`:244`) | **risk** | Medium |
| 10 | One EAN means one product across the chain | — (per-company item code) | `uq_item_identifiers_alias` is per store (`:432`) | **risk** | Medium |
| 11 | Deactivating an item retires its barcodes | `Editing-the-Item-Names` / item master | `DELETE /v1/admin/items/:id` "deactivates identifiers" (`api-routes.md:119`) — Worker rule only | **defect** | Medium |
| 12 | Weighing-scale barcode: prefix + item code + embedded weight + suffix | `Weighing-Scale-Barcode`, `Barcode-Profile-Configuration`, `Item-Master-Configuration`, `FAQs-Weighing-Scale-Barcode` | `items.scale_qty`; `item_identifiers` (whole-string alias + fixed `qty_base`) | **gap** | High |
| 13 | Countable scale labels (Ut-prefix + qty) | `FAQs-Weighing-Scale-Barcode` #10, #16 | same as 12 | **gap** | High |
| 14 | Price printed on the label ("rate of the item") | `Weighing-Scale-Barcode`; `FAQs` #02 | `bill_lines.price_source` (`:330`) | **defect** | Medium |
| 15 | Export the barcode file the scale prints from | `Exporting-the-barcode-file`; `Weighing-Scale-Configuration` (format, delimiter, EOL, caption, path) | no route; data present (`item_identifiers`, `item_prices`) | **gap** | Medium |
| 16 | Catalog delta by change log, cheap monotonic cursor | — | D1; `catalog_changes` (`:259`); `api-routes.md` §4 | match | — |
| 17 | Concurrent admin edits on one item | — | `trg_items_version` (`0008:225`) | match | — |
| 18 | Price revision atomicity, one open tier set | — | `trg_item_prices_version_scope`, `..._revision_coherent` (`0008:376,386`) | match | — |
| 19 | Dated price window with automatic revert | `Datewise-Pricing` | D26 + `PUT /v1/admin/items/:id/prices` (`api-routes.md:120`) | **defect** | High |
| 20 | Closing a price set ("empty `prices[]`") | — | `api-routes.md:120`; `uq_item_prices_open` (`0008:368`) | **defect** | High |
| 21 | `item_price` delta payload size | — | `api-routes.md` §4 ("full current price set … all scopes and versions") | **risk** | Medium |
| 22 | Identifier-set replace vs concurrent edit | — | `PUT …/identifiers` has no `base_version` (`api-routes.md:121`) | **risk** | Medium |
| 23 | Price history cannot be rewritten | `community-change-selling-28-10-2025` (edits batch price/MRP in place) | `trg_item_prices_frozen` (`0005:13`) freezes UPDATE; no DELETE guard | **risk** | Low |
| 24 | 410 / prune floor / staged snapshot | — | `api-routes.md` §4; `devices.last_catalog_seq` (`0008:190`) | match | — |
| 25 | Chunked catalog re-seed visibility | — | `catalog_rebuild` (`0008:202`) | **risk** | Low |
| 26 | Terminal persists its catalog cursor | — | `api-routes.md:637,640` read/write `sync_state` — **table not in the DDL** | **defect** | Medium |
| 27 | Item import from a GoFrugal export (mapping table) | `G-Data-Import`, `Default-Master-Import`, `Margin-Settings-for-the-imported-items` | D30 "Item loading from a GoFrugal export" (`DECISIONS.md:722`) | **defect** | High |
| 28 | Decimal prices from an export | `G-Data` (DPL 17.10, ₹1946.00) | money = integer paise; `STRICT` tables | **risk** | Medium |
| 29 | Tax-inclusive rates on import | `Margin-Settings` (MRP = DPL + margin + VAT) | `items.price_includes_tax` | **risk** | Medium |
| 30 | MRP 0 / blank MRP on import | `FAQs-G-Data-Import` #10 ("items will have the dealer price and the MRP by default") | `items.mrp_minor CHECK >= 0`, `item_batches.mrp_minor` nullable | **risk** | Medium |
| 31 | Missing HSN on an imported item | `GST-Reports` | `items.hsn` nullable, no import rule | **gap** | Low |
| 32 | Category / brand / colour grouping | `MarkUp-MarkDown` (categorywise), `Reports`, `Datewise-Pricing` (BRAND/COLOUR) | no category anywhere on `items` | **gap** | Low |
| 33 | Multi-language item names | `Unicode-Editor`, `Editing-the-Item-Names`, `Creating-a-Language` | `items.name_local` (one, franchise-wide); not copied to `bill_lines` | **defect** | Medium |
| 34 | Item search by name at the register | item LOV / pick list | `items(name)` unindexed (`explain-queries` plan) | **risk** | Medium |

## 2. Findings

### F1 — `base_unit` and `dimension` are three independent FKs (defect, High)

**Scenario.** `items.dimension`, `items.base_unit` and `items.default_sales_unit` each point at `units` alone.
Nothing ties them to each other, and nothing requires `base_unit` to be the group's factor-1 unit. RICE
authored as `dimension='mass'`, `base_unit='kg'` (the admin picks the display unit) stores quantities in kg
while every conversion in D27/D29/D30 assumes base units. A child item with `content_qty_base = 1000`
("1 kg pack") then takes **1000 kg** of the parent per pack.

**Repro** (`C1`):

```
mass item, base_unit=pcs        : ACCEPTED
mass item, base_unit=kg         : ACCEPTED
mass item, sales_unit=ml        : ACCEPTED
count item, base_unit=g         : ACCEPTED
  child insert: ACCEPTED
  -> stock taken per pack: 1000 kg
```

This is schema review D6, listed "Open — deferred" in `TRIAGE.md` §F; 0008 does not fix it.

**Fix.** A trigger, because adding a composite FK now would mean rebuilding `items` (SQLite cannot
`ALTER TABLE … ADD CONSTRAINT`) with its dependants (`item_prices`, `item_identifiers`, `bill_lines`) pointing
at it: on INSERT and on `UPDATE OF base_unit, default_sales_unit, dimension`, require
`EXISTS (SELECT 1 FROM units u WHERE u.unit = NEW.base_unit AND u.factor_to_base = 1 AND u.unit_group = NEW.dimension)`
and that `default_sales_unit` has the same `unit_group` as `base_unit`. The composite-FK form the review
proposed (`FOREIGN KEY (base_unit, dimension) REFERENCES units(unit, unit_group)`, plus a unique index on
`units(unit, unit_group)`) is the tidier version if `items` is recreated in 0001 while it is still empty.
**Cost.** ~6 lines of trigger DDL, no rebuild. Risk: existing seed rows pass.

### F2 — `base_unit` can be changed after bills exist (defect, High)

**Scenario.** `d1-schema.md` says `dimension`, `pricing_mode` and `base_unit` "freeze once a bill line
references the item (Worker rule)". It is not a rule anywhere in the DDL: `trg_items_version` only demands a
version bump, and it *lists* `base_unit` among the fields whose change requires one. So the flip is legal with
`version + 1`, and `sales_daily.qty_base` then sums grams and kilograms in one column forever. `qty_base` is
an INTEGER, so after flipping g→kg the register cannot sell 1.5 kg at all (it rounds to 2).

**Repro** (`C2`):

```
bill 1, 1500 g: ACCEPTED
admin flips base_unit g->kg at version 2: ACCEPTED
bill 2, '2' base units: ACCEPTED
sales_daily qty_base for the item: [(1502,)]
```

**Fix.** One trigger: `BEFORE UPDATE OF base_unit, dimension, pricing_mode ON items
WHEN EXISTS (SELECT 1 FROM bill_lines WHERE item_id = NEW.id) → RAISE(ABORT,'item_unit_frozen')`. A wrong
unit then needs a new item, which is the honest repair (the old lines keep their meaning).
**Cost.** 5 lines. It also removes the need for the prose rule in `d1-schema.md`.

### F3 — MRP lives in three places, none of them enforced against price (defect, High)

**Scenario.** GoFrugal keeps an item MRP *and* a batch MRP (`community-batch-and-expiry`), so Traasu
mirroring both is right. What is missing is any statement of precedence and any check that the charged price
is not above MRP. D27 itself states the legal premise ("Indian law forbids charging above the printed MRP"),
so the design contradicts its own constraint. The import path (F10) makes it likely: a G-Data style export
carries MRP 0 or blank for loose goods.

**Repro** (`C3`):

```
set items.mrp_minor=5000 (v2)  : ACCEPTED
item mrp 5000, tier price 6000: ACCEPTED      <- price above the printed MRP
tier price 4000, tier mrp 3000: ACCEPTED      <- tier price above its own MRP
batch mrp 5000, batch price 6000: ACCEPTED    <- batch price above batch MRP
item mrp_minor = 0 (import of a blank GoFrugal MRP): ACCEPTED
what the three MRPs say for m1 now:
  items.mrp_minor   : 5000
  tier 1 mrp/price  : ('NULL', 6000)
  m3 tier mrp/price : (3000, 4000)
```

**Fix.** Three parts, all cheap and pre-code:
1. `CHECK` on `item_prices` and `item_batches`: `mrp_minor IS NULL OR mrp_minor >= price_minor`; and
   `items`: `mrp_minor IS NULL OR mrp_minor > 0` (0 is not an MRP; blank means NULL).
2. One sentence in D27/D26: for a batched item the **batch** MRP is authoritative and `items.mrp_minor` is
   ignored; for a non-batched item the **tier** `item_prices.mrp_minor` wins, `items.mrp_minor` is the
   fallback. Copy the winner onto `bill_lines.mrp_minor` (already a column).
3. `policy_violation` also set when `price_minor > mrp_minor` on push (it is already the "flag, never reject"
   channel).
**Cost.** 3 CHECKs + one rule + one Worker branch. Turning a CHECK into a table rebuild later is the
expensive version; now it is free.

### F4 — A dated price window cannot be created by any route (defect, High)

**Scenario.** GoFrugal's `Datewise-Pricing` sets a price for a date range that reverts by itself — the normal
workflow for vegetables and festival offers in an Indian grocery. Traasu's SCD2 can *store* the window, but
only in one write order, and no route can express it: `PUT /v1/admin/items/:id/prices` takes
`{store_key, batch_id, base_version, prices[]}` with no `valid_from`/`valid_to`, and D26 fixes `valid_from` to
the server clock. `trg_item_prices_revision_coherent` also refuses the natural order (add the future row
while the current row is open).

**Repro** (`C4`):

```
current price v7 open from 1000 : ACCEPTED
promo v8 3500 for [2000,3000)  : error: price_revision_mixed
  the only legal order: schedule the whole future, then close nothing twice
  1. close v7 at the promo start : ACCEPTED
  2. promo v8 [2000,3000)        : ACCEPTED
  3. post-promo v9 from 3000     : ACCEPTED
  price at t=1500 (today, v7)    : [(4000, 7)]
  price at t=2500 (promo, v8)    : [(3500, 8)]
  price at t=3500 (revert, v9)   : [(4000, 9)]
```

So the read path (`query-catalogue` §3) already handles windows correctly; the write path cannot produce
them, and a terminal that is offline when a promo starts keeps billing the old price.

A second-order defect in the same place: D26's push table says a line is clean when its version is "Found,
and open at `created_at`". A promo row is *closed* by construction (`valid_to = promo end`), so a correctly
priced promo line would be flagged `stale_price`. The check must be `valid_from <= created_at < valid_to`,
never `valid_to IS NULL`.

**Fix.**
1. Add optional `valid_from`/`valid_to` to the prices PUT (both absent = "now", current behaviour), and let
   one call write the current set, the promo window and the post-promo set — the Worker keeps allocating
   `MAX(version)+1` per revision.
2. Relax `trg_item_prices_revision_coherent` to `WHEN NEW.valid_to IS NULL AND EXISTS (…)`: the coherence
   rule is about *open* rows; a closed future row is a schedule, not a competing current price.
3. State the D26 lookup as a window test, not an openness test.
**Cost.** One route field pair, one `WHEN` clause, one sentence. Without it, seasonal pricing stays a manual
6 a.m. admin edit that offline terminals miss.

### F5 — Closing the last price set leaves an active item unpriced (defect, High)

**Scenario.** The route documents "an empty `prices[]` closes that set" as a supported call. The item stays
`active = 1` and the register's price query returns nothing: either the sale is blocked at the counter or the
register prices it at 0.

**Repro** (`C5`):

```
close every open row of m1  : ACCEPTED
rows for m1 that are valid at t=1500: []
m1 still active: [(1, 2)]
```

**Fix.** Smallest: the Worker sets `items.active = 0` (with the version bump) in the same batch when a PUT
empties the last scope for an item; and the register treats "no price" as a hard "call the manager", never as
0. A DDL guard is possible but ugly (a trigger on `item_prices` UPDATE would have to consult `items` and raise
on the last close) — the route is the cheaper place.
**Cost.** One Worker branch + one register rule. Add a `check-invariants` case that an empty set cannot leave
an active item.

### F6 — `tax_rates` has no history, so a rate change is unverifiable (risk, High)

**Scenario.** A GST rate change is a real annual event. `tax_rates` is one mutable row; `UPDATE … SET
rate_bp` is accepted and the old rate is gone. A terminal offline across the change bills at the old rate; the
line copies `tax_rate_bp`, and the server has nothing to compare it against. Compare with `stale_price`,
which exists for exactly this pattern — tax is the one money field with no equivalent flag.

**Repro** (`C9`):

```
columns : ['id', 'name', 'rate_bp', 'active']
UPDATE GST 5% -> 12%: ACCEPTED
row now              : [('GST 5%', 1200)]
catalog_changes row for a tax_rate carries version: [('tax_rate', None)]
```

**Fix.** Keep `tax_rates` as the identity (it is an FK target from `items`) and add
`tax_rate_history(id, rate_bp, valid_from, valid_to)` written whenever `rate_bp` changes, plus a `stale_tax`
flag on the line computed at push: `tax_rate_bp` ≠ the rate valid at `bills.created_at`.
`bill_lines.tax_rate_bp` already holds the claim.
**Cost.** One small table + one trigger or one Worker write + one flag column (`stale_tax INTEGER NOT NULL
DEFAULT 0`, additive). Cheaper now than after the first rate change, because the history for past bills can
never be reconstructed.

### F7 — `price_source` has no value for a label-priced line, and it is a closed enum (defect, Medium)

**Scenario.** A weighing-scale label carries the rate (`Weighing-Scale-Barcode`: "the details viz, barcode,
weight and rate"). Traasu prices from the local catalog (D26), so a label price can only be recorded as
`manual`, which forces `override_by` and an override reason — every scale-priced line then pollutes the
override report. There is no `'label'` value, and `price_source` is a `CHECK` on `bill_lines`, so adding one
later means rebuilding an immutable money table. D25 promised "no new CHECK vocabularies on `bills`,
`bill_lines`, `catalog_changes`" — this one was missed.

**Repro** (`C8`):

```
price_source='label'                  : error: CHECK constraint failed: price_source IN ('list', 'tier', 'override', 'manual')
price_source='manual', no override_by : error: CHECK constraint failed: price_source NOT IN ('override', 'manual') OR override_by IS NOT NULL
price_source='manual', override_by=u1 : ACCEPTED
```

**Fix.** Add `'label'` to the CHECK now, and exempt it from the `override_by` requirement if the label is the
price authority (or keep `override_by` as "the cashier who accepted the label"). Decide the policy: a label
price above MRP is itself a legal problem, so the push should flag it.
**Cost.** One word in a CHECK, free pre-code. This is the last cheap moment.

### F8 — Weighing-scale barcodes have no parse model (gap, High)

**Scenario.** GoFrugal's scale label is `prefix + item code + weight + suffix` (`AA00100450WT`), configurable
per item through a **barcode profile** (order, max length, precision per field —
`Barcode-Profile-Configuration`; mapped per item in the Packing tab — `Item-Master-Configuration`). Weight is
variable, so one label per pack weight. Traasu's identifier model is a whole-string equality hit with a fixed
`qty_base` (`query-catalogue` §2): the 450 g label can be stored as a literal alias, but 475 g cannot, and
nothing in the schema records prefix, field lengths, precision or suffix.

**Repro** (`C7`):

```
label AA00100450WT as alias : ACCEPTED
label AA00100475WT as alias : ACCEPTED
scan of the 450 g label     : [('sc', 450)]
scan of a 460 g label       : []
columns available to parse a label: ['id','store_id','alias','kind','item_id','qty_base','active','updated_at']
```

Is it a v1 non-goal? No: `items.scale_qty` exists (DESIGN-BRIEF lists it in the domain shapes), so the feature
is in scope with no way to implement it.

**Fix.** One additive table plus one item column:

```sql
CREATE TABLE item_scale_barcodes (
  item_id     TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  store_id    TEXT NOT NULL DEFAULT '',        -- '' = every store
  prefix      TEXT NOT NULL DEFAULT '',        -- 'AA'
  code_len    INTEGER NOT NULL CHECK (code_len BETWEEN 1 AND 12),
  weight_len  INTEGER NOT NULL CHECK (weight_len BETWEEN 1 AND 7),
  weight_dec  INTEGER NOT NULL CHECK (weight_dec BETWEEN 0 AND 3),
  suffix      TEXT NOT NULL DEFAULT '',        -- 'WT'
  qty_unit    TEXT NOT NULL DEFAULT 'weight' CHECK (qty_unit IN ('weight','count')),
  priced      INTEGER NOT NULL DEFAULT 0 CHECK (priced IN (0,1)),
  version     INTEGER NOT NULL DEFAULT 1,
  updated_at  INTEGER NOT NULL,
  PRIMARY KEY (item_id, store_id)
) STRICT, WITHOUT ROWID;
```
Parse rule (one place, Go + TS): a scan first tries the exact identifier lookup (§2, unchanged, so EAN/PLU
keep working), then the `(prefix, total length, suffix)` profiles for the store; the embedded field is
`weight_len` digits with `weight_dec` implied decimals → `qty_base` in the item's base unit (`qty_unit='count'`
means pieces). If `priced = 1` the label also carries the rate → `price_source='label'` (F7). Solves
countable-item labels (`Ut` prefix + qty) with the same table, `qty_unit='count'`.
**Cost.** One table, one parse function, one `catalog_changes` entity… which the closed `entity` CHECK does
not have (F13) — so either add `'item_scale_barcode'` to it now or store the profile as columns on `items`
(no new entity, no new change row type). The `items`-column variant is cheaper: six nullable columns, and
`trg_items_version` already covers the replay rule.

### F9 — No route exports the barcode file the scale reads (gap, Medium)

**Scenario.** GoFrugal writes a file the scale picks up: `Inventory >> Barcode >> Item Barcode Printing`,
`Shift+F3`, with the file format, delimiter, EOL and caption configured in `Weighing-Scale-Configuration`
(csv/xls/txt, `,`/tab, Cr/Cr+Lf/Lf, "save with field caption", output path). Traasu has every datum
(`item_identifiers.alias`, `item_prices.price_minor`, `items.mrp_minor`) and no export. Without it a shop with
a weighing scale cannot label anything, which is how produce is sold.
**Fix.** `GET /v1/admin/scale-export?store_id=` returning rows `{barcode, item_code, rate, qty, mrp}`, plus
`store_settings.scale_format/delimiter/eol/caption/path` (nullable, additive). The register app writes the
file to the configured path. **Cost.** One read route, five nullable columns.

### F10 — The import has no source key, so a re-import duplicates the catalog (defect, High)

**Scenario.** D30's mapping table ends with "Import order: parents and bulks, then children and repack items,
then the source rows" — and every reference (`parent_item_id = the parent's code`) is a *code*, which `items`
does not store. `items` has no `external_code`/`source_key`; the id is a UUIDv7 that nothing derives from the
GoFrugal item code. A second import run therefore creates a second copy of every item, and an incremental run
cannot resolve "the parent I imported last month" to a UUID at all.

**Repro** (`C12`):

```
items columns: ['id','name','name_local','dimension','pricing_mode','base_unit','default_sales_unit',
 'tax_rate_id','hsn','price_includes_tax','mrp_minor','allow_discount_pct','restrict_price_edit',
 'prompt_qty','scale_qty','active','updated_at','version','uses_batches','parent_item_id','content_qty_base']
two 'TATA SALT 1KG' rows: ACCEPTED
                        : ACCEPTED
rows with that name: [(2,)]
```

**Fix.** `ALTER TABLE items ADD COLUMN external_code TEXT;` plus
`CREATE UNIQUE INDEX uq_items_external_code ON items(external_code) WHERE external_code IS NOT NULL;`. The POST item
route then becomes an upsert by `external_code` (or returns `409 item_exists` with the existing id, which the
import transform maps to "already imported"). One sentence in D30: the transform sends the GoFrugal item code
as `external_code`.
**Cost.** One nullable column, one partial unique index, one route branch. This is the difference between an
import that can be re-run and one that cannot.

### F11 — The terminal's cursor table is not in the DDL (defect, Medium)

**Scenario.** `api-routes.md` §4 gives the exact checkpoint statements against `sync_state(key, value)` on the
terminal's local SQLite, and the design says the terminal runs the same DDL as D1. No migration creates
`sync_state`.

**Repro** (`C10`):

```
api-routes.md:637 reads sync_state; table exists? []
```

**Fix.** Add `CREATE TABLE sync_state (key TEXT PRIMARY KEY, value TEXT NOT NULL) STRICT, WITHOUT ROWID;` to
the DDL (the terminal needs the table; D1 can have it too, it is one empty table).
**Cost.** 3 lines. Without it the checkpoint contract cannot be implemented as written.

### F12 — `item_price` delta re-sends the whole price history of the item (risk, Medium)

**Scenario.** The catalog pull sends "the item's full price set … all scopes and versions" for every
`item_price` change row, and `item_prices` has no `changed_at`, so the payload cannot be narrowed by time —
only by validity. No route and no prune job deletes price rows (0005 freezes UPDATE; the only DELETE in the
system is the terminal's own replace-set apply), so the history grows with every revision, and dated pricing
(F4) would make that daily.

**Repro** (`C13`):

```
400 daily revisions x 3 tiers = 1200 rows; one item_price document = 138377 bytes
20 such items edited in one page = 2767540 bytes (page cap is 8 MB; limit 500 rows)
   rows that are actually usable at a later t (open + future windows): 3
```

The page stays under 8 MB only because the byte bound cuts it, so a bulk price update becomes dozens of
round-trips that each re-download the same history.
**Fix.** Send rows where `valid_to IS NULL OR valid_to > server_now` (the terminal only prices *new* lines; the
promo row is a closed row in the future, so it survives the filter). One `WHERE` clause in the document
query; the terminal's replace-set apply already tolerates a shrinking set.
**Cost.** One query, no DDL.

### F13 — `catalog_changes.entity` is a closed vocabulary (risk following F8, Low)

**Repro** (`C17`):

```
insert entity='category': error: CHECK constraint failed: entity IN ('item','item_price','item_identifier',
  'item_batch','book','unit','tax_rate','user','store','store_settings','numbering_policy','terminal_config')
```

D25 explicitly ruled "no new CHECK vocabularies on … `catalog_changes`; the Worker validates" — the CHECK is
there anyway. Any new synced entity (a scale-barcode profile per F8, tax-rate history per F6, a category per
F14) requires rebuilding the hottest append table. A rebuild preserves `seq` values if written as
`INSERT INTO new SELECT * FROM old`, so cursors survive, but it is a stop-the-world migration on the table
every terminal pulls from.
**Fix.** Drop the entity CHECK now (pre-code) and validate in the Worker, as D25 intended; or add
`'item_scale_barcode'` and `'tax_rate_history'` while the table is empty.
**Cost.** One line, free now.

### F14 — Identifier writes can lose a barcode silently (risk, Medium)

**Scenario.** `PUT /v1/admin/items/:id/identifiers` "replace one store's identifier set" with no
`base_version`/`If-Match` (the price route has `base_version` and answers `409 version_conflict`; this one
does not). Two admins — one adding the new EAN, one replacing the set from a page loaded a minute earlier —
and the second write deletes the first's barcode with no conflict. The register then cannot scan that product:
the sale is blocked at the counter with no trace of why.
**Fix.** Either accept `base_version` (the max `updated_at` of the store's rows, or a new
`items.identifiers_version`) and answer `409 identifier_conflict`, or make the route per-identifier
`POST`/`DELETE` so two writes touch different rows. **Cost.** Route shape only; the per-identifier variant
needs no DDL.

### F15 — A deactivated item keeps its active barcode (defect, Medium)

**Scenario.** The item DELETE route is a soft delete and "deactivates identifiers" as a Worker rule. If any
path deactivates an item without that step (PATCH `active:false` is on the same route family and is listed as
a plain field), the identifier lookup still resolves it: `query-catalogue` §2 filters only
`item_identifiers.active = 1`, never `items.active`.

**Repro** (`C19`):

```
deactivate m1 (v3)          : ACCEPTED
m1 aliases still active     : [('8901234567890', 1)]
scan still resolves m1      : [('m1', '8901234567890', 'Soap', 0)]
```

**Fix.** A trigger on `item_identifiers` INSERT and `UPDATE OF active, item_id`:
`WHEN NEW.active = 1 AND NOT EXISTS (SELECT 1 FROM items WHERE id = NEW.item_id AND active = 1) →
RAISE(ABORT,'identifier_item_inactive')`. Keep the Worker's bulk deactivation for the catalog rows (a trigger
cannot write `catalog_changes`). **Cost.** One trigger.

### F16 — One EAN can mean two products in a chain (risk, Medium)

**Repro** (`C7`):

```
item A at s1: ACCEPTED      (ean 8901234567890)
item B at s2: ACCEPTED      (ean 8901234567890)
same store, two items (correctly refused): error: UNIQUE constraint failed: item_identifiers.store_id, item_identifiers.alias
```

Per-store uniqueness is deliberate (D9) and correct for local PLUs, but a GTIN is global: after a store-to-
store transfer or a shared label, the same barcode rings up two different products. **Fix.** A trigger
`WHEN NEW.kind = 'ean' AND NEW.active = 1 AND EXISTS (SELECT 1 FROM item_identifiers x WHERE x.alias = NEW.alias
AND x.kind = 'ean' AND x.active = 1 AND x.item_id <> NEW.item_id) → RAISE(ABORT,'ean_item_conflict')` — the
same item may hold the EAN in several stores, two items may not. **Cost.** One trigger, and the import
(F10) then fails loudly on a duplicate instead of importing it.

### F17 — Identifier format is unvalidated (risk, Medium)

**Repro** (`C18`):

```
ean with 3 characters      : ACCEPTED
ean with a bad check digit : ACCEPTED
plu with 12 digits         : ACCEPTED
alias ' A ' (untrimmed)    : error: CHECK constraint failed: alias = upper(alias) AND alias = trim(alias) …
```

A mistyped barcode scans nothing (annoying) or another product (money). GoFrugal avoids this class by
*generating* the code from a profile rather than typing it. **Fix.** A `CHECK` per kind is not expressible in
one row-CHECK without `GLOB` gymnastics; a trigger is: `kind='ean'` → 8/12/13 digits + a valid GTIN check digit
(needs a function, so a Worker validation is acceptable), `kind='plu'` → 1–5 digits. **Cost.** Worker
validation on the identifiers route, no DDL; the trigger is optional.

### F18 — `pricing_mode` has no defined meaning in the read path (risk, Medium)

**Scenario.** `fixed_only`, `tiered`, `linear` are three words with no rule attached: nothing prevents a
`fixed_only` item from holding four tiers, and nothing says what `linear` prices *from*. `query-catalogue` §4
"decomposes" a quantity from the tier rows regardless of mode, so the mode is decorative — and a
`fixed_only` item that accidentally has two tiers prices differently from what the label says. GoFrugal's
equivalent (`Price-Level`, `MarkUp-MarkDown`) always names the basis explicitly.
**Fix.** One paragraph: `fixed_only` = exactly one tier (trigger: no second `qty_base` in an open set);
`tiered` = the tier ladder `query-catalogue` §4 already implements; `linear` = one tier at `qty_base = 1`
only, price × qty. Enforce the two shapes with one trigger on `item_prices` INSERT. **Cost.** One rule + one
trigger.

### F19 — One regional name, franchise-wide, never on the bill (defect, Medium)

**Scenario.** GoFrugal's Unicode Editor keeps a regional name per item, per language, and prints it on the
bill (`Editing-the-Item-Names`, `Creating-a-Language`). Traasu has one `items.name_local` for the whole
franchise and `bill_lines` copies `item_name` only. So (a) a Telugu store and a Tamil store cannot have
different regional names for the same item, and (b) a receipt reprinted after an item is renamed or
deactivated loses the regional name the customer saw.
**Fix.** Add `bill_lines.item_name_local TEXT` (additive, one line, same argument as `hsn` in 0002) and state
which language `name_local` is in (`store_settings.name_local_lang`). A per-language table
(`item_names(item_id, lang, name)`) is the real answer but needs a new catalog entity (F13); v1 can ship one
local name per item and one per store setting.
**Cost.** One column + one `store_settings` column.

### F20 — Item name is unindexed (risk, Medium)

**Repro** (`C15`):

```
(2, 0, 216, 'SCAN items')          -- SELECT id FROM items WHERE name LIKE 'TAM%' AND active=1
SEARCH item_identifiers USING INDEX uq_item_identifiers_alias (store_id=? AND alias=?)   -- alias path: fine
```

At 20k SKUs the register's type-ahead scans the whole item table on every keystroke; on a terminal that is
already writing bills with `synchronous=FULL`, that is visible lag at the counter. **Fix.**
`CREATE INDEX idx_items_name ON items(name)` (and `name_local` if the register searches it) — the admin list
orders by `id`, so no other index changes. **Cost.** One index; `EXPLAIN` should stop saying SCAN.

### F21 — `catalog_rebuild` can publish a generation with no end (risk, Low)

**Repro** (`C11`):

```
insert (gen=1,start=500,end=NULL,complete=1): ACCEPTED
row: [(1, 500, None, 1, 0)]
```

`deploy-inventory.md` §7 says the new floor is published only after the last chunk. The table lets
`complete = 1` with `end_seq IS NULL`, so a half-finished rebuild can be marked complete and the floor
advanced past a gap the terminals never received. **Fix.** `CHECK ((complete = 0 AND end_seq IS NULL) OR
(complete = 1 AND end_seq IS NOT NULL))`. **Cost.** One CHECK, free pre-code.

### F22 — Price rows can be deleted, not just closed (risk, Low)

**Repro** (`C6`):

```
UPDATE price_minor=1        : error: price_row_immutable      -- 0005 does freeze updates
DELETE the whole v1 row     : ACCEPTED
rows left for m1: []
```

The DDL deliberately has no delete guard because the terminal's catalog apply *is* delete-and-replace. On the
server, deleting a version makes every old line naming it resolve to "not found" → `price_mismatch` on honest
sales. **Fix.** A server-side rule (no `DELETE FROM item_prices` in the Worker) plus the admin route having
none; a trigger is not worth it while the terminal needs deletes. **Cost.** Zero DDL; document it.

### F23 — Import numeric edge cases (risk, Medium ×2)

**Decimal prices** (`C16`):

```
    40.45 -> int(float*100) =   4045   round =   4045
    19.99 -> int(float*100) =   1998   round =   1999      <- a paisa lost
     0.45 -> int(float*100) =     45   round =     45
```

A GoFrugal export prints rupees with two decimals (`DPL 17.10`, `MRP 24`); the transform must parse the
decimal string and `round`, never `int(float*100)`. `STRICT` catches the gross case (a REAL in an INTEGER
column is rejected), not truncation. **Fix.** One function in the transform + a `check-invariants` case.

**Tax-inclusive** — GoFrugal's MRP/DPL formulas (`Margin-Settings-for-the-imported-items`: `MRP = DPL +
Margin% + Local VAT%`) are *tax-inclusive* by construction, so `price_includes_tax = 1` is the right default
and the transform must not "add GST" on top; but a chain that exports exclusive rates and imports them as
inclusive overcharges the customer by the tax on every sale. One rule in D30 ("the transform states
`price_includes_tax` per row; default 1; a row that differs is imported with 0 and its price as exclusive")
and one `check-invariants` case that a 5% inclusive line and a 5% exclusive line give different totals.

**MRP 0** — see F3.

### F24 — No category on items (gap, Low)

GoFrugal groups by category/brand/colour and uses them for category-wise markup (`MarkUp-MarkDown`),
datewise pricing filters (`Datewise-Pricing` "Based on Category") and reports. `items` has no category, so
"sales by category" and category-wide price changes are impossible without one. DESIGN-BRIEF only requires
per-item reports, so this is a non-goal today — but the column is one line and a rebuild later is not.
**Fix.** `items.category TEXT` (+ index) or a `categories` table when the admin UI needs one. **Cost.** One
nullable column; the category then also becomes a new catalog entity (F13), which is why dropping the entity
CHECK now matters.

### F25 — Missing HSN is invisible on import (gap, Low)

A GST tax invoice must print HSN per line (`GST-Reports`); Traasu carries `items.hsn` and copies it to the
line, but the column is nullable, the import rule is silent, and no report lists items without one. A GoFrugal
export with a blank HSN column therefore imports cleanly and the gap is discovered at the first B2B invoice.
**Fix.** The import transform counts rows with blank HSN and the admin item list filters on
`hsn IS NULL OR hsn = ''`; optionally a `422 validation_failed` on `POST /v1/admin/items` for a non-zero-tax
item with no HSN. **Cost.** One Worker check + one filter, no DDL.

## 3. Where Traasu is already stronger (no action)

- **Change log vs `updated_at`** (D1): a scalar, gap-free cursor with a prune floor, epoch, staged snapshot
  and a byte-bounded page. GoFrugal has nothing comparable (its master import is a file, its multi-DB is
  replication, `Multi-DB`, `FAQs-Multi-DB`).
- **Item version + price version frozen on the line** (D26) with `trg_items_version` refusing an equal-version
  edit — verified in `C14`: `item v2, name B → error: item_version_not_bumped`, and a price version is bound
  to one scope (`price v1, scope s1 → error: price_version_scope`).
- **Rewrite-vs-close** (0005 `trg_item_prices_frozen`) makes a price row history rather than mutable state —
  GoFrugal's `Change Selling` edits MRP/price in place; Traasu's is auditable.
- **HSN and `price_includes_tax` copied onto the immutable line** (0002) — this is the fix schema review D2
  asked for and it is in place.
- **Identifier lookup is a unique-index seek** (`uq_item_identifiers_alias`), not a table scan.
- **410 converged correctly:** `pruned_below` advances to `MIN(devices.last_catalog_seq)` over *active*
  devices, a never-pulled device has `NULL` and cannot hold the floor, and every terminal that falls behind
  gets a staged snapshot rather than a partial catalog. No defect found in the floor/head/page arithmetic.

## 4. Not checked

- Barcode *printing* (label layout, printer profiles) — desktop chrome; only the data and export path were
  assessed (F9).
- `Price-Drop`, `Purchase-Formula`, `Margin-Settings-for-colorants` — purchase-side price derivation, out of
  v1 scope (no purchases).
- `Multi-DB` / `Multi-company` replication semantics — different slice.
- Batch/repack item shape triggers (`trg_items_stock_link_*`, `trg_item_repack_sources_shape`) beyond the
  parent/child base-unit case in C1 — `ParityRepack` owns them.
- The register's own catalog-apply transaction ordering (item before identifier/price rows): the FKs make the
  order load-bearing inside one page, and a page applied identifier-first would abort. I did not build a
  repro because the fix is one line in the apply (`PRAGMA defer_foreign_keys=on` or order documents
  parent-first); flagging it for the sync owner. [UNVERIFIED]
- GoFrugal pages I could have fetched but did not: none — every behaviour cited is from the local Markdown.
