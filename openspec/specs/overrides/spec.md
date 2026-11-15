# Overrides Specification

## Purpose
Governs the money exceptions a cashier can make at the counter: a percentage or rupee discount, a price typed by
hand, and the approval that authorises one. It fixes what the line records, what the register may do offline, and
what the server verifies afterwards, so a discount is always visible and never a way to rewrite a price silently.

## Requirements

### Requirement: Every reduction from the list amount is recorded on the line
The system SHALL record the difference between a line's list amount and the amount charged as `discount_minor` on
that line, and SHALL record on `price_source` how the charged amount was reached: a list price, a quantity tier, an
override of the list price, or a price typed by hand. (D23; bill_lines.discount_minor, bill_lines.price_source)

#### Scenario: ₹10 off a ₹65 line
- **GIVEN** a Tamarind line whose list amount is 6500 paise
- **WHEN** the cashier charges 5500 paise
- **THEN** the line carries `discount_minor` 1000, `list_amount_minor` 6500, `line_total_minor` 5500, taxable 5238,
  CGST 131 and SGST 131
- **AND** the discount is visible on the bill rather than folded into a smaller list amount

<!-- OPEN parity-billing #G: price_source is terminal-asserted, so a ₹10 discount can still be stored as a list sale with a low list_amount and never appear in the override review. -->

### Requirement: An override names the user who made it
The system SHALL record the authorising user on every line whose price source is an override or a manual price, and
SHALL refuse such a line without one. (0001 bill_lines; bill_lines.override_by CHECK)

#### Scenario: A manual price with no approver
- **GIVEN** a soap line priced by hand at 9000 paise
- **WHEN** the line is written with `price_source` manual and no `override_by`
- **THEN** the line is refused
- **AND** the same line written with the cashier's user id is accepted

### Requirement: An override states a reason from a closed list
The system SHALL record an override reason drawn from damaged, near expiry, haggling, staff or other, and SHALL put
any further explanation in the note field. (0001 bill_lines; D25 D44)

#### Scenario: Damaged stock sold cheap
- **GIVEN** a packet of biscuits at list 3000 paise damaged on the shelf
- **WHEN** the cashier sells it for 1500 paise
- **THEN** the line carries `override_reason` damaged, `override_note` free text and `discount_minor` 1500
- **AND** any other reason string is refused by the column's own check

### Requirement: Approval is a token minted online, never a shipped secret
The system SHALL gate money exceptions with an approval grant issued to a named approver for one terminal, and
SHALL never ship a manager credential or a cached manager PIN to a register. (D8; approval_grants, POST /v1/approvals)

#### Scenario: The owner approves a discount at the counter
- **GIVEN** the manager is an active owner and the register is online
- **WHEN** the manager enters their own password on the register
- **THEN** the register receives a grant token, its expiry, `max_overrides` and `max_discount_pct`
- **AND** nothing else about the manager's credential is stored on the register

### Requirement: A grant expires and is bound to one terminal and one approver
The system SHALL issue each approval grant with an expiry, a terminal id and a user id, and SHALL treat an expired
or revoked grant as authorising nothing. (D3; approval_grants, api-routes §3.4)

#### Scenario: A grant from yesterday
- **GIVEN** a grant issued at 09:00 for terminal 2 with an expiry of 21:00
- **WHEN** an override is attempted at 22:15 on that terminal
- **THEN** the grant authorises nothing and the register falls back to the ceiling
- **AND** a grant presented on another terminal authorises nothing either

### Requirement: The store ceiling bounds a discount made without a grant
The system SHALL fall back to the store's override ceiling, a percentage and a rupee amount with the tenant
defaults standing in when unset, whenever no valid grant covers the line. (D3; 0008 store_settings.override_ceiling_pct,
override_ceiling_minor)

#### Scenario: A 20% discount under a 10% ceiling
- **GIVEN** the store ceiling is 10% and a Tamarind line lists at 6500 paise
- **WHEN** the cashier tries to charge 5500 paise, a discount of 1000 paise, with no grant
- **THEN** the register allows at most 650 paise of discount on that line
- **AND** the cashier either reduces the discount to the ceiling or asks for an approval

### Requirement: Offline, the sale still completes at the ceiling
The system SHALL complete a sale made offline with no valid grant by using the ceiling or the list price, and SHALL
never refuse or park the sale for want of an approval. (D3, D2; api-routes §9.2)

#### Scenario: A discount asked for with the uplink down
- **GIVEN** the register has no network and holds no unexpired grant
- **WHEN** the cashier tries a 20% discount on a 6500 paise line at a 10% ceiling
- **THEN** the line is billed at 5850 paise, the ceiling
- **AND** the bill is saved and pushed later like any other

### Requirement: The line records the grant that authorised it
The system SHALL record on an authorised line the id of the grant used, as a plain value that survives the grant
row being purged. (D25 D33; 0008 bill_lines.approval_grant_id)

#### Scenario: A grant later purged
- **GIVEN** a line authorised by grant `0192f3c0-…-a7` and the grant has since been deleted
- **WHEN** the bill line is read a year later
- **THEN** the line still carries that grant id
- **AND** the amount and the approver on the line are unchanged

### Requirement: A grant's uses are counted and exceeding the cap is a flag
The system SHALL count the lines authorised by a grant against its `max_overrides` and its `max_discount_pct`, and
SHALL accept a line that exceeds either while flagging it `policy_violation`. (D25 D33; api-routes §3.4)

#### Scenario: The eleventh override on a ten-use grant
- **GIVEN** a grant with `max_overrides` 10 and `max_discount_pct` 15, already used on ten lines
- **WHEN** an eleventh line is pushed under it at a 20% discount
- **THEN** the bill is accepted
- **AND** the line is stored with `policy_violation` 1 for the owner to review

### Requirement: The server verifies an override against its own prices and never rewrites money
The system SHALL recompute the applicable list price from its own price rows at push time, record it as
`computed_list_price_minor`, and keep the charged amount exactly as the register reported it. (D2; bill_lines)

#### Scenario: A terminal claims a ₹100 list price that the server says is ₹65
- **GIVEN** the tamarind version named by the line costs 6500 paise on the server
- **WHEN** the line arrives claiming a list amount of 10000 paise with no discount
- **THEN** the server stores `computed_list_price_minor` 6500 and can flag the discrepancy
- **AND** `line_total_minor` stays whatever the terminal charged

### Requirement: A ceiling breach is never a rejection
The system SHALL accept and store a completed sale whose discount exceeds the ceiling or the grant, recording
`policy_violation` on the line, because the customer has already paid. (D2, D3; api-routes §9.2)

#### Scenario: A bill that arrives over the ceiling
- **GIVEN** an offline bill with a 30% discount on a line, pushed after the store ceiling was lowered to 10%
- **WHEN** the push is handled
- **THEN** the bill is accepted with no rejection code
- **AND** the line carries `policy_violation` 1 and the response counts it in `flagged.policy_violation`

### Requirement: Override lines are listable for owner review
The system SHALL answer an owner's review query for override and manual lines over a date range from live bills,
excluding voided bills, without reading any price history. (query-catalogue §10; 0004 idx_bill_lines_override_live)

#### Scenario: Reviewing last week's discounts
- **GIVEN** the store wrote 40 override lines between 2026-11-08 and 2026-11-14 and voided one bill with two of them
- **WHEN** the owner lists overrides for that range
- **THEN** 38 lines are returned with date, bill number, item name, list price, charged price, approver, reason and
  the violation flag
- **AND** the two lines of the voided bill are absent
