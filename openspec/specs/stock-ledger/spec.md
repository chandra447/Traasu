# Stock Ledger Specification

## Purpose
Defines the append-only record of every stock change and the per-batch running balance kept from it, so on-hand
is an auditable estimate that never stands between a cashier and a sale.

## Requirements

### Requirement: Every stock change is one appended movement with a reason
The system SHALL record each stock change as one appended movement carrying a direction, a base quantity, a batch, a store and a reason from receipt, adjustment, sale, void or repack. (D27; 0008 `inventory_movements.reason`)

#### Scenario: A sale and a receipt are different reasons
- **GIVEN** soap batch APR-24 holding 10 bars
- **WHEN** the shelf is restocked by 2 and a bar is billed
- **THEN** two movements exist, one in at reason receipt and one out at reason sale
- **AND** the batch's on-hand is 11

### Requirement: batch_stock is the running balance kept from movements
The system SHALL keep each batch's on-hand as a balance that moves when a movement is appended, and MUST NOT recompute or rewrite it from a later adjustment of history. (D27; `trg_movements_batch_stock`)

#### Scenario: An out movement lowers the balance
- **GIVEN** batch MAY-24 at 24 bars
- **WHEN** an out movement of 24 is appended
- **THEN** its on-hand is 0

### Requirement: On-hand may go below zero
The system SHALL allow a batch's on-hand to become negative and MUST NOT refuse a movement that would take it below zero, because an offline register cannot know the true count. (D27; D28; `batch_stock.on_hand_qty_base`)

#### Scenario: Two packs sold past zero offline
- **GIVEN** batch MAY-24 counted down to 0
- **WHEN** an offline register sells 2 more bars from it
- **THEN** both sale movements are accepted
- **AND** its on-hand is −2
- **AND** Covered by check-invariants: "sell past zero offline"

### Requirement: Stock never blocks or splits a sale
The system SHALL accept a billed line whatever the batch's on-hand is, and MUST NOT split one line across batches or hold the sale for a stock check. (D27; D28 "one line stays one batch, one price")

#### Scenario: A line larger than the batch's count
- **GIVEN** batch APR-24 with 5 bars on hand
- **WHEN** a line for 8 bars is billed from it
- **THEN** the sale is accepted from that one batch
- **AND** the register warns that the quantity exceeds the on-hand
- **AND** its on-hand becomes −3

### Requirement: A billed line appends exactly one out movement
The system SHALL write one sale movement for each billed line that names a stock batch, and MUST NOT append a second sale movement for the same line when a push is retried. (D27; `d1-schema.md` invariant "`bill_lines` one per line")
<!-- OPEN parity-billing #B / parity-stock #F4: no unique index on (bill_id, bill_line_no), so a retried push can take stock twice and the bill then cannot be voided. -->

#### Scenario: A replayed bill takes stock once
- **GIVEN** a bill for one bar of soap already stored with its sale movement
- **WHEN** the same bill is pushed again with the same id
- **THEN** the bill insert changes nothing
- **AND** the batch's on-hand is unchanged by the replay

### Requirement: A void puts the goods back
The system SHALL append one in movement per sale movement of a voided bill, at the void's time and with the void as its actor, so the balance returns without editing history. (D27; `trg_bill_voids_restock`)

#### Scenario: Voiding a soap bill restores the bar
- **GIVEN** soap batch APR-24 at 5 bars after a sale of one bar on bill bsoap
- **WHEN** bsoap is voided
- **THEN** an in movement of 1 is appended for that batch
- **AND** its on-hand is 6
- **AND** Covered by check-invariants: "void puts the goods back"

### Requirement: An adjustment changes quantity by a delta, never by overwrite
The system SHALL change a batch's quantity only by appending an adjustment movement with a signed quantity, a reason and a note, and MUST NOT overwrite the stored quantity. (D27; `api-routes.md` PATCH `/v1/admin/batches/:id/adjustments`)

#### Scenario: Writing off two damaged packs
- **GIVEN** batch APR-24 at 10 bars
- **WHEN** the owner records an adjustment of −2 for damage
- **THEN** one adjustment movement of 2 out exists with its note
- **AND** the batch's on-hand is 8

### Requirement: A stock count enters the counted number and writes the difference
The system SHALL accept the counted quantity from a physical count and write only the difference between the counted number and the current balance as one adjustment. (D27; `api-routes.md` PATCH `/v1/admin/batches/:id/adjustments`)
<!-- OPEN parity-stock #F8: the counted number is not stored anywhere, so the count cannot be audited or told apart from a damage write-off. -->

#### Scenario: Counting the shelf finds two missing
- **GIVEN** the book says 10 bars and the staff count 8
- **WHEN** the count of 8 is entered
- **THEN** one adjustment of 2 out is appended
- **AND** the batch's on-hand is 8

### Requirement: A movement without a known batch leaves balances alone
The system SHALL append a movement that names a batch only when that batch exists, and MUST NOT create a balance row or change a balance for a movement whose batch is unknown. (D27; `trg_movements_batch_stock` conditional)

#### Scenario: A line for an item priced without a batch
- **GIVEN** loose rice sold at an item-level price with no batch on the line
- **WHEN** the sale movement is appended with no batch
- **THEN** the movement records the sale for the ledger
- **AND** no batch balance changes

### Requirement: Movements are append-only
The system SHALL keep stock movements append-only, and MUST NOT update or delete one outside the archival cascade that removes archived bills. (D27; D22 cost "archival deletes old sale movements by cascade")

#### Scenario: Archival removes the sale movement but keeps the balance
- **GIVEN** batch APR-24 at 6 bars, with the archived bill's sale movement still present
- **WHEN** archival deletes that bill
- **THEN** the bill's sale movement is removed by cascade
- **AND** the batch's on-hand is still 6
- **AND** Covered by check-invariants: "archival does not change stock"

### Requirement: A repack's movements come from the repack, not from a route
The system SHALL let the repack record produce its own movements through the schema, so an unpack always reverses the same quantities the pack wrote, and MUST NOT depend on the repack route writing them. (D30; `trg_repack_lines_move`)

#### Scenario: A pack line writes both sides
- **GIVEN** a rice sack of 25,000 g and a repack line of 20 packs of 1,000 g from it
- **WHEN** the line is stored
- **THEN** 20,000 g leave the sack and 20 packs enter the new pack batch
- **AND** the sack's balance is 5,000 g before any conversion loss
- **AND** Covered by check-invariants: "rice ledger nets to the movements"
