# Repack Specification

## Purpose
Defines how bulk stock is opened and packed into labelled packs with their own batches, MRP and quantity, and
how packs are unpacked again, so a 25 kg rice sack becomes sellable 1 kg packets with a stock history.

## Requirements

### Requirement: A repack item names the bulk items it comes from
The system SHALL record which bulk items a repack item may be packed from, allowing several bulks per repack item, and SHALL require every bulk of one repack item to use the same base unit. (D30; `item_repack_sources`)

#### Scenario: Rice 1 kg maps to two rice bulks
- **GIVEN** repack item Rice 1 kg with a content of 1000 g, and two bulk rice items stocked in grams
- **WHEN** both mappings are created
- **THEN** both are accepted
- **AND** a mapping to a bulk in pieces is refused
- **AND** Covered by check-invariants: "map Rice 1 kg and Rice 500 g to the sack; Rice 1 kg also to a second rice bulk"

### Requirement: Repack and parent links are one level deep
The system SHALL refuse a repack item as a bulk, a child as a repack item, and a repack item as a parent, so stock never chains. (D30; `trg_item_repack_sources_shape`, `trg_items_stock_link_ins`)

#### Scenario: Packing packs into packs is refused
- **GIVEN** repack item Rice 500 g and repack item Rice 1 kg
- **WHEN** Rice 1 kg is mapped as a bulk of Rice 500 g
- **THEN** the mapping is refused as an invalid repack source
- **AND** naming Rice 1 kg as a parent is refused as an invalid stock link
- **AND** Covered by check-invariants: "a repack item as a bulk (chain)"

### Requirement: A pack line creates a new batch of the repack item
The system SHALL create one new batch per pack line, in the entry's book, of the repack item, carrying a required MRP, an optional expiry and its own selling prices. (D30; `repacks`, `repack_lines`, `trg_repack_lines_valid`)

#### Scenario: Packing 20 × 1 kg and 9 × 500 g
- **GIVEN** a rice sack of 25,000 g in the Main book
- **WHEN** a repack of 20 packs of Rice 1 kg at MRP ₹60 and 9 packs of Rice 500 g at MRP ₹32 is saved
- **THEN** two new pack batches exist, each with its MRP and quantity
- **AND** each pack batch belongs to the repack item it was packed as
- **AND** Covered by check-invariants: "pack 20 x 1 kg and 9 x 500 g, 100 g lost: sack 25000 - 20000 - 4500 - 100"

### Requirement: A pack batch without an MRP is refused
The system SHALL refuse a pack line whose new batch carries no MRP, because the pack is sold at a printed price. (D30; `trg_repack_lines_valid`)

#### Scenario: A pack batch with no printed price
- **GIVEN** a repack batch of Rice 1 kg with no MRP
- **WHEN** a pack line names it
- **THEN** the line is refused as an invalid repack line
- **AND** Covered by check-invariants: "a pack batch without an MRP"

### Requirement: A pack batch is used by one line and one repack only
The system SHALL refuse a second pack line into the same new batch, a pack batch created outside the repack, and a pack batch in a different book from the entry. (D30; `trg_repack_lines_valid`, `trg_item_batches_repack`)

#### Scenario: Two lines cannot share one pack batch
- **GIVEN** a repack line already writing into pack batch rb_1kg
- **WHEN** a second line names the same batch
- **THEN** it is refused as an invalid repack line
- **AND** a purchase-entry batch and a batch in the other book are refused the same way
- **AND** Covered by check-invariants: "a second line into the same new batch"

### Requirement: The pack and unpack ledger is written by the schema
The system SHALL write, per pack line, the bulk quantity out and the packs in as two repack movements, and MUST NOT let a repack move one side only. (D30; `trg_repack_lines_move`)

#### Scenario: Twenty packs out of one sack
- **GIVEN** a sack of 25,000 g and a line of 20 packs of 1,000 g
- **WHEN** the line is stored
- **THEN** the sack's movement is 20,000 g out and the pack batch's is 20 in
- **AND** the sack's balance is 5,000 g

### Requirement: Conversion loss is its own out movement
The system SHALL record a repack's conversion loss as one additional out movement from the bulk batch, and MUST NOT fold it into a pack line. (D30; `repacks.loss_qty_base`, `trg_repacks_loss`)

#### Scenario: 100 g spilt while packing
- **GIVEN** a sack of 25,000 g and a repack with a loss of 100 g
- **WHEN** the repack is saved with 20 × 1 kg and 9 × 500 g
- **THEN** 20,000 g and 4,500 g leave for the packs and 100 g leaves as loss
- **AND** the sack's balance is 400 g
- **AND** Covered by check-invariants: "pack 20 x 1 kg and 9 x 500 g, 100 g lost: sack 25000 - 20000 - 4500 - 100"

### Requirement: An unpack returns packs to the batch they came from
The system SHALL reverse both movements of a pack line on an unpack, and SHALL refuse an unpack of a pack batch into a bulk batch it was not packed from or at a different content. (D30; `trg_repack_lines_valid`)

#### Scenario: Five 1 kg packs go back into the sack
- **GIVEN** a pack batch of 20 × Rice 1 kg made from sack RICE-1
- **WHEN** 5 packs are unpacked into RICE-1
- **THEN** 5 packs leave the pack batch and 5,000 g return to the sack
- **AND** an unpack of those packs into a different sack is refused
- **AND** Covered by check-invariants: "unpack 5 x 1 kg back into the sack"

### Requirement: Unpacking more than was packed is not capped
The system SHALL accept an unpack and a pack beyond the quantities on hand, so the bulk batch and the pack batch may both go negative, and MUST NOT block a repack on a stock check. (D30; D27 negative stock)
<!-- OPEN parity-repack #F4: unpack is never capped at what was packed, so an unpack of 5 more packs invents 5,000 g in the sack and sells packs that never existed. -->

#### Scenario: Packing more than the sack holds
- **GIVEN** a sack whose balance is already negative after earlier packing
- **WHEN** another 30 packs of 1,000 g are packed from it
- **THEN** the repack is accepted
- **AND** the sack's balance goes further negative
- **AND** Covered by check-invariants: "repack more than the sack holds: allowed, the sack goes negative"

### Requirement: A saved repack cannot be edited or cancelled
The system SHALL keep a saved repack and its lines immutable and MUST NOT allow an update or a delete of either, so a mistake is undone by an unpack or an adjustment. (D30; `trg_repacks_frozen`, `trg_repack_lines_frozen`, `trg_repacks_no_delete`, `trg_repack_lines_no_delete`)

#### Scenario: Editing a saved repack is refused
- **GIVEN** a saved repack with a loss of 100 g
- **WHEN** its loss is set to 0, or one of its lines is deleted
- **THEN** each write is refused as immutable
- **AND** Covered by check-invariants: "edit or delete a saved repack"

### Requirement: A repack entry is one atomic admin write of at least one line
The system SHALL write a repack's header, its new batches, their prices and its lines in one atomic write in the admin plane, and SHALL refuse an entry with no lines. (D30; `api-routes.md` POST `/v1/admin/repacks`)

#### Scenario: A header without lines
- **GIVEN** a repack of the rice sack with a book, a kind and no lines
- **WHEN** the entry is saved
- **THEN** the request is refused as invalid
- **AND** no repack row exists
<!-- OPEN parity-repack #F3: a retried repack either errors or mints a phantom batch, because the route cannot pre-check its own client id. -->

### Requirement: A repack must stay inside the store's active books
The system SHALL accept a repack only from a bulk batch of a bulk item in the same store and into a book of that store, and a repack into a deactivated book MUST NOT be accepted. (D30; `trg_repacks_scope`, `trg_item_batches_repack`)
<!-- OPEN parity-repack #F5: the scope trigger checks the book's store but not that it is active, so packs can land where the register never looks. -->

#### Scenario: A repack from a non-bulk item
- **GIVEN** a coffee sack batch whose item has no repack mapping
- **WHEN** a pack repack names that batch as its bulk
- **THEN** the write is refused as an invalid repack scope
- **AND** Covered by check-invariants: "a repack from an item that is no bulk"

### Requirement: A bulk item may also be a parent
The system SHALL allow a bulk item to serve as the parent of child items as well, so loose scoops and repacked packets may come out of the same sack, and opening a pack to make smaller packs SHALL be an unpack followed by a pack. (D30 "A bulk item may also be a parent")

#### Scenario: Loose scoops and packets from one sack
- **GIVEN** a rice sack item that is both a bulk for Rice 1 kg and the parent of a loose 500 g scoop item
- **WHEN** the scoop is billed and separately 10 packs of Rice 1 kg are packed
- **THEN** both transactions reduce the sack's grams
- **AND** the pack batch exists as its own stock

### Requirement: A repack stores no cost
The system SHALL keep the repack to quantities, MRPs, expiry, reference and book, and MUST NOT store a computed or entered cost. (D30 "Cost auto-calculated: not stored")

#### Scenario: No cost on a repack
- **GIVEN** a repack of 20 rice packs
- **WHEN** it is saved
- **THEN** the stored record holds no cost amount
- **AND** a short or excess against the bulk is fixed with an adjustment on that batch

### Requirement: A repacked batch is sellable like any other batch
The system SHALL deliver a repack's new batches, their MRP and prices to registers, so a pack can be scanned and billed as soon as the entry is saved. (D30 "Where"; D28 batch rules)

#### Scenario: Packets on sale immediately
- **GIVEN** a saved repack of 20 × Rice 1 kg at MRP ₹60
- **WHEN** a register pulls the catalog and stock
- **THEN** the pack batch exists with 20 on hand
- **AND** scanning Rice 1 kg uses that batch at its MRP and price
