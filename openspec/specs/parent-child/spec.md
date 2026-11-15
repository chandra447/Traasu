# Parent And Child Items Specification

## Purpose
Defines how a small pack sells from the stock of the item it is cut or taken from, so a coffee shop's 1 kg bag
draws grams from its 20 kg sack while keeping its own name, price and tax.

## Requirements

### Requirement: A child item sells from its parent's stock
The system SHALL let an item name a parent item and a content quantity, and SHALL take that child's stock from the parent's batches instead of holding any of its own. (D29; 0008 `items.parent_item_id`, `content_qty_base`)

#### Scenario: Coffee 1 kg draws on the sack
- **GIVEN** a parent item Coffee 20 kg stocked in grams, and a child Coffee 1 kg with a content of 1000 g
- **WHEN** one Coffee 1 kg is billed
- **THEN** 1000 g leave a batch of the parent
- **AND** the child holds no stock of its own
- **AND** Covered by check-invariants: "coffee sack (parent, grams) and Coffee 1 kg (child, 1000 g per piece)"

### Requirement: A child is unbatched and its parent is batched
The system SHALL refuse a child item that is sold from batches and MUST NOT accept a child without a content quantity, because stock lives in the parent's batches. (D29; `trg_items_stock_link_ins`)

#### Scenario: A batched child is refused
- **GIVEN** a parent item Coffee 20 kg
- **WHEN** a child is created with batching turned on and 1000 g of content
- **THEN** the write is refused as an invalid stock link
- **AND** the same item without the content is refused as well
- **AND** Covered by check-invariants: "a batched child"

### Requirement: Stock links are one level deep
The system SHALL allow a child to be neither a parent, a repack item nor a bulk item, and SHALL keep a parent that has children batched. (D29; `trg_items_stock_link_ins`, `trg_items_stock_link_upd`)

#### Scenario: A child of a child is refused
- **GIVEN** the child item Coffee 1 kg
- **WHEN** another item names Coffee 1 kg as its parent
- **THEN** the write is refused as an invalid stock link
- **AND** turning the parent unbatched while it has children is refused too
- **AND** Covered by check-invariants: "a child of a child (one level)"
<!-- OPEN parity-repack #F2: the catalog page has no parent-before-child order, so a child row applied first aborts the whole page and freezes the checkpoint. -->

### Requirement: A child keeps its own name, price, tax and identifiers
The system SHALL price a child from its own item and item-level price rows, and MUST NOT inherit price, tax rate, HSN or name from its parent. (D29; `query-catalogue.md` §3 child query)

#### Scenario: The 1 kg bag has its own GST rate
- **GIVEN** a parent sack taxed at 5% and a child 1 kg bag taxed at 18% at its own price
- **WHEN** the child is billed
- **THEN** the line charges the child's price and carries 18% tax
- **AND** the parent's price and tax play no part in the line

### Requirement: A child's sale records the parent stock it took
The system SHALL copy the parent item, the parent batch and the base quantity taken onto the child's bill line, computed as the line quantity times the content, and the sale movement SHALL match those copied values. (D29; `trg_movements_child_line`)

#### Scenario: Selling one 1 kg bag
- **GIVEN** a child with 1000 g of content and a parent batch holding 5000 g
- **WHEN** one 1 kg bag is billed
- **THEN** the line records the parent, the parent batch and 1000 g taken
- **AND** its sale movement names the parent item with 1000 g out
- **AND** the sack's balance is 4000 g
- **AND** Covered by check-invariants: "sell 1 x Coffee 1 kg: 1000 g out of the Godown sack"
<!-- OPEN parity-repack #F8: the server trusts stock_qty_base, so a line can record 1,000 g taken for 2 kg sold and the sack drops too little. -->

### Requirement: A line that takes stock must name the parent and the quantity
The system SHALL refuse a child line that names a parent item without a stock quantity, and MUST NOT store stock columns that disagree with each other. (D29; `bill_lines` stock columns CHECK)

#### Scenario: A child line with no gram figure
- **GIVEN** a child line that names the parent item Coffee 20 kg
- **WHEN** the stock quantity is left empty
- **THEN** the line is refused by the stock columns check
- **AND** Covered by check-invariants: "a child line naming a parent without a quantity"
<!-- OPEN parity-repack #F7: a child line with no stock link at all is accepted and deducts nothing, so a sack that expires stops moving stock silently. -->

### Requirement: The parent batch is chosen by book priority, with no MRP prompt
The system SHALL take the parent batch holding stock from the lowest-priority book, the most recent within that book, and with no stock anywhere SHALL use the lowest-priority book's most recent batch, which goes negative; the register MUST NOT prompt about the parent's MRP or price. (D29; `query-catalogue.md` §3)

#### Scenario: Stock appears in book 1 and takes precedence
- **GIVEN** a sack of 20,000 g in the Main book and 5,000 g in the Godown, the Godown being book 1
- **WHEN** a 1 kg bag is billed
- **THEN** the grams come out of the Godown sack
- **AND** the cashier sees no batch prompt
- **AND** Covered by check-invariants: "stock in book 1 (Godown) as well: book 1 first"

### Requirement: A void returns the parent's quantity
The system SHALL append an in movement of the copied stock quantity to the parent batch when a child's sale is voided, so the sack returns to its earlier balance. (D29; `trg_bill_voids_restock`)

#### Scenario: Voiding a coffee sale
- **GIVEN** a Godown sack at 4000 g after one 1 kg bag was sold
- **WHEN** that bill is voided
- **THEN** 1000 g return to the same sack
- **AND** its balance is 5000 g
- **AND** Covered by check-invariants: "void the child sale: the 1000 g go back to the sack"

### Requirement: Stock is held in the parent's base unit
The system SHALL keep parent stock in whole base units, so a piece parent can carry a pack and a box as two children and a purchase of three boxes records the pieces. (D29; `items.base_unit`, `content_qty_base`)

#### Scenario: Pieces, a pack of 10 and a box of 20 packs
- **GIVEN** a piece parent Biscuit, a child pack of 10 pieces and a child box of 20 packs
- **WHEN** 3 boxes are received and a box then a pack is sold
- **THEN** the receipt records 600 pieces
- **AND** the two sales take 200 and 10 pieces
- **AND** the parent's balance is 390 pieces
- **AND** Covered by check-invariants: "3 boxes received = 600 pieces; sell a box, then a pack"

### Requirement: Reports show a child's sale and the parent's stock
The system SHALL report a child's sales under the child item and its stock under the parent item. (D29 cost "the stock report has to explain that")

#### Scenario: Coffee sales and sack stock in one month report
- **GIVEN** 40 sales of Coffee 1 kg in November from one 20 kg sack
- **WHEN** the owner runs the November report
- **THEN** the sales rows show Coffee 1 kg with its own quantity and amount
- **AND** the stock view shows the sack with the grams consumed by those sales
