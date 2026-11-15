# Bill Push Specification

## Purpose
Defines how a register hands its completed bills to the server: batches of bills, one transaction per bill, an
order that lets the header be checked against lines that already exist, and a reply that says which bills were
accepted. Every rule here exists so a lost response, a replay or one bad bill can never duplicate a sale, lose one,
or double-count a report.

## Requirements

### Requirement: A push carries at most 100 bills and 1000 lines
The system SHALL accept at most 100 bills and 1000 lines in one request, and SHALL refuse a larger envelope as a
whole with `413 batch_too_large` so the register splits it. (D12; api-routes §3.4, §6)

#### Scenario: A register with a day's backlog
- **GIVEN** the outbox holds 340 unsynced bills
- **WHEN** the register sends a request with 101 bills
- **THEN** the request is refused as a whole and nothing is written
- **AND** the register sends four requests of at most 100 bills instead

### Requirement: Each bill is its own transaction
The system SHALL write each bill in its own batch of statements, so one malformed bill is rejected on its own and
never rolls back the bills beside it. (D12; api-routes §3.4)

#### Scenario: One bad bill among ninety-nine good ones
- **GIVEN** a request of 100 bills where bill 57 claims a total of 1 paise for a 1029 paise line
- **WHEN** the request is applied
- **THEN** 99 bills are accepted and stored
- **AND** bill 57 alone is listed in `rejected` with `invalid_totals`

### Requirement: One request applies sessions and shifts before bills and voids after them
The system SHALL apply staff sessions in session-start order, then shifts, then bills, then voids, so a void can
name a bill delivered in the same request and a bill can name a session delivered beside it. (api-routes §3.4;
staff_sessions, shifts, bills, bill_voids)

#### Scenario: A void for a bill in the same push
- **GIVEN** one request carrying 3 bills and a void for the second of them
- **WHEN** the request is applied
- **THEN** the void is recorded against the bill already inserted in that request
- **AND** the bill's rollup rows are reduced in the same pass

### Requirement: Inside one bill, lines and movements are written before the header
The system SHALL open each bill's batch with deferred foreign keys, then insert the lines, then the stock
movements, and then the header last, so the header's checks can read the lines it belongs to. (D12, D23;
PRAGMA defer_foreign_keys, api-routes §9.9)

#### Scenario: The header's trigger sees its own lines
- **GIVEN** a bill of two lines totalling 6500 and 3000 paise
- **WHEN** the bill's batch runs
- **THEN** the header insert sees both lines and is accepted
- **AND** the rollup rows are written from those lines in the same statement

### Requirement: A replayed bill inserts nothing and fires no trigger
The system SHALL insert the header with a do-nothing conflict clause on the bill id, so a repeat push writes no
money, adds no rollup and needs no compensation. (D12, D13; api-routes §3.4)

#### Scenario: The response to a push is lost
- **GIVEN** a bill was accepted and its response never reached the register
- **WHEN** the register pushes the same bill again
- **THEN** the duplicate is acknowledged as accepted with `duplicate` true
- **AND** the day's rollup figure is unchanged by the replay

### Requirement: A duplicate id is accepted only with the same content
The system SHALL accept a bill whose id exists when its stored payload hash matches, and SHALL otherwise reject it
`id_conflict` without overwriting the stored bill. (api-routes §3.4; 0002 bills.payload_sha256)

#### Scenario: A retried bill whose content changed
- **GIVEN** bill `0192f3d1-…-6e7f` was stored with `payload_sha256` `b0e1…`
- **WHEN** a push presents the same id with a different hash
- **THEN** the bill is rejected `id_conflict`
- **AND** the stored bill and its lines are untouched

### Requirement: A register-level identifier that repeats is refused
The system SHALL reject a bill whose store, terminal and local sequence already belong to a different bill id as
`duplicate_seq`, and a bill whose display number is already attached to another bill from the same store as
`display_no_conflict`, so the numbers the shop relies on identify one sale each. (0001 uq_bills_store_terminal_seq;
api-routes §3.4)

#### Scenario: A cloned register reuses sequence 431
- **GIVEN** terminal 2 of the Kukatpally store already has `local_seq` 431 stored under bill `…-6e7f`
- **WHEN** a different bill id arrives claiming `local_seq` 431 for the same terminal
- **THEN** it is rejected `duplicate_seq`
- **AND** the register parks it for the owner instead of retrying

#### Scenario: Two bills print the same display number
- **GIVEN** `S1-T2-000431` is stored for one bill of the store
- **WHEN** another bill id arrives carrying `S1-T2-000431`
- **THEN** it is rejected `display_no_conflict`
- **AND** the register's numbering is reported as needing attention

<!-- OPEN parity-billing #P: a re-installed register restarts local_seq at 1 and every bill it writes is then refused duplicate_seq forever. -->

### Requirement: The server identifies a line's price version by key, not by clock
The system SHALL find each line's tier set by item, price scope and price version, and SHALL flag `stale_price`
when that version was closed before the bill's creation time, or `price_mismatch` when the version is unknown or the
claimed list price differs from the version's rows. (D26; item_prices, bill_lines.stale_price, price_mismatch)

#### Scenario: A register that had not synced the price change
- **GIVEN** the register billed tamarind from price version 8, which the server closed at 18:00
- **WHEN** the bill, created at 17:50, is pushed at 21:00
- **THEN** the version is found and was open at 17:50, so no flag is set
- **AND** a bill created at 18:10 from the same version is flagged `stale_price`

#### Scenario: A version the server never issued
- **GIVEN** a line claims `price_version` 41 while the item's versions stop at 12
- **WHEN** the bill is pushed
- **THEN** the line is flagged `price_mismatch`
- **AND** `computed_list_price_minor` records what the server's prices would have charged at that time

### Requirement: A price flag never changes money and never rejects a bill
The system SHALL store the price comparison as a diagnostic only, keep the charged amount as the register reported
it, and count flagged bills in the response rather than rejecting them. (D2, D26; bill_lines.computed_list_price_minor,
api-routes §3.4 flagged counts)

#### Scenario: A bill with one stale line
- **GIVEN** a bill of two lines, one priced from a version closed before the sale
- **WHEN** the push is handled
- **THEN** the bill is accepted and both line totals are stored as charged
- **AND** the response reports `stale_price` 1 so the owner can see the gap

### Requirement: The business date is validated against the bill's own cutover
The system SHALL accept a bill only when its `business_date` equals the business day derived from its `created_at`
and the `business_day_start_minutes` it carries, and SHALL refuse a `created_at` later than the server's receipt
time. (D11 revised, D25 D42; bills.business_day_start_minutes)

#### Scenario: A 04:00 cutover and a sale at 03:40
- **GIVEN** the store's business day starts at 04:00 and the register billed at 03:40 on 15 November, recording
  `business_date` 2026-11-14
- **WHEN** the bill is pushed at 09:00 on 15 November
- **THEN** the bill is accepted, because the date follows from its own cutover
- **AND** the same bill claiming `business_date` 2026-11-15 is rejected `invalid_bill`

### Requirement: Every bill records when the server received it
The system SHALL stamp `received_at` on every bill it accepts, so an owner can tell a closed day from a day a
terminal has not synced yet. (D11; 0002 idx_bills_received_at, api-routes §3.6 provisional_until)

#### Scenario: A day that is not yet final
- **GIVEN** 600 bills for 2026-11-14 are stored and one register has not pushed since 14:00
- **WHEN** the day report for 2026-11-14 is read
- **THEN** the newest `received_at` is reported as `provisional_until`
- **AND** the owner can see the day is still moving

### Requirement: The daily rollup is written by the database, never by the push handler
The system SHALL let the trigger on the bill insert maintain the daily sales rollup, so a duplicate or concurrent
push cannot add to it twice and no second code path can skew it. (D13; 0008 trg_bills_rollup)

#### Scenario: Two invocations push the same bill
- **GIVEN** the same bill is pushed by two retries that overlap
- **WHEN** both are handled
- **THEN** exactly one bill row exists and the rollup gained its amounts once
- **AND** the second response reports the bill as a duplicate

### Requirement: Each pushed sale line writes its stock movement in the same batch
The system SHALL write one outbound stock movement per sale line inside that bill's batch, carrying the batch and
store the line names, or the parent item, batch and quantity a child line names. (D27, D29; inventory_movements,
0008 trg_movements_child_line)

#### Scenario: A child line moves the sack, not the pack
- **GIVEN** a bill line for one Coffee 1 kg naming the parent sack batch and 1000 g
- **WHEN** the bill's batch runs
- **THEN** one outbound movement of 1000 g is written against the sack's batch
- **AND** a movement written against the child item instead is refused

<!-- OPEN parity-billing #B: inventory_movements has no unique key on (bill_id, bill_line_no), so a retry with a fresh movement id takes the stock out twice and the void then cannot be written at all. -->

### Requirement: A bill takes its store, terminal and device from the credential
The system SHALL take `store_id`, `terminal_id` and `device_id` for a pushed bill from the device's token, and SHALL
ignore the same fields in the request body. (api-routes §1, §3.4)

#### Scenario: A body that names another store
- **GIVEN** a device enrolled to terminal 2 of Kukatpally
- **WHEN** a pushed bill carries a `store_id` for another store
- **THEN** the bill is stored against Kukatpally
- **AND** the body value is ignored

### Requirement: The reply names the accepted bills so the register can mark them synced
The system SHALL answer a valid envelope with HTTP 200 whatever the per-bill verdicts, listing each accepted bill
with its invoice number and each rejected bill with a code, and the register SHALL mark only the accepted bills as
synced. (D12; api-routes §3.4, query-catalogue §6)

#### Scenario: A push with one rejected bill
- **GIVEN** a request of 40 bills where one is rejected `duplicate_seq`
- **WHEN** the response is processed
- **THEN** 39 bills are marked synced and leave the outbox
- **AND** the rejected bill stays in the outbox, parked for the owner rather than retried in a loop
