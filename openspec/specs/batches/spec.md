# Batches Specification

## Purpose
Defines how stock of one item arrives and is sold as batches, each with its own MRP, optional expiry and
selling price, so the register can bill the pack on the shelf without ever repricing an older one upward.

## Requirements

### Requirement: A stock receipt line creates one batch
The system SHALL create exactly one batch per stock receipt line, carrying a batch number, the MRP, an optional expiry date, the received date and the book the stock landed in. (D27, D28; 0008 `item_batches`)

#### Scenario: April and May soap are two batches
- **GIVEN** soap already on the shelf in batch APR-24 at MRP ₹100, and a purchase entry for 24 bars at MRP ₹200
- **WHEN** the owner saves the entry
- **THEN** batch MAY-24 exists with 24 bars on hand at MRP ₹200
- **AND** APR-24 is unchanged, still holding its own stock at ₹100

### Requirement: A batch's MRP is the printed ceiling for that pack
The system SHALL hold each batch's MRP as the printed maximum price of the pack and MUST NOT set that batch's selling price above its own MRP; a lower-MRP batch never takes another batch's higher price. (D27; `item_batches.mrp_minor`)
<!-- OPEN parity-stock #F5: MRP is stored on the batch and on the price row with no check, so a batch can be priced above its MRP. -->

#### Scenario: The April pack cannot move to the May price
- **GIVEN** April soap at MRP ₹100 and May soap at MRP ₹200, both on the shelf
- **WHEN** a bar is billed from the April batch
- **THEN** the line charges at most ₹100, the price set of the April batch
- **AND** the line records ₹100 as the MRP it was sold under

### Requirement: Each batch has its own selling-price tier set in its store
The system SHALL keep a batch's selling prices as price rows tagged with that batch, in the batch's store, with one open price per tier per batch. (D27; 0008 `item_prices.batch_id`, `uq_item_prices_open`)

#### Scenario: A price row for a batch in another store is refused
- **GIVEN** soap batch MAY-24 in store s1
- **WHEN** a price row names that batch with store s2
- **THEN** the write is refused as a batch price scope mismatch
- **AND** no price exists for MAY-24 outside store s1
- **AND** Covered by check-invariants: "batch price in another store"

### Requirement: The latest batch sets the MRP and selling price of a new line
The system SHALL take the MRP and selling price of a new line from the most recently received batch of the item in the store that is not retired, not expired and in an active book, whatever its on-hand quantity. (D28; `query-catalogue.md` §3)

#### Scenario: May sets the price even though April has stock
- **GIVEN** April soap at MRP ₹100 with 10 bars and May soap at MRP ₹200 with 24 bars
- **WHEN** the cashier scans soap on 2026-11-14
- **THEN** the line uses MAY-24 and ₹200
- **AND** April is offered only as other stock at a different MRP
- **AND** Covered by check-invariants: "latest is May; April has stock at another MRP"

### Requirement: A sold-out latest batch is still the batch billed
The system SHALL keep the latest batch as the one a line uses when every batch at that MRP and price has zero on hand. (D28; `query-catalogue.md` §3)

#### Scenario: May at zero is still chosen
- **GIVEN** May soap received last, with its 24 bars counted down to zero
- **WHEN** the cashier scans soap
- **THEN** the line uses MAY-24
- **AND** no other batch is offered, because none has stock at a different MRP or price
- **AND** Covered by check-invariants: "the latest batch is used even at zero"

### Requirement: The register prompts about other batches at a different MRP or price
The system SHALL list every other batch with stock above zero whose MRP or selling price differs from the chosen batch, showing MRP, price, book, batch number and quantity ordered by book priority, and MUST NOT list batches at the same MRP and price. (D28; `query-catalogue.md` §3)

#### Scenario: April is offered with its MRP and quantity
- **GIVEN** May at MRP ₹200 chosen for the line, and April at MRP ₹100 holding 10 bars
- **WHEN** the cashier scans soap
- **THEN** the prompt offers April at MRP ₹100 with 10 left
- **AND** the cashier may switch the line to it
- **AND** Covered by check-invariants: "prompt shows April's MRP and quantity"

### Requirement: An expired batch cannot be picked or offered
The system SHALL exclude a batch whose expiry date is before the business date from the batch choice and from the prompt, and SHALL treat a batch with no expiry date as never expiring. (D27; `item_batches.expiry_date`)

#### Scenario: The January batch drops out of the offer
- **GIVEN** a January batch expiring 2026-01-01, still holding 5 bars
- **WHEN** soap is scanned for business date 2026-11-14
- **THEN** the January batch is neither used nor listed in the prompt
- **AND** the April batch with no expiry is used instead

### Requirement: A batch edit bumps its version
The system SHALL require the batch version to increase on every change to the batch number, MRP, expiry date or retirement, and SHALL treat an equal version with equal content as a replay that changes nothing. (D27; `trg_item_batches_version`)

#### Scenario: Changing the expiry without a version bump is refused
- **GIVEN** batch MAY-24 at version 1
- **WHEN** its expiry date is set to 2027-01-01 with version still 1
- **THEN** the write is refused as a batch version not bumped
- **AND** the same edit at version 2 succeeds
- **AND** Covered by check-invariants: "batch edit without a version bump"

### Requirement: A batch's item, store and book never change
The system SHALL keep the item, store and book of a batch fixed for its life, so moving stock between books is a transfer and never an edit of the batch. (D28; `trg_item_batches_version`)

#### Scenario: Re-homing a batch to the Godown is refused
- **GIVEN** oil batch OIL-B in the Main book
- **WHEN** an update moves it to the Godown book, version bumped
- **THEN** the write is refused as a batch version not bumped
- **AND** the batch stays in the Main book
- **AND** Covered by check-invariants: "move a batch to another book"

### Requirement: A batch is retired, never deleted
The system SHALL stop offering a batch for sale by setting its retirement time, and MUST NOT delete a batch row, because bill lines name the batch they sold. (D27; `trg_item_batches_no_delete`)

#### Scenario: Deleting a batch is refused
- **GIVEN** the April soap batch with bill history
- **WHEN** a delete of that batch is attempted
- **THEN** the delete is refused as a retire-only operation
- **AND** the batch disappears from the batch choice once retired
- **AND** Covered by check-invariants: "delete a batch"

### Requirement: A batch price change is a new price version for that batch
The system SHALL close every open price row of that batch and insert its whole new tier set at the next price version of the item in one write, leaving other batches' open prices untouched. (D27, D26; `trg_item_prices_revision_coherent`, `trg_item_prices_version_scope`)

#### Scenario: Repricing May leaves April open
- **GIVEN** April batch prices at version 1 and May batch prices at version 2, both open
- **WHEN** May is repriced to ₹220 at version 3
- **THEN** May's version 2 rows are closed and version 3 rows are open
- **AND** April's version 1 rows stay open
- **AND** a May row written at version 3 with a different start time from its siblings is refused

### Requirement: The billed batch is copied onto the immutable bill line
The system SHALL copy the batch id, batch number and MRP onto the bill line when the line is sold, and those copied values MUST NOT change afterwards. (D27; 0008 `bill_lines.batch_id`, `batch_no`, `mrp_minor`)

#### Scenario: A soap bill shows the batch the customer got
- **GIVEN** a bill line for one bar of soap sold from APR-24 at MRP ₹100
- **WHEN** the bill is stored
- **THEN** the line carries APR-24 and ₹100
- **AND** a later MRP edit on the batch leaves that line unchanged
