# Parity audit — slice `repack` (D29 parent/child, D30 bulk/repack) vs GoFrugal RetailEasy RPOS7

Read: `docs/DESIGN-BRIEF.md`, D26–D30 in `docs/DECISIONS.md`, `docs/d1-schema.sql` + `docs/migrations/0002`–`0008`,
`docs/api-routes.md` (§2 admin routes, §3.3 catalog, §3.4 bills), `docs/query-catalogue.md` §1/§3, `docs/d1-schema.md`,
`docs/deploy-inventory.md` §4/§7, `docs/TRIAGE.md`, the four reviews, `docs/check-invariants.py`.

GoFrugal evidence: `reference/gofrugal/md/{Repacking,Inventory-Repack,Reuse-in-Repack,FAQs-Repacking,
Creating-bulk-Repack-item-in-Item-Master,community-concept-of-parent-and-child-in-item-master-its-applicability,
community-how-to-setup-products-for-repacking-bulk-to-child-repack-12-11-2025,community-creating-bulk-repack-item-in-item-master,
community-learn-about-bulk-repack-process-in-hq,Short-Excess,Short-Excess-Enquiry,Barcode-Printing,Reports-Bulk-Repack,
Making-purchase-entry,serveeasy-Sales-Batch-LOV}.md`, plus the two video read-outs
`vid-parentchild/FINDINGS.md` (YouTube `QsXeATRUDPE`) and `vid-repack/FINDINGS.md` (`7jD0uq0GGIU`).
No extra pages were fetched from gofrugal.com; everything cited is local.

Repro: `docs/parity/repro/repack_audit.py` — one in-memory SQLite per case, `d1-schema.sql` + all migrations,
`PRAGMA foreign_keys=ON`, bills pushed lines → movements → header with `PRAGMA defer_foreign_keys=on`
(the `check-invariants.py` harness pattern). Run it to see the full output (every
expectation passes, so the printed states are exactly what the DDL accepts). The repo's own
`check-schema.py`, `check-invariants.py`, `explain-queries.py` still pass; nothing in the repo was edited.

## 1. Use-case table

| # | Use case | GoFrugal (page) | Traasu (D#, file) | Verdict | Severity |
|---|---|---|---|---|---|
| F1 | Register bootstrap of pack/batch documents | Inventory-Repack (repacked stock sells immediately) | D30; `api-routes.md` §3.3 snapshot pages + entity list; 0008 `trg_item_prices_batch_scope` | **defect** | **Critical** |
| F2 | Catalog page order: child row before parent row | community-concept-of-parent-and-child (parent created first) | D29; 0008 `trg_items_stock_link_ins/_upd`; `api-routes.md` §3.3 | **defect** | **High** |
| F3 | Repack replay / retry (route claims "yes on client id") | FAQs-Repacking #6 (a saved repack is final) | D30; `api-routes.md` L130 | **defect** | **High** |
| F4 | Unpack / ReUse (F10, negative qty) | Inventory-Repack "Reuse option"; Reuse-in-Repack; `vid-repack/FINDINGS.md` §3.6 | D30 (`kind='unpack'`); 0008 `trg_repack_lines_move` | **defect** | **High** |
| F5 | Repack into a deactivated book | Godown-wise transactions; community repack "Location" | D28, D30; 0008 `trg_repacks_scope` | **defect** | **High** |
| F6 | `uses_batches` 1→0 after stock and sales | community-batch-and-expiry; serveeasy-Sales-Batch-LOV | D27; 0008 `trg_items_stock_link_upd` | **defect** | Medium |
| F7 | Child sale / batched sale whose stock link is absent | serveeasy-Sales-Batch-LOV (GoFrugal deducts, and refuses "You have only [10] qty in stock") | D29; `bill_lines` stock CHECK; 0008 `trg_movements_child_line` | **defect** | **High** |
| F8 | Child line's `stock_qty_base` vs `content_qty_base` | — (GoFrugal deducts by conversion, internally) | D29; `query-catalogue.md` §3 ("the line records … `stock_qty_base = qty_base × content_qty_base`") | **defect** | Medium |
| F9 | Repack mapping deleted after use (chain + ReUse) | community-concept-of-parent-and-child (one level); FAQs-Repacking #11 | D30 (one level); 0008 `trg_item_repack_sources_*` | **defect** | **High** |
| F10 | `content_qty_base` / `base_unit` changed after packs exist | FAQs-Repacking #5/#6 (edit restrictions) | D30 ("copied at entry"), D26 versioning | **risk** | Medium |
| F11 | No API route creates the parent/child link or the bulk mapping | Creating-bulk-Repack-item-in-Item-Master (Repackage tab) | D29, D30 vs `api-routes.md` §2 | **gap** | **High** |
| F12 | Barcode stickers for packs; a label that names its batch | Barcode-Printing (per Repacking ref); `vid-repack/FINDINGS.md` §5 | D30 (no label route); `item_identifiers` is item-level | **gap** | Medium |
| F13 | Repack detail / summary report | Reports-Bulk-Repack (3220, 3221); FAQs-Repacking #12 | none in `query-catalogue.md` or §2 | **gap** | Low |
| U1 | Child sale deducts the parent's stock | community-concept-of-parent-and-child; `vid-parentchild/FINDINGS.md` §4 (1.00 → 0.95 packs) | D29 (stock in the parent's base unit) | differs-by-design | — |
| U2 | Several children per parent (piece / pack of 10 / box of 200) | community-concept-of-parent-and-child; `vid-parentchild/FINDINGS.md` §2 | D29 table; `check-invariants.py` biscuit case | match | — |
| U3 | Child price, GST, name independent of the parent | `vid-parentchild/FINDINGS.md` §5 (child GST blank → 0 %) | D29 ("nothing is inherited") | match (Traasu forces the tax to be set) | — |
| U4 | Child batch choice, no MRP prompt | `vid-parentchild` (no batch on the child); serveeasy-Sales-Batch-LOV | D29; `query-catalogue.md` §3 child query | differs-by-design, holds (see §4) | — |
| U5 | Child line `batch_id` NULL vs the D26 price check | — | D26, D29 | match (verified, case J) | — |
| U6 | Void of a child line restores the parent's stock | — | `trg_bill_voids_restock`; `check-invariants.py` "void the child sale" | match | — |
| U7 | Repack entry: qty, selling, MRP, expiry, repack date | Inventory-Repack steps 6–12 | D30; `POST /v1/admin/repacks` | match (MRP is mandatory here, optional in GoFrugal) | — |
| U8 | Conversion loss | community-how-to-setup… (conversion-loss field; 1000 g → 3×300 g loses 100 g) | `repacks.loss_qty_base` + `trg_repacks_loss` | match | — |
| U9 | Multi-bulk mapping, one repack item → many bulks | community-how-to-setup… Q4; community-creating-bulk-repack-item | D30 `item_repack_sources` | differs-by-design (same base unit required), holds | — |
| U10 | Repack allowed in negative stock | FAQs-Repacking #8 ("yes") vs community-how-to-setup… ("only if stocks are available") | D30, D27 | match (GoFrugal contradicts itself; Traasu takes #8) | — |
| U11 | Saved repack cannot be edited or cancelled | FAQs-Repacking #6; community-how-to-setup… Q6 | `trg_repacks_frozen`, `trg_repack_lines_frozen`, no delete | match | — |
| U12 | Repack cost auto-calculated | Inventory-Repack step 6 ("cost is calculated automatically"); `vid-repack/FINDINGS.md` §5 | D30 "Not stored (purchases and cost stay out)" | gap, v1 non-goal (DESIGN-BRIEF) | — |
| U13 | Short / excess on the bulk after repacking | Short-Excess, Short-Excess-Enquiry | D30 "An `adjustment` on the bulk batch" | differs-by-design, holds (one note, §5) | — |
| U14 | Trigger order inside one `db.batch()` for a repack | Inventory-Repack (one save writes everything) | D30 + route; 0008 `trg_item_batches_repack`, `trg_repack_lines_valid` | match (verified, case C1/K) | — |
| U15 | Item import: Parent / Bulk / Child / Repackage mapping | community-learn-about-bulk-repack-process-in-hq (weights in grams); Creating-bulk-Repack-item… | D30 import table | differs-by-design; one risk (F14, §5) | Medium |

---

## 2. Defects

### F1 — No register can ever bootstrap a batch, so no repacked pack can be sold on a recovered terminal — **Critical**

**Scenario.** D30 says "the register receives the new batches, prices and stock through the catalog"
(`DECISIONS.md` D30, "Where"). It cannot. `api-routes.md` §3.3 defines the snapshot's pages as
`items`, `prices`, `identifiers`, `users`, `reference`, `store` — there is no `batches` page and no `books`
page — and its delta entity list is `item, item_price, item_identifier, unit, tax_rate, user, store,
store_settings, numbering_policy, terminal_config`, also without `item_batch`/`book` (the DDL reserves both:
`d1-schema.sql` L261–266, and D28's cost paragraph says "one more table to sync (`book` catalog entity)").
The three DDL triggers then make the omission fatal rather than merely lossy:

* `trg_item_prices_batch_scope` (0008 L347) refuses a batch price row whose batch is absent;
* `trg_item_batches_book_store` (0008 L315) refuses a batch whose book is absent;
* the register's own D28 lookup needs the batch row for MRP, expiry, `book`, `batch_no` and price.

So a newly enrolled terminal, or any terminal after `410 stale_cursor` (D1, `deploy-inventory.md` §7), gets
items and prices, aborts the prices page, and holds zero batches: every batched item — which is every repack
pack — is unbillable, with no fallback (`GET /v1/stock` carries only `{batch_id, on_hand_qty_base, seq}`,
`query-catalogue.md` §3).

**Repro.** `repack_audit.py` cases A1–A3:

```
A1 item doc applies: OK
   snapshot 'prices' page: one price row for a pack batch: batch_price_scope
A2 register batch lookup for the pack (D28): []
A3 item + batch on a register with no book rows: OK/batch_book_store_mismatch
```

**Fix.** Add `books` and `batches` snapshot pages, applied strictly in that order before `prices`; add
`item_batch` and `book` to the delta entity list; and state the page order in §3.3's page list. `items` must also
carry `uses_batches`, `parent_item_id` and `content_qty_base`: the example document (L219–223) omits all three,
and the prose (L256) mentions only the last two. **Cost:** two page types plus their `SELECT`s, one paragraph of
contract, no DDL change, no data migration.

Citations: entity list `api-routes.md` L233–234; snapshot pages L263; parent/child prose L256; item document
example L219–223.

### F2 — A child row applied before its parent row aborts the page and freezes the checkpoint — **High**

**Scenario.** The register runs the same DDL, and `trg_items_stock_link_ins` (0008 L540) raises
`item_stock_link_invalid` unless the parent row already exists, is batched, has no repack source, and the child
is non-batched with a content. A snapshot page that lists a child before its parent therefore fails, and because
the terminal applies a page in one transaction and only advances its checkpoint after commit
(`query-catalogue.md` §1), the *whole* page is lost and retried forever: that terminal stops receiving new items.
Nothing in `api-routes.md` §3.3 fixes the `items` page order (the admin list route already uses `id ASC`,
L691); a GoFrugal import that mints UUIDv4 ids, or a page sorted by name, breaks it. The same trap fires if the
page carries the parent's *pre-edit* document (`uses_batches = 0`).

**Repro.** `repack_audit.py` cases B1–B3:

```
B1 child Coffee 1kg before Coffee 20kg: item_stock_link_invalid
   after the rollback nothing from the page survived: 0
B2 parent first, still unbatched: OK
   then the child: item_stock_link_invalid
B3 parent batched first, then the child: OK/OK
```

**Fix.** State the order in §3.3 as `ORDER BY (parent_item_id IS NOT NULL), id` for the `items` page (all
non-children first, then children — independent of how the importer mints ids), and add a
`check-invariants.py` case that applies a child before its parent and expects the abort. **Cost:** one
`ORDER BY` clause, one test.

### F3 — A repack retry is either an error or a phantom batch; the DDL cannot make it a no-op — **High**

**Scenario.** `POST /v1/admin/repacks` promises idempotency "yes on client `id`". The route's line body has no
batch id (`lines:[{item_id, qty_base, batch:{batch_no?, mrp_minor, …}}]`, `api-routes.md` L130), so the Worker
mints batch ids. Three outcomes, all reproduced:

1. deterministic batch ids + `ON CONFLICT DO NOTHING` → still fails, because the `BEFORE INSERT` trigger
   `trg_repack_lines_valid` fires *before* the `(repack_id, line_no)` dedupe;
2. re-minted batch id + the next price version → a second batch row and a price row exist, with no pack line and
   no movement, and the register's D28 query returns it as a live, sellable batch at 0 on hand;
3. no `ON CONFLICT` → `UNIQUE constraint failed: repacks.id` (HTTP 500 after a timeout that already committed).

**Repro.** `repack_audit.py` cases C1–C4:

```
C2 replay: repack_line_invalid                       (stock unchanged: 4900 / 20)
C3 replay: OK
   the phantom batch: row exists, no line, no stock: ('rp1-b-new', 'rp1', 6000)
   its price row: (2, 7500)
   both batches are live and sellable to the register:
     [('rp1:1:pack', 6000, 20, 7500), ('rp1-b-new', 6000, 0, 7500)]
C4 header re-insert: UNIQUE constraint failed: repacks.id
```

**Fix.** The route must be idempotent by pre-check (`SELECT … FROM repacks WHERE id = ?` → return the stored
header and its `repack_lines` with `on_hand_qty_base`, before writing anything), and batch ids must be derived
(`<repack_id>:<line_no>:pack`) so a retry cannot mint a new batch. Do not rely on `ON CONFLICT` here.
**Cost:** ~10 lines in one handler, one harness case.

### F4 — Unpack (ReUse) is not capped at what was packed, so stock is created from nothing — **High**

**Scenario.** D30: "an unpack reverses both". The DDL never compares the sum of unpack quantities for a pack
batch against that pack line's quantity, and the unpack branch of `trg_repack_lines_valid` (0008 L658) only
checks the *source* bulk batch and the content. Pack 20, unpack 20, unpack 5 more: the sack ends 5,000 g above
what it ever held and the pack batch goes to −5. GoFrugal caps this in practice because its sale/reuse screens
enforce stock (`vid-repack/FINDINGS.md` §3.6–3.7, "You have only [10] qty in stock"); Traasu's no-block rule is
about *sales*, not about inventory documents inventing stock.

**Repro.** `repack_audit.py` case D:

```
after packing 20: sack / pack: (5000, 20)
unpack 20 back: OK                        sack / pack: (25000, 0)
unpack 5 more from the same, now empty, pack batch: OK
sack / pack: 5000 g created, 5 packs sold that never existed: (30000, -5)
unpacked so far vs packed: (25, 20)
```

**Fix.**

```sql
CREATE TRIGGER trg_repack_unpack_capped
BEFORE INSERT ON repack_lines
WHEN (SELECT kind FROM repacks WHERE id = NEW.repack_id) = 'unpack'
 AND NEW.qty_base + IFNULL((SELECT SUM(l.qty_base) FROM repack_lines l
                            JOIN repacks r ON r.id = l.repack_id
                            WHERE r.kind = 'unpack' AND l.batch_id = NEW.batch_id), 0)
     > (SELECT qty_base FROM repack_lines WHERE batch_id = NEW.batch_id)
BEGIN
  SELECT RAISE(ABORT, 'unpack_exceeds_packed');
END;
```

**Cost:** one trigger, one harness case. The pack side stays uncapped (negative stock is allowed, D27).

### F5 — Repacking into a deactivated book sells nothing and destroys the bulk's stock — **High**

**Scenario.** `trg_repacks_scope` (0008 L645) checks the book belongs to the store, not that it is active, and
`trg_item_batches_book_store` (L315) likewise. A repack into a deactivated book succeeds: the packs are created
with stock, and the register's D28 lookup (`JOIN books k ON k.active = 1`, `query-catalogue.md` §3) never
returns them, so the item cannot be priced at all — while the bulk's grams are gone. The same hole exists on the
receipt path (E2), which is D28's own model.

**Repro.** `repack_audit.py` cases E, E2:

```
repack 20 packs into the deactivated book: OK      pack stock recorded: 20
register lookup for the pack item: []
receipt: OK/OK/OK                                  register lookup: []
```

**Fix.** Add `AND k.active = 1` to the `books` `EXISTS` in `trg_repacks_scope`, and to
`trg_movements_receipt_book`'s join. **Cost:** one predicate each, no data migration (pre-code).

### F6 — `uses_batches` 1→0 is allowed while the item still has a live batch — **Medium**

**Scenario.** `trg_items_stock_link_upd` (0008 L551) refuses the switch only when the item has children or a
repack source. An ordinary batched item with stock and sale history can be switched to item-level pricing in one
`UPDATE … version+1`; from then on nothing deducts the batch, its price set stays open, and the register never
sees the stock again. The 9 units cannot be sold, only adjusted. (The repack and bulk cases are protected
because they hold source rows; the parent case because it has children — this hole is the ordinary batched
item, i.e. most of the shop.)

**Repro.** `repack_audit.py` case F:

```
a sale from the batch: OK     on hand: 9
turn batches off, no children and no repack sources: OK
now sell two at the item level: OK
the batch still holds 9 and nothing reaches it again: 9
```

**Fix.** Extend the second branch of `trg_items_stock_link_upd` with
`OR EXISTS (SELECT 1 FROM item_batches b WHERE b.item_id = NEW.id)` (a batch is never deleted, so any batch at
all is enough; add `AND b.retired_at IS NULL` if retiring every batch should be the escape hatch).
**Cost:** one `EXISTS` clause, one harness case.

### F7 — A stock-bearing sale is accepted with no stock link at all, and nothing is deducted — **High**

**Scenario.** Two paths, both accepted by the DDL:

* **Child.** `query-catalogue.md` §3's child query returns `NULL` when every parent batch is retired, expired or
  in an inactive book (`ORDER BY … LIMIT 1` over a filtered set). `bill_lines` then permits all three stock
  columns NULL (the CHECK only ties them to each other), and `trg_movements_child_line` only fires when
  `stock_item_id IS NOT NULL`. So the child is billed, no movement is written, and the sack's stock never moves —
  silently, permanently, and the cashier is never told. The realistic trigger is expiry: a sack passes its date
  and every subsequent coffee sale stops touching stock.
* **Batched item.** Nothing requires `batch_id` on a line whose item has `uses_batches = 1`; such a line prices
  from an item-level set, writes no movement, and leaves the batch untouched.

**Repro.** `repack_audit.py` cases G, G2:

```
child pick with only an expired sack: None
the child is still billed, with no stock link at all: OK
the sack is untouched: 20000
child line stock columns: (None, None, None)
movements for the bill: 0
--
sell a pack without a batch_id: OK      pack stock untouched: 20
```

**Fix.** Two rules, both cheap:
(1) the child lookup falls back to the parent's most recent batch *including* retired/expired ones before
returning nothing (last resort — the stock then goes negative, which D27 already accepts, instead of vanishing);
(2) the Worker treats a line with no stock link for an item that has `parent_item_id` or `uses_batches = 1` as
malformed: flag it (accept, never reject, D2) and write an `audit_events` row. **Cost:** one extra ordering tier
in one query, one comparison in the push handler.

### F8 — The child's `stock_qty_base` is trusted, and can understate what left the sack — **Medium**

**Scenario.** `query-catalogue.md` §3 states the rule (`stock_qty_base = qty_base × content_qty_base`) and
`trg_movements_child_line` (0008 L579) enforces only that the *movement* equals the *line*. Nothing compares the
line to the item's content, and the server stores it as sent (D27 calls the server "the stock truth"). Sell two
1 kg packs and record 1,000 g taken: the bill is accepted, the sack drops 1 kg for 2 kg sold.

**Repro.** `repack_audit.py` case H:

```
sell 2 x 1kg but record 1000 g taken: OK
sack after selling 2 kg: 19000
line stock_qty_base vs qty x content: (1000, 2000)
```

**Fix.** In the push handler, recompute `qty_base × content_qty_base` from the item named by `stock_item_id`;
on a mismatch write an `audit_events` row `kind='stock_qty_mismatch'` (no new column, no rejection). Skip the
check when the line's `item_version` is older than the item's current `version`, where the content may have
legitimately changed. **Cost:** one multiply plus one conditional insert per stock-bearing line.

### F9 — The repack mapping can be deleted, which creates the chain D30 forbids and breaks ReUse forever — **High**

**Scenario.** `item_repack_sources` has a `BEFORE UPDATE` trigger and no `BEFORE DELETE`
(0008 L533; compare `trg_item_batches_no_delete` L337 and `trg_item_repack_sources_shape` L517, which only
checks on insert). Deleting a mapping does two things:

* every existing pack batch made under that mapping can no longer be unpacked — the header itself is refused
  (`trg_repacks_scope` requires a source for the bulk item), so ReUse is gone for stock already on the shelf;
* with the row gone, a new mapping can make a pack item a bulk (2-level chain), and the pack side then deducts
  **base units** from a batch holding **packs**: `trg_repack_lines_move` takes `qty × content` from the bulk
  batch, so 2 × 500 g removes 1,000 packs from a batch holding 19, i.e. a 1000× over-deduction.

**Repro.** `repack_audit.py` case I2:

```
DELETE FROM item_repack_sources: OK
ReUse of a batch made under that mapping is now refused at the header: repack_scope_invalid
map Rice 500g <- Rice 1kg: a chain D30 forbids: OK/OK
Rice 1kg is now both a bulk and a pack item (a chain): 1
repack packs from packs: Rice 500g out of a Rice 1kg pack batch: OK/OK/OK/-981
```

**Fix.** `CREATE TRIGGER trg_item_repack_sources_no_delete BEFORE DELETE ON item_repack_sources
BEGIN SELECT RAISE(ABORT,'repack_source_immutable'); END;` — mirroring the batch trigger. A wrong mapping is
then fixed with a new item or a migration, which is the same answer GoFrugal gives for a wrong repack
(FAQs-Repacking #6, community Q6: only a stock update). **Cost:** four lines, one harness case.

### F10 — Item-master edits after use: content, and the worse `base_unit` change — **Medium risk**

**Scenario A (content).** Changing a repack item's `content_qty_base` (allowed with a version bump) leaves old
pack batches at the old content, and an unpack computed from the item's *current* content is refused by
`trg_repack_lines_valid`; the recorded content works. That is the right behaviour, but the route must read
`repack_lines.content_qty_base` (by `batch_id`) rather than the item's, or ReUse silently fails on every batch
packed before the edit. **Fix:** one sentence in the route definition; cost ~1 query.

**Scenario B (base unit).** `trg_items_version` demands a version bump to change `base_unit` but permits the
change, and no trigger refuses it once stock, movements or repack lines exist. Every stored quantity is then
reinterpreted in the new unit — including the bulk's grams and every `repack_lines.content_qty_base` — while
`bill_lines` copies `dimension` but not `base_unit` (D26), so old lines cannot be reinterpreted at all.

**Repro.** `repack_audit.py` case I1 / I3:

```
content 1000 -> 500: OK
unpack the old batch at the item's current content: repack_line_invalid
unpack at the content recorded on the pack line: OK
--
base_unit g -> kg, version bumped: OK
5000 base units that were grams are now read as 5000 kg: 5000
```

**Fix.** Refuse `base_unit`/`dimension` changes once the item has any movement, bill line or batch:
`BEFORE UPDATE OF base_unit, dimension ON items WHEN EXISTS (…)` → `item_unit_frozen`. **Cost:** one trigger,
one harness case; add `base_unit` to `bill_lines` if old lines must stay interpretable (that is a 0001-era
column decision, out of this slice).

---

## 3. Gaps

### F11 — No route writes the parent/child link, the content, or the bulk mapping — **High**
D29 and D30's structures are reachable only by the GoFrugal import. `PATCH /v1/admin/items/:id` enumerates
`name, name_local, tax, HSN, MRP, flags, active` and has a `409 immutable_field` error (`api-routes.md` L118);
`POST /v1/admin/items` does not specify its body; and **no route exists at all for `item_repack_sources`**, so a
shop cannot map Rice 1 kg to its sack from the admin UI, only via an import. GoFrugal does it in the Item Master's
Repackage tab (Creating-bulk-Repack-item-in-Item-Master, community-concept-of-parent-and-child step 5).
Not a DESIGN-BRIEF non-goal (parent/child and repack are v1). **Fix:** specify the create body fields
(`uses_batches`, `parent_item_id`, `content_qty_base`) and add
`PUT /v1/admin/items/:id/repack-sources` (replace the set, `422` on the trigger's rules, `409` once any pack batch
exists). **Cost:** one route, one body definition; the DDL already validates everything.

### F12 — There is no label print for a pack, and no identifier that can name a batch — **Medium**
GoFrugal prints stickers per repack (`Barcode-Printing`: transaction = Repacking, Ref No = the repack) and the
bill line shows a per-item code with a suffix (`vid-repack/FINDINGS.md` §5, `00022709171`) [UNVERIFIED whether
the suffix encodes the batch]. Traasu has no label route and `item_identifiers` is `(item, store, alias, kind,
qty_base)` with no `batch_id` — so a label printed for the April pack (MRP ₹100) scans to the item, and D28's
default is the **latest** batch, i.e. May's ₹200, with only a prompt to correct it. That undercuts D27's premise
("the April pack cannot move to the May price"). **Fix:** `item_identifiers.batch_id` (nullable, scope-checked
like `item_prices.batch_id`), the alias query returns it, the register pre-selects that batch; and an admin
"print labels for repack N" screen. **Cost:** one column + one index change + the alias query; the print screen is
UI only. If v1 defers labels, say so and require the cashier to confirm the MRP prompt.

### F13 — No repack report — **Low**
GoFrugal ships Repack Details 3220 and Repack Summary 3221 (Reports-Bulk-Repack; FAQs-Repacking #12), and Short
Excess Enquiry for the same period. `query-catalogue.md` has nothing on `repacks`, and §2 has no report route, so
the owner cannot see what was packed, from which sack, at what loss. **Fix:**
`GET /v1/admin/reports/repacks?from&to` returning header, lines (`item_id, qty_base, content_qty_base, mrp`,
batch), and `loss_qty_base`, plus a bulk-side roll-up (in, packed, loss) that subsumes GoFrugal's Short Excess
enquiry. **Cost:** one fixed query + route; the data is already there.

## 4. Why the matches and the by-design differences hold

**U1 (deduction unit).** GoFrugal deducts `child grams ÷ parent conversion` in *parent packs* (1.00 → 0.95), a
fractional balance; Traasu deducts `qty × content_qty_base` in the parent's **base unit** (1,000 g from a 20,000 g
sack). Integer arithmetic, no rounding residue, and the parent can also be sold whole. D29's reasoning holds; the
one loss is that a GoFrugal-sourced report comparing "packs" must divide by the parent's pack weight, which
Traasu no longer stores (F14).

**U4 (no MRP prompt on a child).** Legally sound: the pack the customer receives is packed by the shop, so its
MRP is the shop's declared one (`items.mrp_minor` on the child, or none for loose goods), not the sack's printed
MRP; the sack's MRP is a property of a pack that is not being sold. GoFrugal's video shows the child carrying no
batch at all, so it has nothing to prompt with either. Two consequences to accept explicitly: the cashier cannot
see which sack was drawn (a 1 g remainder can be picked ahead of a full sack, because the order is
"any stock, then book priority"), and expiry filtering means a child silently stops deducting once the only
sack expires — that is F7. A non-interactive cart hint ("drawn from RICE-1, 12 kg left") costs one row already
fetched and removes most of the surprise.

**U5 (child `batch_id` NULL).** Verified: the D26 key is `(item_id, IFNULL(store_id,'') = IFNULL(price_store_id,''),
version)`; the child's item-level set is its own item, so a NULL `batch_id` resolves exactly, and the parent's
batch versions are unreachable from the child (`repack_audit.py` case J:
`the line: (None, 3, 'coffee', 'cf1', 1000)`, `D26 lookup for the child line finds the child's own set: [(15000,)]`).
One loose end: a child line carrying a *bogus* `batch_id` is still accepted and still passes the price check
(same case), because nothing reads `batch_id` for a non-batched item. Reject it in the Worker (`422`) as noise,
not as a money risk.

**U6 (void).** `trg_bill_voids_restock` copies every `sale` movement of the bill back, so a child's void credits
the parent batch it actually came from (`check-invariants.py`: 4,000 → 5,000 g). Correct, and it stays correct
because the movement — not the child line — carries the batch.

**U9 (multi-bulk).** GoFrugal allows several bulks per repack item and expresses sizes in "grams", so a repack
item mapped to a piece-count bulk and a gram bulk is representable there. Traasu requires one base unit across
the bulks (`trg_item_repack_sources_shape`), which is the only way a single `content_qty_base` can be correct;
the harness proves a mismatched bulk is refused. This is a tightening, not a loss — the shop maps separate repack
items per unit.

**U13 (short/excess).** A counted `adjustment` is equivalent to GoFrugal's PhyStock entry and lands in the same
ledger. One note: `inventory_movements.reason` has no distinct value for a repack short/excess, so a genuine
loss and a data-entry correction look identical; `note` + `created_by` distinguish them today. If the owner wants
the Short Excess Enquiry equivalent, F13's bulk roll-up (in / packed / loss / adjusted) gives it.

**U14 (batch order).** The documented order — header, new batches, their prices, lines — is the *only* order the
triggers accept: `trg_item_batches_repack` needs the header, `trg_repack_lines_valid` needs the batch with
`repack_id`, and the line then writes both movements. Verified in case C1 (sack 25,000 → 4,900 with a 100 g
loss, packs 20). One implementation note the route should state: each new batch needs its **own** price version,
because versions are per item and only grow (`trg_item_prices_version_scope`); two pack lines of one item at one
version are refused (case K). Allocate `MAX(version)+1 … +N` in the same batch.

**U15 (import mapping).** The mapping table is right about the shapes (parent/bulk batched, child non-batched with
a content, repackage with a content and source rows), and the trigger order matches D30's stated import order
(cases B, and the harness's source cases). One risk, F14 below.

## 5. One risk in the import mapping — F14 — **Medium**

**Scenario.** D30's import table says `Bulk`/`Parent`: "opening stock = packs × *Repack Conversion (Grams)*", so
the importer multiplies once and then has nowhere to keep the number: D30 says `content_qty_base` belongs to the
**repack item only**. After the import, a later purchase entry or opening-stock correction must be entered in
grams (`25000`), because `POST /v1/admin/stock-receipts` takes only `qty_base` (`api-routes.md` L127) — a 1000×
data-entry risk on every sack, and the same one that D30's table was written to avoid. The DDL already permits
`content_qty_base` on a Parent/Bulk (no trigger tests it), and its meaning is coherent there ("base units in one
purchased pack", the same relation as a child's content).

**Repro.** No failure to reproduce — the column is accepted and unused:
`repack_audit.py`'s `item("rice25", …, batched=1)` fixtures can carry a content without complaint; no trigger or
query reads it. [UNVERIFIED that any admin screen would offer packs.]

**Fix.** One definition in D30: `items.content_qty_base` = base units contained in one unit of this item, applied
to a child (in the parent's unit), a repack item (in the bulk's unit) and a parent/bulk (its own purchased pack);
the receipt screen accepts `qty_packs` and multiplies. No DDL change. **Cost:** one paragraph plus UI; the
alternative (a new `pack_qty_base` column) buys nothing.

---

## 6. Not checked

* GoFrugal's "Weight to Qty" preparation status and `Grinding Per Kg` — not in the local pages, not demonstrated
  in either video; D30 says ask before importing such items.
* GoFrugal auto-cost / Rate Fix Bulk Items / Auto Rate Fix in repack (cost propagation from a purchase to the pack
  price). Out of v1 (no purchases, no cost); not fetched, not compared.
* The Repack Summary / Details report *columns* — `Reports-Bulk-Repack.md` carries only screenshots, so what 3220
  and 3221 actually show could not be verified.
* Whether GoFrugal's repack barcode encodes the batch or the item — the video's `00022709171` suffix is
  unexplained; F12's fix does not depend on it.
* Two admin operators repacking the same bulk batch concurrently (D1 serialises; both entries commit, stock moves
  twice) — correct by design, not exercised.
* Archival interaction: repack movements have no `bill_id`, so D22's cascade never deletes them while it deletes
  sale movements; `batch_stock` keeps the balance either way (D27 states the consequence). Not exercised here.
* Multi-store: a repack is per store and transfers between stores remain a v1 non-goal; not tested.
* The paper-trail law question behind F12 (what must appear on a shop's own pack label, including MRP) — outside
  the design, needs the owner's accountant.
