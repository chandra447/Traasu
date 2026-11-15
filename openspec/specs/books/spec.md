# Books Specification

## Purpose
Defines the stock locations inside a store, their priority order and the Main book every store gets, so stock
can be kept in more than one place without changing what a customer is charged.

## Requirements

### Requirement: Every store has books, one per priority
The system SHALL hold a store's stock locations as books with a name and a priority, SHALL allow at most one active book per priority in a store, and SHALL provision a book named "Main" with priority 1 for every store. (D28; 0008 `books`, `uq_books_priority`)

#### Scenario: A new store gets a Main book
- **GIVEN** a store provisioned with no stock locations
- **WHEN** provisioning finishes
- **THEN** the store has one active book "Main" at priority 1
- **AND** a second active book at priority 1 in that store is refused

### Requirement: Book names are unique within a store
The system SHALL refuse a new book whose name matches an existing book in the same store, treating names as the merchant typed them. (D28; `uq_books_name`)

#### Scenario: A second Godown is refused
- **GIVEN** store s1 with books Main and Godown
- **WHEN** a book named "Godown" is created in s1
- **THEN** the write is refused as a duplicate book name
- **AND** another store may still create its own "Godown"

### Requirement: A batch lives in one book of its own store
The system SHALL require every batch to name a book belonging to the batch's store, and MUST NOT accept a batch whose book belongs to another store. (D28; `trg_item_batches_book_store`)

#### Scenario: A batch in another store's book is refused
- **GIVEN** store s2 and a book of store s1
- **WHEN** a batch for store s2 names that s1 book
- **THEN** the write is refused as a book store mismatch
- **AND** Covered by check-invariants: "batch in a book of another store"

### Requirement: Book priority orders batches at the same MRP and price
The system SHALL take stock from the batch in the book with the lowest priority number among batches at the same MRP and selling price, and within one book SHALL prefer the most recently received batch. (D28; `query-catalogue.md` §3)

#### Scenario: Book 1 drains before book 2
- **GIVEN** dal at MRP ₹100 and ₹95 in Main (book 1, 4 packs) and in the Godown (book 2, 10 packs), the Godown batch received later
- **WHEN** the cashier scans dal
- **THEN** the line uses the Main batch
- **AND** no prompt is shown, because the MRP and price are the same
- **AND** Covered by check-invariants: "same price in both books: book 1 first, no prompt"

### Requirement: With no stock at the chosen price, book 1's batch goes negative
The system SHALL bill the batch in the lowest-priority book even when no batch at that MRP and price has stock, and its on-hand count SHALL be allowed to fall below zero. (D28; `batch_stock.on_hand_qty_base`)

#### Scenario: Both books sold out
- **GIVEN** dal in Main and the Godown at ₹95, both counted down to zero
- **WHEN** one more pack is billed
- **THEN** the line uses the Main batch
- **AND** that batch's on-hand becomes −1
- **AND** Covered by check-invariants: "both sold out: back to book 1, which goes negative"

### Requirement: Reordering books goes through a free priority
The system SHALL reorder two books by parking one on an unused priority, moving the other, then setting the parked book's final priority, all in one atomic write, and MUST NOT rely on a single-statement swap. (D28; `uq_books_priority`)

#### Scenario: Swapping Main and Godown
- **GIVEN** Main at priority 1 and Godown at priority 2 in store s1
- **WHEN** one statement sets Main to 2 and Godown to 1
- **THEN** the write is refused by the unique priority rule
- **AND** the three-step move through priority 3 succeeds
- **AND** Covered by check-invariants: "swap via a free priority (one db.batch)"
<!-- OPEN parity-stock #F2: a register receives book rows one at a time and the same unique priority index refuses every order of a swap, so its catalog pull freezes. -->

### Requirement: A deactivated book leaves the lookup and keeps its stock
The system SHALL drop a deactivated book's batches out of the register's batch choice and prompt while keeping their recorded stock, and SHALL restore them when the book is reactivated. (D28; `books.active`)

#### Scenario: Deactivating the Godown
- **GIVEN** the Godown book holding 10 packs of dal, and Main holding 4
- **WHEN** the Godown is deactivated
- **THEN** the register offers only batches in active books
- **AND** the Godown's 10 packs stay recorded and reappear when it is reactivated

### Requirement: A book is deactivated, never deleted
The system SHALL stop using a book by deactivating it and MUST NOT delete a book row, because batches name it. (D28; `trg_books_no_delete`)

#### Scenario: Deleting the Godown is refused
- **GIVEN** store s1 with a Godown book holding batches
- **WHEN** a delete of the Godown is attempted
- **THEN** the delete is refused as a deactivate-only operation
- **AND** Covered by check-invariants: "delete a book"

### Requirement: A book change bumps its version
The system SHALL require the book version to increase on every change to its name, priority or active flag, and SHALL treat an equal version with equal content as a replay. (D28; `trg_books_version`)

#### Scenario: Renaming without a version bump
- **GIVEN** the Godown book at version 1
- **WHEN** its name is changed to "Back Room" with version still 1
- **THEN** the write is refused as a book version not bumped
- **AND** the same rename at version 2 succeeds

### Requirement: A batch's book is fixed for its life
The system SHALL treat a change of a batch's book as a stock transfer between books and not as an edit, so no update may re-home a batch. (D28; `trg_item_batches_version`)

#### Scenario: Moving stock between books is not an edit
- **GIVEN** a batch of 5 packs in the Godown
- **WHEN** an admin edit attempts to set the batch's book to Main
- **THEN** the write is refused
- **AND** the goods stay in the Godown until a transfer moves them
