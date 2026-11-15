# Item Import Specification

## Purpose

Defines how the chain loads its item master and opening stock from the owner's transformed GoFrugal export, and what provisioning must seed, so a brand-new register's first catalog is complete.

## Requirements

### Requirement: Each GoFrugal preparation status maps to one item shape

The import SHALL map Trade As Is to an ordinary item, Parent to a batched item stocked in its base unit, Child to a non-batched item naming its parent and content, Bulk to a batched item in grams, and Repackage to a batched item with a content and one source row per bulk. (D30)

<!-- OPEN parity-catalog #10: items carry no source key, so a second import run duplicates the catalog and cannot resolve a parent imported last month. -->

#### Scenario: four rows of one export

- **GIVEN** the export holds RICE 25KG as Bulk, RICE 1KG as Repackage, COFFEE 20KG as Parent and COFFEE 1KG as Child
- **WHEN** the import runs
- **THEN** both bulks arrive batched in grams, and RICE 1KG carries content 1000 with a source row for RICE 25KG
- **AND** COFFEE 1KG arrives non-batched with parent COFFEE 20KG and content 1000

### Requirement: Parents and bulks load before children and repack items

The import SHALL write parents and bulks first, then children and repack items, then source rows, and any other order SHALL be refused by the item and source shape rules. (D30; 0008 `trg_items_stock_link_ins`, `trg_item_repack_sources_shape`)

#### Scenario: a child row reaching the database first

- **GIVEN** the COFFEE 1KG child row is written before COFFEE 20KG exists
- **WHEN** the insert runs
- **THEN** it is refused as `item_stock_link_invalid`
- **AND** after the batched parent is written, the same child is accepted

- **AND** Covered by check-invariants: "a child of an unbatched item", "a child of a child (one level)"

### Requirement: Opening stock enters as a receipt in the Main book

Opening stock of a Parent or Bulk SHALL be recorded as packs times its conversion, in the item's base units, as one receipt movement into a batch of the item in the store's Main book, carrying the MRP and selling price the export records for that pack where present. (D30, D28; provisioning creates the Main book)

<!-- OPEN parity-repack #F14: nothing keeps the purchased pack size on a Parent or Bulk, so a later receipt must be typed in grams — a 1000x data-entry risk. -->

#### Scenario: twelve sacks of rice

- **GIVEN** RICE 25KG was exported with 12 sacks of 25 kg, MRP ₹1,600 and a selling price of ₹1,500
- **WHEN** its opening stock is imported
- **THEN** one batch exists in the store's Main book with MRP 160000 and price 150000, and 300000 g on hand
- **AND** the ledger shows one receipt movement of 300000 g, not 12

### Requirement: Imported money and quantities are integers

The import SHALL write money as integer paise and quantities as integer base units; a decimal rupee amount is converted by the transform and never stored as a real number, because every table rejects one. (0001 storage rules)

#### Scenario: a dealer price printed with decimals

- **GIVEN** the export prints a dealer price of 17.10 and an MRP of 24 for one item
- **WHEN** the row is imported
- **THEN** the price is stored as 1710 and the MRP as 2400
- **AND** a real number 17.1 written into a paise column is refused by the table

### Requirement: Imported prices are tax-inclusive by default

An imported price SHALL be stored with the item's tax-inclusive flag, which defaults to inclusive, so the rupee figure the export prints is the amount the customer pays. (0001 `items.price_includes_tax`; DESIGN-BRIEF)

#### Scenario: an MRP of ₹24 at GST 5%

- **GIVEN** the export lists Tata Salt 1 kg at ₹24 with GST 5%
- **WHEN** it is imported and sold
- **THEN** the line charges 2400 paise inclusive, with 2286 paise taxable and 114 paise tax
- **AND** the tax is split 57 paise CGST and 57 paise SGST

### Requirement: Each item loads with its prices and its store identifiers

The import SHALL write, for one item, its default price revision, any store override as a later revision, and its identifiers per store, so a single snapshot makes the item sellable. (D9, D26; 0001 `item_prices`, `item_identifiers`)

#### Scenario: Tata Salt 1 kg on the shelf

- **GIVEN** the export gives Tata Salt 1 kg a default price of ₹24 and a Kukatpally override of ₹23, with EAN 8901234567890 at Kukatpally
- **WHEN** the item is imported
- **THEN** the default tier is version 1 and the override is version 2, because versions only grow per item
- **AND** the EAN row exists for Kukatpally with quantity 1000 g and resolves to that item

### Requirement: A repack item maps only to bulks of its own base unit

A Repackage item SHALL receive one source row per bulk item it names, all of those bulks SHALL share its base unit, and a repack item SHALL NOT be a child, a parent or a bulk. (D30; 0008 `trg_item_repack_sources_shape`)

#### Scenario: Rice 1 kg mapped to two sacks and one wrong item

- **GIVEN** Rice 1 kg is a grams item and two rice bulks are also grams items
- **WHEN** it is mapped to both
- **THEN** both source rows are accepted
- **AND** mapping it to a biscuit bulk counted in pieces is refused as `repack_source_invalid`

- **AND** Covered by check-invariants: "a bulk in another base unit", "a repack item as a bulk (chain)"

### Requirement: Weight to Qty rows are not imported

An export row whose preparation status is Weight to Qty SHALL NOT be imported, and it SHALL be reported to the owner for confirmation before any retry. (D30)

#### Scenario: one unlisted preparation status

- **GIVEN** the export contains one row marked Weight to Qty
- **WHEN** the import completes the rest
- **THEN** that row is absent from the catalog
- **AND** the import report lists it as awaiting the owner's confirmation

### Requirement: Provisioning seeds a complete catalog in one batch

Provisioning SHALL seed the store's Main book, store settings, numbering policy, terminal configuration, the owner and the imported items with their prices and identifiers in one batch that also appends the catalog change rows and advances the allocator, so no terminal's first snapshot is empty. (deploy-inventory §4 step 6; api-routes §3.3)

#### Scenario: a new franchisee with 4,812 items

- **GIVEN** a newly created tenant database with migration seeds only
- **WHEN** provisioning runs
- **THEN** the first snapshot returns the five units, the five GST rates, the Main book, 4,812 items with their prices and identifiers, and the owner
- **AND** an empty catalog after provisioning is treated as a provisioning failure, not a valid state
