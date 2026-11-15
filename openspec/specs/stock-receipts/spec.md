# Stock Receipts Specification

## Purpose
Defines the admin purchase entry that puts stock into one book, creating a new batch and its prices per line, so
the register later sells exactly the packs the shop received.

## Requirements

### Requirement: A receipt names one store, one book and one supplier reference
The system SHALL record a stock receipt as a header holding the store, the book the goods went into, the supplier's bill number as free text and the receipt time. (D28; 0008 `stock_receipts`)

#### Scenario: A Godown receipt
- **GIVEN** store s1 with books Main and Godown
- **WHEN** the owner saves a receipt for the Godown with supplier bill number INV-778
- **THEN** one receipt row exists for s1, the Godown and INV-778
- **AND** Covered by check-invariants: "purchase entry into the Godown"

### Requirement: Every receipt line creates a new batch
The system SHALL create a new batch for every line of a receipt, even when an older batch has the same MRP and price, so each batch is exactly one stock entry. (D28; `item_batches.received_at`)

#### Scenario: A repeat delivery at the same price is still a new batch
- **GIVEN** soap batch MAY-24 at MRP ₹200 with stock, and a new receipt of 24 bars at MRP ₹200
- **WHEN** the receipt is saved
- **THEN** a second batch exists with 24 bars in it
- **AND** MAY-24's quantity is unchanged

### Requirement: A blank batch number is generated from the entry
The system SHALL generate a batch number from the receipt when the line leaves it blank, and SHALL refuse a batch number that already exists for that item in that store. (D28; `uq_item_batches_no`)

#### Scenario: Two lines of one entry cannot share a generated number
- **GIVEN** a receipt with two soap lines and no batch numbers typed
- **WHEN** both lines are saved
- **THEN** each batch gets its own generated number
- **AND** a manually repeated batch number is refused as a duplicate

### Requirement: The receipt, its batches, prices and movements are one write
The system SHALL write the receipt header, the new batches, their price sets and one receipt movement per line in a single atomic write, so a failed save leaves no batch without its stock. (D28; `api-routes.md` POST `/v1/admin/stock-receipts`)

#### Scenario: A line that fails aborts the whole entry
- **GIVEN** a receipt of two lines where the second names a batch number that already exists
- **WHEN** the entry is saved
- **THEN** the save fails as a whole
- **AND** neither the receipt nor the first line's batch exists afterwards

### Requirement: A receipt movement must land in a batch of the entry's book
The system SHALL accept a receipt movement only when its batch belongs to the book and store of the receipt, and MUST NOT move goods into a batch held in another book. (D28; `trg_movements_receipt_book`)

#### Scenario: A Godown entry cannot stock a Main batch
- **GIVEN** receipt pr1 for the Godown, and oil batch OIL-A in the Main book
- **WHEN** a receipt movement links pr1 to OIL-A
- **THEN** the write is refused as a receipt book mismatch
- **AND** Covered by check-invariants: "Godown receipt line into a Main-book batch"

### Requirement: Each received quantity is added to its batch's on-hand
The system SHALL increase the receiving batch's on-hand by the received base quantity, so the batch is immediately sellable from the book it landed in. (D27; `trg_movements_batch_stock`)

#### Scenario: Three books of stock land on the right batches
- **GIVEN** oil batches A in Main with 5 packs, B in Main with 3 and C in the Godown with 10
- **WHEN** each receipt movement is written
- **THEN** each batch's on-hand equals its received quantity
- **AND** Covered by check-invariants: "stock in: A 5, B 3, C 10"

### Requirement: A receipt is replay-safe on its client id
The system SHALL treat a repeated receipt with the same client id as the same document and return the stored receipt with its lines rather than creating a second one. (D28; `api-routes.md` route idempotency column)

#### Scenario: A retried save after a timeout
- **GIVEN** a receipt already saved with client id r-77
- **WHEN** the same body is posted again
- **THEN** the stored receipt and its batches are returned
- **AND** no second batch or movement is created

### Requirement: A receipt records no supplier, cost or purchase invoice
The system SHALL store only the free-text supplier bill number, the quantities, the batches and their prices, and MUST NOT store supplier records, cost prices or a purchase document. (D28; D30 "purchases and cost stay out")

#### Scenario: Cost is not accepted
- **GIVEN** a purchase entry for 24 bars of soap
- **WHEN** the owner saves it
- **THEN** the stored receipt holds the supplier's bill number and the received quantities
- **AND** no cost price or supplier row is written
<!-- OPEN parity-stock #S10: there is no opening-stock kind; a shop with goods already on the shelf has only a receipt to record them. -->

### Requirement: One entry never mixes books
The system SHALL keep every line of one receipt in the book named on its header, and SHALL begin a new entry when the owner switches book. (D28; `trg_movements_receipt_book`)

#### Scenario: Switching book starts a new receipt
- **GIVEN** an open entry for the Main book
- **WHEN** the owner picks the Godown
- **THEN** the screen starts a new receipt for the Godown
- **AND** the first entry's lines keep the Main book
