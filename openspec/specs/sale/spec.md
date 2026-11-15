# Sale Specification

## Purpose
Turns a scanned or searched item into a bill line at the register and commits the bill locally: the frozen
versions, the batch or parent stock the line uses, and the numbering the receipt carries. Every report and every
push downstream reads what this capability wrote, so the line must record exactly what the cashier saw.

## Requirements

### Requirement: Scan resolves a store identifier to one item and its quantity
The system SHALL resolve a scanned or typed alias, SKU, EAN or PLU to exactly one item, using the register's
store-scoped identifiers matched on the whole normalised string with no partial match, and SHALL add the
identifier's quantity when it carries one. (D9; item_identifiers, query-catalogue §2)

#### Scenario: A weighed pack alias adds 500 g
- **GIVEN** the store prices Tamarind and holds the active alias `TAM 500` for it with `qty_base` 500
- **WHEN** the cashier scans or types `tam 500`
- **THEN** the cart adds one line of Tamarind with 500 g
- **AND** no picker is shown, because an active alias is unique per store

#### Scenario: An unknown code leaves the cart alone
- **GIVEN** no active identifier in this store matches `8901234567890`
- **WHEN** the cashier scans that code
- **THEN** the register reports the code as unknown
- **AND** the cart and its totals are unchanged

### Requirement: Quantities are whole base units and money is whole paise
The system SHALL record every quantity as an integer count of the item's base unit (grams, millilitres, pieces)
and every amount as an integer number of paise, converting the displayed unit only at the edges of the screen and
the receipt. (DESIGN-BRIEF domain shapes; bill_lines.qty_base, bill_lines.price_minor)

#### Scenario: 1.5 kg of tamarind at ₹40 per kg
- **GIVEN** Tamarind is sold in kg with base unit `g`
- **WHEN** the cashier enters 1.5 kg at ₹40.00 per kg
- **THEN** the line carries `qty_base` 1500 and `price_minor` 4000 per 1000 g
- **AND** no field of the line holds a fractional value

### Requirement: The register prices every line from its local catalog
The system SHALL compute each line's price and its decomposition from the register's own SQLite catalog, online
or offline, and SHALL never wait on the server to price a line. (D26; item_prices, item_identifiers)

#### Scenario: Pricing works with the uplink down
- **GIVEN** the register has synced the catalog and the network is down
- **WHEN** the cashier adds 1500 g of Tamarind priced at ₹40.00 per 1000 g
- **THEN** the line is priced at once from the local rows
- **AND** the sale proceeds without a server call

### Requirement: A line freezes the item version and the copied item fields
At the moment a line is added to the cart, the system SHALL copy onto the line the item's version, name,
dimension, HSN, tax rate and price-includes-tax flag, so the receipt and the item master cannot drift apart.
(D26; bill_lines.item_version, item_name, dimension, hsn, tax_rate_bp, price_includes_tax)

#### Scenario: The line carries what the item was when it was added
- **GIVEN** Tamarind is at item version 3 with HSN `0810`, dimension `mass` and GST 5%
- **WHEN** the cashier adds 1500 g of it to the cart
- **THEN** the line carries `item_version` 3, `item_name` Tamarind, `dimension` mass, `hsn` 0810, `tax_rate_bp`
  500 and `price_includes_tax` 1
- **AND** a later item edit changes none of those values on this line

### Requirement: A line freezes the price version and its price scope
The system SHALL record on each line the price version it was priced from and the scope that version belongs to,
where a null scope means the franchise default and a store id means that store's override. (D26;
bill_lines.price_version, bill_lines.price_store_id)

#### Scenario: A store override is named on the line
- **GIVEN** Kukatpally holds its own tier set for Tamarind at version 3, and the franchise default is version 1
- **WHEN** a line is added in Kukatpally
- **THEN** the line carries `price_version` 3 and `price_store_id` equal to the Kukatpally store id
- **AND** a line added in a store with no override carries `price_version` 1 and a null `price_store_id`

<!-- OPEN parity-billing #E: per-tier precedence lets one (version, store) name two tier sets, so the check cannot be exact. -->
<!-- OPEN parity-billing #F: nothing stops price_store_id naming a store other than the bill's store, and the lookup then finds it. -->

### Requirement: An open cart is never repriced
The system SHALL leave every line already in the cart unchanged when a catalog update arrives, and SHALL apply
the new catalog only to lines added after it. (D26; catalog delta)

#### Scenario: A price change mid-cart does not move the screen
- **GIVEN** the open cart holds 1500 g of Tamarind at ₹40.00 per 1000 g, a line amount of 6500 paise
- **WHEN** the owner raises Tamarind to ₹44.00 per 1000 g and the register pulls that change
- **THEN** the cart line still shows 6500 paise and its frozen version and money
- **AND** the next 1000 g added after the update uses 4400 paise

### Requirement: A batched line records the batch, number and MRP it was sold from
The system SHALL record on a batched item's line the batch id, its batch number and its MRP, copied from the
batch the register selected, so an old bill still says which physical stock left the shop. (D27; bill_lines.batch_id,
bill_lines.batch_no, bill_lines.mrp_minor)

#### Scenario: April soap sold at ₹100 MRP
- **GIVEN** the soap has the batch `APR-24` with MRP ₹100 and selling price ₹95, and it is the latest batch
- **WHEN** the cashier bills one soap
- **THEN** the line carries that batch's id in `batch_id`, `batch_no` `APR-24` and `mrp_minor` 10000
- **AND** `price_minor` 9500 stays inside the MRP

<!-- OPEN parity-billing #C: MRP is called a legal ceiling but no rule compares a line's price with its mrp_minor or refuses a batched line with no MRP. -->

### Requirement: A child line records the parent stock it consumes
The system SHALL record on a child item's line the parent item, the parent's batch and the parent base units
taken, and SHALL leave the line's own batch empty. (D29; bill_lines.stock_item_id, stock_batch_id, stock_qty_base)

#### Scenario: One Coffee 1 kg takes 1000 g from the sack
- **GIVEN** the child item Coffee 1 kg has `content_qty_base` 1000 and its parent is the 20 kg sack
- **WHEN** the cashier bills one Coffee 1 kg
- **THEN** the line carries `stock_item_id` of the sack item, `stock_batch_id` of the sack batch in use and
  `stock_qty_base` 1000
- **AND** the line's own `batch_id` is null, so it is priced from the child's own prices

### Requirement: Stock never blocks a sale and an over-quantity sale only warns
The system SHALL complete the sale when the quantity exceeds the batch's on-hand count, and SHALL show a warning
instead of refusing the line. (D27, D28; batch_stock, inventory_movements)

#### Scenario: The last pack is sold twice
- **GIVEN** two registers are offline and the shop has one pack of soap on hand at MRP ₹100
- **WHEN** each register sells one pack
- **THEN** both bills are saved
- **AND** the batch's on-hand count goes to -1 rather than the second bill being refused

### Requirement: The bill is committed locally before the sale completes
The system SHALL write the bill header and all of its lines in one local transaction, lines before the header,
before it treats the sale as complete, so a completed sale always exists as a bill. (D23, D14; bills, bill_lines,
PRAGMA defer_foreign_keys)

#### Scenario: Power is cut immediately after the receipt prints
- **GIVEN** the cashier has taken 6500 paise for a 1500 g Tamarind bill
- **WHEN** power is lost the moment the receipt finishes printing
- **THEN** the bill and its line are both present when the register restarts
- **AND** the bill is either wholly there or absent, never a header without lines

### Requirement: The billing connection runs with synchronous=FULL
The system SHALL run the register's billing SQLite connection in WAL mode with `synchronous=FULL`, so a bill the
register has acknowledged survives a power cut. (D14; register SQLite configuration)

#### Scenario: The setting survives a register restart
- **GIVEN** the register has been billing all day
- **WHEN** the terminal process is restarted and a bill is written
- **THEN** the write is committed with a full fsync
- **AND** no bill acknowledged to the customer can be lost by a power cut

### Requirement: A saved bill carries the register's next display number
The system SHALL assign each saved bill a `display_no` from the register's own numbering series at the moment of
the sale, while offline, and SHALL treat that number as what is printed for the customer. (0001 numbering_policy;
api-routes §9.6)

#### Scenario: Numbers keep running while disconnected
- **GIVEN** terminal 2 of the Kukatpally store last printed `S1-T2-000431` and the uplink is down
- **WHEN** the cashier saves the next bill
- **THEN** the bill carries `display_no` `S1-T2-000432`
- **AND** the register's own counter advances without the server
