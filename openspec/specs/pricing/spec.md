# Pricing Specification

## Purpose

Fixes how a selling price is recorded and resolved — pack-price tiers per scope, versioned revisions with validity windows, batched prices and the MRP ceiling — so the register and the server can name the exact tier set a bill line used.

## Requirements

### Requirement: A price row is a pack price

Each price row SHALL state what a whole quantity of base units costs: the quantity in base units and the price in paise for that quantity, never a rate per gram. (D2 revised; D26; 0001 `item_prices`)

#### Scenario: Tamarind priced by pack

- **GIVEN** Tamarind holds two tiers: 1000 g for 4000 paise and 500 g for 2500 paise
- **WHEN** a line sells 300 g
- **THEN** the line records a rate of 4000 per basis quantity 1000 g, and a list amount of 1200 paise
- **AND** its price breakdown copies the tier row it used

### Requirement: A tier set belongs to one scope

Price rows SHALL belong either to the franchise default (no store) or to one store; for one item and quantity the store's row SHALL win and the default SHALL be the fallback, and a row SHALL never be chosen by highest version across scopes. (D9, D26; api-routes §9.7; query-catalogue §3)

#### Scenario: a Kukatpally override on ₹39 a kilo

- **GIVEN** the franchise default is 3900 paise per 1000 g and Kukatpally overrides it with 3850
- **WHEN** a Kukatpally register sells 1 kg
- **THEN** the line charges 3850 paise
- **AND** a Secunderabad register selling the same 1 kg charges 3900 paise

### Requirement: A version names one revision of one scope and batch

A price version SHALL name exactly one revision of one scope — and one batch, for a batched item — of one item; versions SHALL only grow per item across all scopes and batches, so reusing a version in another scope or inserting an older one is refused. (D26; 0008 `trg_item_prices_version_scope`)

#### Scenario: a store override cannot reuse the default's version

- **GIVEN** the franchise default is open at version 1
- **WHEN** a Kukatpally override is inserted at version 1
- **THEN** the write is refused as `price_version_scope`
- **AND** the same override at version 2 is accepted

- **AND** Covered by check-invariants: "store override reuses v1", "older version inserted after a newer one"

### Requirement: One revision writes the whole tier set

A revision SHALL close every open row of its scope and then insert the complete tier set at one version and one start time in one batch; a tier left open at another version or start time SHALL abort the write. (D26; 0008 `trg_item_prices_revision_coherent`)

#### Scenario: a half-written tier set

- **GIVEN** the default set has 1000 g and 500 g open at version 1
- **WHEN** an admin inserts a version 2 row for 1000 g while the 500 g row is still open at version 1
- **THEN** the write is refused as `price_revision_mixed`
- **AND** closing both version 1 rows first and then inserting both version 2 rows is accepted

- **AND** Covered by check-invariants: "default v3 while v1 tiers still open (half-open)", "default v3 after closing every v1 tier"

### Requirement: At most one open price per tier per scope

The system SHALL permit at most one open row per item, tier, scope and batch, so a second current price for the same tier is a write failure rather than a choice the register must make. (0001 `uq_item_prices_open`; 0008 adds `batch_id` to the key)

#### Scenario: two current prices for one kilo

- **GIVEN** Tamarind has an open 1000 g default row
- **WHEN** a second open 1000 g default row is inserted in another revision
- **THEN** the insert fails on the unique key
- **AND** one closed row and one open row for the same tier are accepted together

### Requirement: Price rows are history, closed and never edited

A price row's item, scope, tier, version, price, MRP and start time SHALL be immutable; the only permitted update SHALL be setting an end time once, from empty to a value. (0005 `trg_item_prices_frozen`, `trg_item_prices_close_only`)

#### Scenario: correcting a price by editing the row

- **GIVEN** Tamarind's open 1000 g row charges 4000 paise
- **WHEN** an admin updates that row to 3500 paise
- **THEN** the write is refused as `price_row_immutable`
- **AND** closing the row and opening a new version at 3500 paise is accepted, and closing it a second time is refused

### Requirement: A validity window decides the price at a moment

A price row's start time SHALL come from the server clock; its end time SHALL be exclusive and later than its start; the price at a moment SHALL be the row whose start is at or before that moment and whose end is absent or after it. (D26; 0005 `trg_item_prices_window`; query-catalogue §3)

<!-- OPEN parity-catalog #4: no route takes valid_from/valid_to, so a dated window cannot be written; the push check must test a window, not openness. -->

#### Scenario: reading the price for a sale time

- **GIVEN** Tamarind's version 1 tier starts at 1763000000 and has no end
- **WHEN** the register asks for the price at 1763000001
- **THEN** version 1 is returned
- **AND** a row that starts at 1763100000 is not a candidate before that second

### Requirement: Closing a tier set is explicit and keeps the rows

An administrative write with an empty tier list SHALL close that scope's open rows by setting their end time, without deleting them; the closed rows SHALL remain readable as price history. (api-routes §2 `PUT /v1/admin/items/:id/prices`; 0005)

<!-- OPEN parity-catalog #5: closing the last set leaves the item active with no price, so the register has nothing to charge and no rule stops the sale at zero. -->

#### Scenario: closing the Mahua season

- **GIVEN** Mahua holds an open default tier of 1000 g for 8000 paise
- **WHEN** the owner saves an empty tier list for that scope
- **THEN** the open row receives an end time and no row is deleted
- **AND** the item's own active flag is unchanged and the history still returns that row

### Requirement: A price write carries the version it read

A price write SHALL take the base version the admin read and SHALL answer `version_conflict` when that version has moved, so a retry after a committed write cannot create a second revision. (api-routes §2; d1-schema.md failed batch)

#### Scenario: two admins on one price

- **GIVEN** both admins read Mahua at version 7
- **WHEN** the first saves version 8 and the second saves with base version 7
- **THEN** the second is refused with 409 `version_conflict`
- **AND** after re-reading at version 8, the second admin's save becomes version 9

### Requirement: A batched item is priced from the batch being billed

For an item sold from batches, the price SHALL be the price set of the batch the line bills, carried on rows that name that batch; several batches of one item MAY have open prices at once, and within one batch there SHALL be one open price per tier. (D27; 0008 `trg_item_prices_batch_scope`, `uq_item_prices_open`)

<!-- OPEN parity-stock #5: a batch price can exceed the batch MRP, and MRP is stored on both the batch and the price row with no rule between the copies. -->

#### Scenario: April soap and May soap on one shelf

- **GIVEN** April soap sells at 9500 paise with MRP ₹100 and May soap at 11000 paise with MRP ₹200, both open
- **WHEN** a price row is inserted for May soap naming April's batch
- **THEN** the write is refused as `batch_price_scope`
- **AND** each batch pricing its own pack is accepted with both sets open

- **AND** Covered by check-invariants: "May price open at the same time", "batch price in another store"

### Requirement: The MRP that applies is the pack's own

The MRP applied to a line SHALL be the printed MRP of the pack being sold — the batch's, for a batched item — recorded alongside the price and copied onto the bill line when it is written. (D27; 0008 `bill_lines.mrp_minor`)

<!-- OPEN parity-catalog #3: nothing checks that a charged price stays at or below MRP, and precedence between items.mrp_minor and item_prices.mrp_minor for a non-batched item is unstated. -->

#### Scenario: the April pack cannot move to May's price

- **GIVEN** April soap is on the shelf at MRP ₹100 with 6 left, May soap at MRP ₹200
- **WHEN** the cashier bills one April pack at 9500 paise
- **THEN** the line records MRP 10000 paise and charges nothing above it
- **AND** switching the line to May's batch copies MRP 20000 and May's price instead

### Requirement: A sale's price comes from tier, scope, batch and time

The selling price for a quantity SHALL be resolved from the item, that quantity's tier, the store first and the franchise default second, the batch when the item is batched, and the moment of sale; no other row SHALL influence the money. (D26; query-catalogue §3)

#### Scenario: one kilo at the counter

- **GIVEN** Tamarind has a default 1000 g tier at 3900 and a Kukatpally override at 3850, both open
- **WHEN** the register prices 1 kg at 11:40
- **THEN** it returns 3850 paise from the Kukatpally row
- **AND** a closed row and another store's row are not candidates
