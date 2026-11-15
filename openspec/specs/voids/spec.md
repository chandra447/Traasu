# Voids Specification

## Purpose
Records the one correction a register may make to a saved bill: a void that names who did it and why, subtracts the
sale from the reports and puts the stock back. The bill itself is never edited, so the shop keeps both what it sold
and the fact that it was cancelled.

## Requirements

### Requirement: A void is a separate record and the bill stays untouched
The system SHALL record a correction as a new void row naming the bill, and SHALL never change a bill's money, its
lines or its business date, so the original sale and its cancellation both survive. (D15; bill_voids, 0004
trg_lines_frozen)

#### Scenario: A bill cancelled at the counter
- **GIVEN** a stored bill of 6500 paise with one 1500 g tamarind line and the cashier cancels it
- **WHEN** the correction is recorded
- **THEN** a void row exists for the bill and the bill's total is still 6500 paise
- **AND** its line is unchanged

### Requirement: A bill is voided at most once
The system SHALL allow one void per bill, and SHALL answer a repeat with the stored void and `duplicate` true
instead of writing a second one or failing. (D15; 0003 uq_bill_voids_bill, api-routes §2 void route)

#### Scenario: The void retried after a lost response
- **GIVEN** a void for bill `…-6e7f` was recorded at 18:02
- **WHEN** the register retries the same void
- **THEN** the response carries the original void time, the actor and `duplicate` true
- **AND** no second void row exists and the report totals do not move again

### Requirement: A void names its actor and a reason
The system SHALL record the user who voided the bill and a reason of at least one and at most 200 characters, and
SHALL refuse a void with a blank reason or without an actor. (D15; 0003 bill_voids, 0004 trg_bill_voids_actor)

#### Scenario: A void with a whitespace reason
- **GIVEN** a void whose reason field is `"   "`
- **WHEN** the row is written
- **THEN** it is refused
- **AND** the same void with the reason `customer left without the bag` and a cashier id is accepted

### Requirement: A bill cannot arrive voided and a void cannot be undone
The system SHALL refuse a pushed bill that is already marked voided, and SHALL refuse any attempt to clear or
change a void once recorded. (0008; trg_bills_insert_unvoided, trg_bills_void_once)

#### Scenario: Clearing the flag
- **GIVEN** a bill voided at 18:02
- **WHEN** an update tries to set `voided` back to 0
- **THEN** it is refused
- **AND** Covered by check-invariants: "un-void"

#### Scenario: A bill pushed as already voided
- **GIVEN** a pushed bill arrives with its voided flag set
- **WHEN** the header insert runs
- **THEN** it is refused, because a void travels as its own record
- **AND** Covered by check-invariants: "bill inserted as voided"

### Requirement: A void belongs to the bill's own store
The system SHALL refuse a void whose store differs from the store of the bill it names. (0008; trg_bill_voids_same_store)

#### Scenario: A void from another store
- **GIVEN** the bill was written by the Kukatpally store
- **WHEN** a void arrives claiming another store id
- **THEN** it is refused
- **AND** Covered by check-invariants: "void from another store"

### Requirement: The void flips the bill and reduces the daily rollup in one statement
The system SHALL set the bill's voided flag, void time, actor and reason, and SHALL subtract the bill's quantities
and amounts from the daily rollup, as the consequence of the void insert itself. (D25; 0008 trg_bill_voids_apply)

#### Scenario: Voiding a 1029 paise line
- **GIVEN** the day's rollup for the item reads 1 piece and 1029 paise from one bill
- **WHEN** that bill is voided
- **THEN** the rollup reads 0 pieces and 0 paise and the bill's count drops by one
- **AND** the bill carries the actor and the reason of the void
- **AND** Covered by check-invariants: "void flips the bill flag"
- **AND** Covered by check-invariants: "void decrements the rollup"

### Requirement: A void puts the goods back on the batch they left
The system SHALL write one inbound stock movement per outbound movement of the voided bill, against the same batch,
and SHALL do it as part of the void insert. (D27, D29; 0008 trg_bill_voids_restock)

#### Scenario: One pack of soap returned
- **GIVEN** a soap batch at MRP ₹100 read 5 on hand, and one pack was sold, leaving 4
- **WHEN** the bill is voided
- **THEN** the batch reads 5 again
- **AND** Covered by check-invariants: "void puts the goods back"

#### Scenario: A child sale voided
- **GIVEN** one Coffee 1 kg was sold, taking 1000 g from the godown sack, leaving 4000 g
- **WHEN** the bill is voided
- **THEN** the sack reads 5000 g again
- **AND** no stock returns to the child item, which holds none
- **AND** Covered by check-invariants: "void the child sale: the 1000 g go back to the sack"

### Requirement: A void for a bill the server does not hold is parked, never dropped
The system SHALL answer `bill_not_found` when no bill exists and nothing has been archived, and `bill_archived`
when the bill was received but has since been archived, and the register SHALL keep both in a parked state rather
than retrying or discarding them. (D22; schema_meta.archive_before, api-routes §3.4)

#### Scenario: A void for a bill archived to the archive store
- **GIVEN** `archive_before` is 2026-10-01 and the register reports a void for a bill dated 2026-09-14
- **WHEN** the void is pushed
- **THEN** the reply names `bill_archived` and the void is not stored in the bill table
- **AND** the register keeps the void parked so the correction is not lost

<!-- OPEN parity-platform #F5: there is no server-side parking table and bill_voids cannot reference a deleted bill, so an archived bill's void has nowhere to be recorded and its rollup cannot be corrected. -->

### Requirement: A void arrives by push or by the dedicated route and lands in one record
The system SHALL accept a void either as an entry of a bill push or through the single-bill void route, and SHALL
make both paths write the same record and the same consequences. (api-routes §9.13; bill_voids)

#### Scenario: An online correction at the counter
- **GIVEN** the register is online and the cashier voids a bill from the day's list
- **WHEN** the void route is called
- **THEN** one void row exists and the bill, the rollup and the batch reflect it
- **AND** no second route-specific record or counter is written

### Requirement: A void raises a flag and is never refused for policy
The system SHALL record a void that looks like a policy breach and report it in the push reply's flagged counts,
because the goods and the money already moved and a refusal would lose the correction. (api-routes §9.2, §3.4
flagged counts)

#### Scenario: A void outside any approval
- **GIVEN** the store has no void approval configured and a cashier voids a 6500 paise bill
- **WHEN** the void is pushed
- **THEN** the void is recorded and the bill's totals are reduced
- **AND** the reply counts it under flagged policy violations rather than rejecting it

<!-- OPEN parity-platform #F6: nothing gates a void at all — no grant column on bill_voids and no store setting — so the largest money action at a register has no approval behind it. -->

### Requirement: A voided bill is out of the day's sales and out of the multi-day rollup
The system SHALL exclude a voided bill from the single-day report that reads bill lines, and SHALL rely on the
already-reduced rollup for multi-day ranges so that no report needs a voided filter on the rollup path. (D10, D13;
api-routes §3.6, sales_daily)

#### Scenario: The month report after a void
- **GIVEN** the November rollup held 6500 paise for tamarind and one 6500 paise bill is voided on 20 November
- **WHEN** the month report for November is read
- **THEN** the tamarind amount is 0 paise with no filter on the voided flag
- **AND** the single-day report for the voided bill's date excludes it as well
