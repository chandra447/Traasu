# Reports Specification

## Purpose
Defines the owner's sales reports: what a day and a month are computed from, what a row means, and how a report
behaves after old bills have left the database.

## Requirements

### Requirement: Reports are admin-only and store-scoped
The system SHALL serve reports on the admin plane only, to owners and staff, and SHALL limit every row to the stores in the caller's store scope. (D9; `api-routes.md` §2, §9.1)

#### Scenario: A staff member scoped to one store
- **GIVEN** a staff user linked to store s1 only, and sales in s1 and s2
- **WHEN** the user requests the November sales report without naming a store
- **THEN** the rows cover s1 only
- **AND** s2 totals never appear

### Requirement: A report range is inclusive and capped at 92 days
The system SHALL treat the requested from and to dates as inclusive business dates, SHALL refuse a range longer than 92 days, and SHALL refuse from later than to. (D10 revised; `api-routes.md` §3.6)

#### Scenario: A quarter is allowed, a year is not
- **GIVEN** a report request from 2026-11-01 to 2026-11-30
- **WHEN** it runs
- **THEN** all 30 business dates are included
- **AND** a request from 2026-01-01 to 2026-12-31 is refused as too large

### Requirement: A single day report reads bill lines
The system SHALL compute a one-day report from the bill lines joined to their bills for that store and business date, counting only bills that are not voided, and MUST NOT re-derive money from the price tables. (D10; `query-catalogue.md` §7)

#### Scenario: One day of soap and rice sales
- **GIVEN** 42 live bills for 2026-11-14 with 4 lines each, and one voided bill
- **WHEN** the day report runs
- **THEN** each item's quantity and amount come from those 42 bills' lines
- **AND** the voided bill's line is absent from the totals

### Requirement: A multi-day report reads the daily rollup
The system SHALL compute any range longer than one day from the per store, per date, per item rollup, and MUST NOT scan bill lines for it. (D10 revised; D13; `query-catalogue.md` §8)

#### Scenario: A month of sales
- **GIVEN** sales across November in three stores
- **WHEN** the November report runs
- **THEN** each row is one item in one store summed over the month
- **AND** the answer comes from the rollup rather than from bill lines

### Requirement: The rollup follows every bill and void by trigger
The system SHALL add a bill's per-item quantities and amounts to the rollup when the bill row is inserted and subtract them when a void is recorded, so a replayed push cannot double count. (D13 revised; `trg_bills_rollup`, `trg_bill_voids_apply`)

#### Scenario: A replayed bill does not double the month
- **GIVEN** a November rollup row for tamarind at 15,000 g and ₹650
- **WHEN** the same bill is pushed again
- **THEN** the rollup stays at 15,000 g and ₹650
- **AND** a void of that bill then subtracts it once

### Requirement: The rollup needs no void filter
The system SHALL answer a multi-day report without filtering voided bills, because the rollup already carries the subtraction, and SHALL filter voided bills only on the single-day path. (D13 revised; `query-catalogue.md` §8)

#### Scenario: A void before month end
- **GIVEN** a November bill of 2,500 g tamarind for ₹400 that is voided
- **WHEN** the November report runs
- **THEN** the tamarind row omits that bill's quantity and amount
- **AND** the day report for that date also omits it

### Requirement: A row reports quantity, amount and average price per item
The system SHALL report for each item its base quantity, its amount and its average price, with the item's current name looked up from the item record rather than from the bill line's copied name. (D10; `query-catalogue.md` §7, §9)

#### Scenario: A renamed item keeps one row
- **GIVEN** an item renamed from "Dal" to "Dal Premium" part-way through November
- **WHEN** the November report runs
- **THEN** the item appears once, under the current name
- **AND** its average price is the month's amount divided by its quantity

### Requirement: provisional_until reports the newest receipt time seen
The system SHALL include in every report response the newest bill receipt time covered by the data, so the owner can tell a closed period from one a terminal has not synced. (`api-routes.md` §3.6)

#### Scenario: A terminal still offline
- **GIVEN** a day whose last bill was received at 18:40, and a terminal that has not synced since 14:00
- **WHEN** the day report runs
- **THEN** provisional_until is the 18:40 receipt time
- **AND** the owner can see the period is not final

### Requirement: A CSV export escapes formula-leading text and is size-capped
The system SHALL escape any exported cell beginning with equals, plus, minus or at with a single leading quote, SHALL return the export without pagination, and SHALL refuse an export above 50,000 rows or 50 MB. (`api-routes.md` §3.6, §5)

#### Scenario: An item name that starts with a minus sign
- **GIVEN** an item whose name begins with a minus sign
- **WHEN** the report is exported as CSV
- **THEN** that cell is written with a leading single quote
- **AND** an export of 60,000 rows is refused as too large

### Requirement: The override list shows discount and manual-price lines
The system SHALL list the lines whose price source is override or manual for a store and date range, with business date, bill number, item name, list price, charged price, approver, reason and policy flag, excluding voided bills and ordered newest first. (D3; `query-catalogue.md` §10)

#### Scenario: The owner reviews last week's discounts
- **GIVEN** 3 lines last week priced by override and 1 by a manual price, on live bills
- **WHEN** the owner opens the override list for that range
- **THEN** all 4 lines appear with both prices and the approver
- **AND** a line whose discount was recorded as a list sale does not appear
<!-- OPEN parity-billing #G: a discount can be stored with price_source "list" and no approver, so it is invisible to this list. -->

### Requirement: A report page is ordered and cursor-bound
The system SHALL order report rows by amount descending then item id, SHALL return a cursor keyed on that order with a lookahead-derived has_more, and MUST NOT treat an empty page as the end. (`api-routes.md` §5)

#### Scenario: A second page of items
- **GIVEN** a report returning 100 rows of more than 100 items
- **WHEN** the client asks for the next page with the returned cursor
- **THEN** the next rows continue after the last item of the first page
- **AND** has_more is false only when the lookahead finds no further row

### Requirement: An archived month still answers from the rollup
The system SHALL answer a report whose range lies before the archive watermark from the rollup, SHALL report the voided-bill count as null when a range crosses the watermark, and SHALL answer a single archived date from the rollup rather than from bill lines. (D22; `api-routes.md` §3.6)

#### Scenario: A September report after September was archived
- **GIVEN** September bills exported to the archive and deleted, with the rollup kept
- **WHEN** the owner runs a report for September
- **THEN** the quantities and amounts per item still come back
- **AND** voided_bills is null when the range crosses the watermark
<!-- OPEN parity-platform #F4: the rollup carries no tax, so any rate-wise or tax cut on an archived month reads zero instead of the archived tax. -->
