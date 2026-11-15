# GoFrugal parity and robustness audit (2026-11-15)

Every use case in five slices, compared three ways: how GoFrugal RetailEasy does it (local copy of its help,
`reference/gofrugal/md/`, plus the two tutorial videos), how the Traasu design does it (D1–D30, the DDL, the
route contract), and whether Traasu's way is correct and fail-proof. A **defect** counts only with a
reproduction against the real DDL; each script is in `docs/parity/repro/` and was re-run on this branch with
the output the report quotes.

| Slice | Report | Use cases | match | by design | gap | defect | risk |
|---|---|---|---|---|---|---|---|
| Billing, pricing, GST, offline | [billing.md](parity/billing.md) | 34 | 7 | 3 | 8 | 10 | 6 |
| Batches, books, stock | [stock.md](parity/stock.md) | 26 | 7 | 4 | 4 | 7 | 4 |
| Parent/child, repack | [repack.md](parity/repack.md) | 28 | 9 | 5 | 4 | 9 | 2 |
| Items, catalog sync, barcodes, import | [catalog.md](parity/catalog.md) | 34 | 7 | 0 | 5 | 10 | 12 |
| Platform, GST reports, releases | [platform.md](parity/platform.md) | 22 | 1 | 8 | 3 | 7 | 1 |
| **Total** | | **144** | **31** | **20** | **24** | **43** | **25** |

Run any repro with `mise exec -- python docs/parity/repro/<script>.py`.

## What holds

The core rules survived every attempt to break them: bill and line immutability, the D23 money identities
for inclusive prices, replay of bill headers and lines, the trigger-kept daily rollup, the void-once rule,
version rules for items and price sets, the repack ledger written by the DDL, one-level stock links, and
"stock never blocks a sale" at the ledger. The 20 by-design differences from GoFrugal mostly hold (each report
says where one does not).

## Fix list (E0 in `openspec/roadmap.md`)

Several slices found the same defect; each cluster below is one story. **Sev.**: C critical (money, stock or
GST wrong, or a sale blocked), H high, M medium. Evidence names the slice report section.

### Critical — before any code

| Story | Problem | Evidence | Smallest fix |
|---|---|---|---|
| E0-S01 | Batches and books never reach a register: no snapshot page and no delta entity for `item_batch`/`book`; a price before its batch, or a child before its parent, aborts the page; a book reorder can never be applied. A fresh or `410`-reset register cannot bill any batched item. | stock F1–F3, repack F1–F2 | Snapshot pages and delta entities for books and batches; one apply order (books → items → batches → prices → identifiers); books sent as one document per store |
| E0-S02 | A line can get two `sale` movements (stock taken twice), and that bill can then never be voided | billing B, stock F4 | Partial unique index on `inventory_movements(bill_id, bill_line_no)`; movement id `<bill>:<line>`; insert `ON CONFLICT DO NOTHING` |
| E0-S03 | An offline bill has no legal invoice number: `invoice_no` is assigned after sync, a duplicate burns a number, the yearly prefix collides | billing A, platform F1 | Register allocates its own gap-free series per counter (as GoFrugal requires for offline); server validates; `length(invoice_no) <= 16` |

### High

| Story | Problem | Evidence | Smallest fix |
|---|---|---|---|
| E0-S04 | MRP is enforced nowhere and stored in four places that can disagree; change-selling is split across two routes | billing C, catalog F3, stock F5 | One MRP owner (the batch); one change-selling route; Worker flag `price_above_mrp` and register hard warning |
| E0-S05 | Units: `dimension`, `base_unit`, sales unit are unlinked; `base_unit` can change after sales; no UQC for GST | catalog F1–F2, platform F3, repack F10 | Trigger tying units to dimension; freeze `dimension`/`base_unit` once used; copy UQC onto the line |
| E0-S06 | No GSTIN, State or address anywhere; no GST report route | platform F2, F11; billing L | `gst_registrations` (or columns on `stores`); rate-wise and HSN summary routes from `bill_lines` |
| E0-S07 | A tax-exclusive item can be created but not billed; tax amount never checked against the line's rate | billing D, H | Fix the exclusive identity; trigger tying tax to `tax_rate_bp` within ±1 paisa per component |
| E0-S08 | Price-set semantics: per-tier precedence names the wrong set, another store's override passes, closing a set leaves holes or an unpriced active item, `pricing_mode` undefined, price rows deletable | billing E, F, I, J; catalog F5, F18, F22 | Whole-set precedence; store check on the line; no close without successor; define `pricing_mode`; no-delete trigger |
| E0-S09 | A discount can be recorded as a list sale | billing G | Line `list_amount` must equal the price-set amount for the quantity, or the line is an override |
| E0-S10 | Migration files are not atomic; the documented recovery fails | platform F7 | One logical change per file; fingerprint check in `migrate.mjs` before re-run |
| E0-S11 | Archival removes the tax data reports need, a void for an archived bill has nowhere to go, retention can be set below the legal period | platform F4, F5, F8; stock F11 | Keep a monthly tax summary in D1; park archived voids with their restock; lawful floor on `retention_months` (§36 CGST, about 7¾ years; to confirm with the accountant) |
| E0-S12 | A stock-bearing line can be accepted with no stock link (nothing deducted); a batched item with no live batch is unbillable; an expired batch sold leaves no trace | repack F7–F8, stock F6–F7 | Line must carry its stock link when the item is batched or a child; item-level fallback with `batch_unavailable` flag; copy expiry onto the line, flag `expired_at_sale` |
| E0-S13 | Repack hardening: a retry is an error or a phantom batch, unpack not capped, a repack into an inactive book loses stock, mapping can be deleted, `uses_batches` can flip with live batches | repack F3–F6, F9 | Idempotent repack by client id; cap unpack at packed minus unpacked; book must be active; no-delete on mappings; freeze `uses_batches` while batches live |
| E0-S14 | No admin route writes the parent link, content or bulk mapping | repack F11 | Routes in `api-routes.md` with the same rules as the triggers |
| E0-S15 | Date-wise prices (promotions) cannot be scheduled | catalog F4 | `valid_from` in the future allowed on the price route; register switches on its clock; D26 "open at created_at" uses the window |
| E0-S16 | Import has no source key (a re-run duplicates the catalog); decimal, tax-inclusive, MRP 0 and missing-HSN cases unhandled | catalog F10, F23, F25 | `items.external_code` unique; import rules for each edge case |

### Medium

| Story | Problem | Evidence |
|---|---|---|
| E0-S17 | Nothing gates a void; a manager cannot approve anything | platform F6, F9 |
| E0-S18 | A physical count stores only the difference; a racing sale hides the shrink | stock F8 |
| E0-S19 | Newest-first leaves the oldest same-price batch unsold until it expires; no FIFO/short-expiry option | stock F9 |
| E0-S20 | The register's on-hand estimate is under-specified and wrong in two of three orderings | stock F10 |
| E0-S21 | Catalog hygiene: register cursor table missing from DDL, price deltas resend history, identifier writes can lose a barcode, inactive items keep barcodes, one EAN two products, no format check, closed `price_source` vocabulary, regional name not on the bill, name unindexed, tax rate history, `catalog_rebuild` without end | catalog F6–F7, F11–F17, F19–F21 |
| E0-S22 | A receipt can write one batch twice or have no lines; an impossible expiry date never expires | stock F13–F14 |
| E0-S23 | Offline lease, clock skew, closed periods, `local_seq` after reinstall, mutable `display_no` | billing O, P |

### Decisions for the owner (gaps, not defects)

| Story | Question | Evidence |
|---|---|---|
| E0-S24 | Weighing-scale barcodes: which scale, price- or weight-embedded, export format? Nothing can parse them today | billing N, catalog F8–F9, F13 |
| E0-S25 | Practice/training login: the only safe shape is a separate local `training.db` from the same DDL with no device row | platform F10 |
| E0-S26 | Labels for repack packs, repack report, godown transfer shape, opening-stock entry, rounding in reports, one rule for future offers, item category | repack F12–F13, stock F12/S10, billing K/M, catalog F24 |

## Not checked

Each slice report ends with its own list. The common ones: real printer output, the register's Go code (none
exists), Cloudflare behavior not already measured in D12/D18/D22, and the legal points marked unverified
(MRP display rules, invoice serial length, retention period), which need the owner's accountant.
