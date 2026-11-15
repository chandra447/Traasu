# Numbering Specification

## Purpose
Defines the two number series a bill carries: the register's own display number, printed for the customer even
with no network, and the store's legal invoice number, assigned by the server when the bill arrives. It fixes who
owns each counter, what a series is made of, and what happens when a number repeats.

## Requirements

### Requirement: A store holds one policy per scope and register
The system SHALL keep one numbering policy row per store and scope, with the store scope carrying no terminal and
the register scope carrying exactly one terminal. (0001 numbering_policy; uq_numbering_policy_scope, CHECK
(scope = 'register') = (terminal_id IS NOT NULL))

#### Scenario: A store with two registers
- **GIVEN** the Kukatpally store has terminals 1 and 2 and needs the legal store series as well
- **WHEN** provisioning completes
- **THEN** three policy rows exist: one for the store and one per register
- **AND** a second store-scope row for the same store is refused

### Requirement: A series is a prefix, a padded counter and a reset cadence
The system SHALL store each series as a prefix, a padding between 1 and 12 digits, a reset cadence of never, daily,
monthly or yearly, a next value of at least 1 and the date of its last reset. (0001 numbering_policy)

#### Scenario: The Kukatpally store series
- **GIVEN** the store policy has prefix `KUK/2026/` and padding 6 and its next value is 431
- **WHEN** a bill is numbered from it
- **THEN** the number is `KUK/2026/000431`
- **AND** the counter now reads 432

### Requirement: Each series has exactly one owner
The system SHALL advance the store series from the server only and the register series from its own terminal only,
so neither side ever moves the other's counter. (0001 numbering_policy comment; d1-schema.md numbering_policy invariant)

#### Scenario: A register that has been offline for a day
- **GIVEN** terminal 2 has issued display numbers up to `S1-T2-000431`
- **WHEN** its bills reach the server and the server assigns invoice numbers
- **THEN** the server's store counter advances and terminal 2's register counter does not move
- **AND** no response from the server can change the number terminal 2 will print next

### Requirement: The register allocates its display number at the moment of the sale
The system SHALL allocate a bill's `display_no` from the register's own series when the bill is saved, with no
server involvement, so a bill printed during an outage carries a number from the shop's own series. (0001
numbering_policy register scope; api-routes §9.6)

#### Scenario: Numbering through a two-day outage
- **GIVEN** terminal 2's register counter is at 431 and the uplink is down for two days
- **WHEN** 380 bills are saved
- **THEN** they carry `S1-T2-000432` through `S1-T2-000811` in order
- **AND** no sale waits for a number from anywhere else

### Requirement: The server assigns the invoice number from the store series
The system SHALL assign `invoice_no` to a bill from the store's series when the bill is accepted, and SHALL return
it in the acceptance so the register can print or reprint the legal number. (api-routes §9.6, Q11; numbering_policy
store scope, bills.invoice_no)

#### Scenario: Forty bills accepted in one push
- **GIVEN** the store counter's next value is 431 when a request of 40 bills arrives
- **WHEN** the bills are accepted
- **THEN** they carry `KUK/2026/000431` to `KUK/2026/000470` and the counter reads 471
- **AND** the acceptance for each bill names its invoice number

#### Scenario: A bill that cannot be stored takes no number
- **GIVEN** a request where bill 3 is rejected `duplicate_seq`
- **WHEN** the counter is advanced for the accepted bills
- **THEN** the rejected bill has no invoice number
- **AND** no number is left unused between the accepted bills of that request

<!-- OPEN parity-billing #A: the counter is advanced in the same batch as the insert, so a concurrent duplicate that inserts nothing still burns an invoice number. -->

### Requirement: The invoice number is unique per store and assigned only once
The system SHALL enforce uniqueness of `invoice_no` within a store, SHALL allow bills with no number yet, and SHALL
refuse any later change to a number that has been assigned. (0001 uq_bills_invoice_no; 0004 trg_bills_invoice_no_once)

#### Scenario: A replayed assignment
- **GIVEN** bill `…-6e7f` carries `KUK/2026/000431`
- **WHEN** a retry tries to write `KUK/2026/000431` onto another bill
- **THEN** the second write is refused
- **AND** a bill still awaiting its number is not blocked by the uniqueness rule

### Requirement: The register prints the display number and treats the invoice number as opaque
The system SHALL print `display_no` on the customer's slip, keep it in the register's own records, and store the
invoice number the server returns without deriving or reformatting it. (api-routes §9.6, §9.1)

#### Scenario: A receipt printed before the push
- **GIVEN** the register has just saved a bill with `display_no` `S1-T2-000431` and no invoice number
- **WHEN** the receipt prints
- **THEN** the receipt shows `S1-T2-000431`
- **AND** when the acceptance arrives the register stores `KUK/2026/000431` beside it and prints it on request

### Requirement: A repeated display number is a reported fault
The system SHALL treat a reused display number as a possible numbering fault to be reported, not as an impossibility:
the store may hold two bills with the same display number, and the register SHALL surface that from the push reply.
(api-routes §6 display_no_conflict; review-schema D39)

#### Scenario: A counter reset after a restore
- **GIVEN** a register restored from a backup reprints `S1-T2-000100` while another stored bill already carries it
- **WHEN** the bills are pushed
- **THEN** the second bill is rejected `display_no_conflict`
- **AND** the owner is told the register's numbering needs attention rather than the bill being accepted silently

### Requirement: A reset restarts the series on the recorded boundary
The system SHALL restart a series on the boundary its cadence names, recording the date in the policy's last reset
field, so a yearly series starts again in the new year without changing the series identity. (0001
numbering_policy.reset_cadence, last_reset_on)

#### Scenario: The first bill of the new year
- **GIVEN** a policy whose cadence is yearly and whose prefix is the literal text `KUK/2026/`
- **WHEN** the series is reset for the new year and the first bill is numbered
- **THEN** the counter restarts at 1 and its last reset date is recorded in the policy
- **AND** the series keeps the same identity, prefix and padding across the reset

<!-- OPEN parity-billing #A: no route or job performs the reset, and a prefix that embeds the year makes the first bill of a new year collide on uq_bills_invoice_no. -->

### Requirement: A prefix change that collides with a stored number is refused
The system SHALL refuse an owner's change to a store's invoice prefix or padding when the resulting number would
repeat one already stored, answering `409 numbering_conflict`. (api-routes §2 store PATCH, §6 numbering_conflict)

#### Scenario: Shortening the padding
- **GIVEN** `KUK/2026/000431` is stored and the owner changes the padding from 6 to 3
- **WHEN** the store update is saved
- **THEN** the change is refused with `numbering_conflict`
- **AND** the stored number and the policy both stay as they were

### Requirement: Creating a terminal registers its series and its profile
The system SHALL create a terminal's register-scope numbering policy and its terminal profile in the same
operation that creates the terminal, so no register can start billing without a series. (api-routes §2
POST /v1/admin/terminals; numbering_policy, terminal_config)

#### Scenario: A second counter is installed
- **GIVEN** the owner adds terminal 2 to the Kukatpally store
- **WHEN** the terminal is created
- **THEN** a register-scope policy for terminal 2 and a terminal profile exist together
- **AND** terminal 2 receives both through its catalog pull before it bills

### Requirement: A stored bill's identity and money are frozen
The system SHALL refuse any update to a stored bill's total, subtotal, tax, rounding, business date, creation time,
cashier, store, terminal or local sequence. (0004 trg_bills_money_frozen; bills)

#### Scenario: An attempt to edit a stored bill's identity
- **GIVEN** a stored bill for 6500 paise on 2026-11-14 with local sequence 431
- **WHEN** an update tries to change its total, business date, cashier or sequence
- **THEN** it is refused
- **AND** the bill keeps the money and identity the customer was given

<!-- OPEN parity-billing #P: display_no is outside the frozen column list, so the number on the customer's slip can be edited after the bill is stored. -->
