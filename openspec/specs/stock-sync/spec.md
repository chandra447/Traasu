# Stock Sync Specification

## Purpose
Defines how a register learns on-hand from the server by a cursor and keeps its own estimate, so a sale is
priced and completed even when other registers are selling the same batch at the same time.

## Requirements

### Requirement: The register pulls changed batch balances by cursor
The system SHALL answer a register's stock pull with the store's batch balances changed after the supplied cursor, each row carrying the batch, its on-hand quantity and its sequence number, in pages with a lookahead flag. (D27; `GET /v1/stock`; `query-catalogue.md` §3)

#### Scenario: Two batches changed since the last pull
- **GIVEN** a register whose cursor is at stock sequence 40, and receipts that moved soap batches to sequences 41 and 42
- **WHEN** the register pulls stock since sequence 40
- **THEN** two rows come back, one per batch, with their current on-hand and their sequences
- **AND** the response's cursor is the last sequence returned

### Requirement: Stock changes are kept out of the catalog journal
The system SHALL track stock changes in their own sequence allocator and MUST NOT append a catalog change row for a sale movement or a balance change. (D27; 0008 `stock_seq`)

#### Scenario: A sale does not grow the catalog delta
- **GIVEN** a register that has caught up on the catalog
- **WHEN** another register pushes 20 sales
- **THEN** the catalog delta for the caught-up register holds no rows
- **AND** the stock pull reports the affected batches

### Requirement: A stock cursor carries the database epoch
The system SHALL include a database epoch in the stock cursor and SHALL answer a pull whose epoch no longer matches with a stale-cursor refusal that requires a full stock refresh. (D27; `GET /v1/stock`; `api-routes.md` §4 epoch)

#### Scenario: A restore resets stock as well as the catalog
- **GIVEN** a register holding stock cursor epoch 1
- **WHEN** a restore moves the database to epoch 2
- **THEN** the register's next stock pull is refused as stale
- **AND** the register discards its cached balances and does a full refresh

### Requirement: A register's on-hand is its own estimate
The system SHALL let a register compute a batch's on-hand as the server value minus that register's own unsynced sales of that batch, so its pending bills are not counted twice. (D27; `query-catalogue.md` §3)

#### Scenario: Three unsynced bars
- **GIVEN** the pulled server balance is 10 bars, and this register has 3 unsynced bars of the same batch
- **WHEN** the cashier scans soap
- **THEN** the register shows 7 as the estimate
- **AND** a fourth sale is still accepted

### Requirement: Another register's sales appear on the next pull
The system SHALL make one register's sale visible to another only as a changed balance on that register's next stock pull, and MUST NOT treat the local estimate as the shared truth. (D27 "Other registers' sales appear on the next pull")

#### Scenario: Two registers, one batch
- **GIVEN** register A and register B both showing 5 packs of the last batch
- **WHEN** A sells 3 and syncs, then B pulls stock
- **THEN** B's estimate for that batch becomes 2 unless B has unsynced sales of it

### Requirement: Admin stock changes reach registers through the pull
The system SHALL deliver stock receipts, adjustments and repacks to registers as changed batch balances on the stock pull, because the server holds the stock truth and the admin plane writes it. (D27; D28 "purchase entry"; D30 "where")

#### Scenario: A receipt becomes visible without a catalog change
- **GIVEN** an admin receipt adding 24 bars to a new batch MAY-24
- **WHEN** a register pulls stock after its receipt
- **THEN** MAY-24 appears with 24 on hand
- **AND** the register shows the same MRP and price for it as the catalog reports

### Requirement: The stock pull is store-scoped
The system SHALL return only balances for the batches of the store named by the register's device token, and MUST NOT ship another store's on-hand. (D9; `GET /v1/stock` store scope)

#### Scenario: A register in store s1
- **GIVEN** soap batches in store s1 and in store s2
- **WHEN** a register in s1 pulls stock
- **THEN** every returned batch belongs to s1
- **AND** no s2 batch appears

### Requirement: Batches and books reach a register through the catalog
The system SHALL deliver a register every batch and book it needs through the catalog, as batch and book documents alongside items and prices, so a register bootstrapped from a snapshot can bill a batched item. (D27; D28 "one more table to sync (`book` catalog entity)"; `catalog_changes.entity`)
<!-- OPEN parity-stock #F1 (S23) / parity-repack #F1: the snapshot's pages and the delta's entity list omit batches and books, so a fresh register has no batch to price. -->

#### Scenario: A replacement register bills soap on day one
- **GIVEN** a newly enrolled register with an empty catalog
- **WHEN** its catalog snapshot completes
- **THEN** it holds the store's books and the live batches with their MRPs, expiries and prices
- **AND** scanning soap returns the latest batch and a price

### Requirement: A quantity above the batch's on-hand only warns
The system SHALL warn the cashier when a line's quantity exceeds the chosen batch's estimated on-hand, and MUST NOT block, split or re-price the line. (D28; `query-catalogue.md` §3)

#### Scenario: Selling 8 packs with 5 estimated
- **GIVEN** a batch estimated at 5 packs on this register
- **WHEN** the cashier adds 8 packs
- **THEN** the screen warns that the quantity exceeds the estimate
- **AND** the line is billed from that batch for all 8
- **AND** the batch's movement is one line of 8, not two lines
