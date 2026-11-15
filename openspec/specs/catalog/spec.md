# Catalog Specification

## Purpose

Defines the item master a register may sell — names, units, tax treatment, version numbers and per-store barcodes — so the server and every terminal describe the same product with the same numbers.

## Requirements

### Requirement: Item master document

The system SHALL store each item as one master document carrying name, optional regional name, dimension, pricing mode, base unit, default sales unit, tax rate, HSN, tax-inclusive flag, MRP, the discount, price-edit, prompt-quantity and scale-quantity flags, active, and a version. (D26; 0001 `items`, 0008 `items.version`)

#### Scenario: Tamarind created once for the whole chain

- **GIVEN** the owner creates Tamarind with dimension mass, base unit g, default sales unit kg, HSN 0810, GST 5% and MRP ₹50
- **WHEN** the item is saved
- **THEN** it exists once for the enterprise at version 1 and is visible to every store
- **AND** a Kukatpally register receives it as an `item` document carrying all of those fields

### Requirement: Quantities are integers in base units

The system SHALL store every quantity as an integer count of the item's base unit and convert to another unit only for display, using the conversion factor the unit row records. (0001 `units`; DESIGN-BRIEF)

#### Scenario: 1.5 kg of tamarind

- **GIVEN** Tamarind is stocked in grams and priced ₹400 per kg
- **WHEN** the cashier weighs 1.5 kg
- **THEN** the line stores `qty_base` 1500, never 1.5
- **AND** the screen shows 1500 g ÷ 1000 g-per-kg = 1.5 kg

### Requirement: Item version grows on every content change

Every content change to an item SHALL increase its version by at least one; a write that repeats a version with different content SHALL be refused, and a write that repeats a version with identical content SHALL be a no-op. (D26; 0008 `trg_items_version`)

#### Scenario: renaming Dal without a version bump

- **GIVEN** Dal sits at version 1 and was sold this morning
- **WHEN** an admin renames it to "Dal Premium" and leaves the version at 1
- **THEN** the write is refused as `item_version_not_bumped`
- **AND** the same rename at version 2 is accepted, and replaying version 2 unchanged is a no-op

- **AND** Covered by check-invariants: "item edit without a version bump", "terminal replays the same item version"

### Requirement: Unit identity freezes after the first sale

Once a bill line references an item, its dimension, pricing mode and base unit SHALL NOT change; such an edit SHALL be refused as `immutable_field` and the owner SHALL create a new item instead. (D26; api-routes `immutable_field`, d1-schema.md `items`)

<!-- OPEN parity-catalog #2: a Worker rule only; the DDL accepts the flip at version + 1. -->
<!-- OPEN parity-catalog #1: the three unit FKs are unrelated, so base_unit need not be the factor-1 unit. -->

#### Scenario: Dal sold in grams, then someone changes the base unit

- **GIVEN** Dal was billed at 1500 g this morning
- **WHEN** an admin PATCHes the base unit from g to kg
- **THEN** the request is refused with `immutable_field`
- **AND** the existing lines still read as 1500 g and the item stays at its current version

### Requirement: Units carry a group, a precision and a factor to base

An item's base unit and default sales unit SHALL be rows of the unit table, which records the unit group (mass, volume, count), the display precision and the integer factor to the base unit of that group. (0001 `units`, `items` FKs)

#### Scenario: an unknown unit is refused

- **GIVEN** the unit table holds g, kg, ml, l and pcs, with kg at factor 1000
- **WHEN** an item is created with base unit "dozen"
- **THEN** the write is refused because no unit row exists
- **AND** creating it with base unit g and default sales unit kg is accepted

### Requirement: Identifiers resolve a scan to exactly one item per store

The system SHALL store each barcode as an identifier row with kind alias, sku, ean or plu, an alias normalised to trimmed upper case, an optional quantity in base units, an active flag and a store, unique among active rows of that store; an exact alias match SHALL win. (D9; 0001 `item_identifiers`, `uq_item_identifiers_alias`; query-catalogue §2)

#### Scenario: scanning a Tata Salt pack

- **GIVEN** Kukatpally holds EAN 8901234567890 for Tata Salt 1 kg with `qty_base` 1000
- **WHEN** the cashier scans it
- **THEN** the cart adds one line of Tata Salt 1 kg, 1000 g
- **AND** typing "  tata salt " finds the same item through the alias `TATA SALT`

### Requirement: A scan never crosses stores

The catalog pull SHALL give a terminal only its own store's identifiers, and an identifier that becomes another store's SHALL reach this terminal as a delete. (D9; api-routes §4)

#### Scenario: an EAN moves from Kukatpally to Secunderabad

- **GIVEN** EAN 8901234567890 belongs to Kukatpally's Tata Salt
- **WHEN** an admin moves that identifier to Secunderabad
- **THEN** Kukatpally's next catalog page carries a delete for that identifier
- **AND** Secunderabad's page carries the upsert, and a Kukatpally register no longer resolves the code

### Requirement: Tax treatment travels with the item

Each item SHALL reference a GST rate from the tax rate master, where the rate is basis points between 0 and 10000, and SHALL record its HSN and whether its price includes tax. (0001 `tax_rates`, `items`; 0002 `bill_lines.hsn`)

<!-- OPEN parity-catalog #6: tax_rates is one mutable row with no history, so a rate change is unverifiable for a terminal that was offline across it. -->

#### Scenario: a GST rate change leaves old bills alone

- **GIVEN** Tata Salt is 5% (500 basis points) with HSN 2501 and was billed yesterday
- **WHEN** the owner changes that rate row to 12%
- **THEN** yesterday's line still reports 500 basis points and HSN 2501
- **AND** the next sale of Tata Salt prices and taxes at 1200 basis points

### Requirement: Stock-link fields are part of the item document

An item SHALL record whether it is sold from batches and, when it is a child or a repack item, its parent and its content in base units; these fields SHALL be versioned with the item, and a child SHALL NOT be batched. (D29, D30; 0008 `items.uses_batches`, `parent_item_id`, `content_qty_base`, `trg_items_stock_link_ins`)

#### Scenario: Coffee 20 kg and Coffee 1 kg

- **GIVEN** Coffee 20 kg is a batched item stocked in grams
- **WHEN** Coffee 1 kg is created with parent Coffee 20 kg and content 1000
- **THEN** it is accepted as a non-batched child
- **AND** the same child created as batched is refused as `item_stock_link_invalid`

- **AND** Covered by check-invariants: "a batched child", "a child of a child (one level)"

### Requirement: Items are deactivated, never deleted

Deleting an item SHALL be a soft delete: active becomes false, the version grows, the item's identifiers are deactivated, and prices and bill lines that name it stay resolvable. (api-routes §2 `DELETE /v1/admin/items/:id`, §8)

<!-- OPEN parity-catalog #15: the identifier lookup filters only item_identifiers.active, so a deactivated item whose identifiers were not retired still scans. -->

#### Scenario: Bread is discontinued

- **GIVEN** Bread is active at version 4 with EAN 8901234500001
- **WHEN** the owner deletes it
- **THEN** the item returns `active: false` at version 5 and its identifiers are inactive
- **AND** a scan of 8901234500001 no longer resolves, while old bills still print Bread

### Requirement: Item creation is idempotent on the client id

A create whose id is already used with the same content SHALL return that item, and a create that reuses the id with different content SHALL be refused as `item_exists`. (api-routes §2 `POST /v1/admin/items`)

#### Scenario: a double-submitted new item

- **GIVEN** the admin web posts a new item with id 0192f3c4-8a5b-7c3d-9e1f-2a3b4c5d6e7f
- **WHEN** the same body is posted again after a timeout
- **THEN** the stored item is returned and no second item exists
- **AND** the same id with a different name is refused with 409 `item_exists`
