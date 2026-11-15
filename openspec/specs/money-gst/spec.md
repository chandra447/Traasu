# Money and GST Specification

## Purpose
Fixes how a bill's money is built and checked: the line identities, the CGST/SGST/IGST split, cess, the
tax-inclusive price rule, the header reconciliation and the rounding line. Every amount here is what the customer
paid; reports and the GST return read these fields and never recompute a different number.

## Requirements

### Requirement: A line's total is its taxable value plus every tax component
The system SHALL store each line so that `line_total_minor` equals `taxable_minor` plus `cgst_minor`, `sgst_minor`,
`cess_minor` and `igst_minor`, and SHALL refuse a line where that sum differs. (D23; 0008 trg_lines_identity_ins)

#### Scenario: A 5% line of ₹10.29
- **GIVEN** a line of 1000 g of toor dal charged 1029 paise at GST 5% inclusive
- **WHEN** the line is written with taxable 980, CGST 24 and SGST 25
- **THEN** the line is accepted, because 980 + 24 + 25 = 1029
- **AND** the same line written with 25 and 25, a total of 1030, is refused
- **AND** Covered by check-invariants: "same line split 25/25 (sum 1030)"

#### Scenario: An inter-state line includes the IGST in its total
- **GIVEN** a line of taxable 10000 paise at 18% IGST
- **WHEN** the line is written with IGST 1800 and total 13600
- **THEN** it is refused, because the components must already be inside the total
- **AND** the same line written with total 11800 is accepted
- **AND** Covered by check-invariants: "IGST line, total includes the tax"

### Requirement: A line's total equals its list amount minus its discount, with no bypass
The system SHALL store each line so that `line_total_minor` equals `list_amount_minor` minus `discount_minor`, and
SHALL apply that check even when `list_amount_minor` is zero. (D23; 0008 trg_lines_discount_identity)

#### Scenario: A ₹1 discount on 6500 paise
- **GIVEN** a Tamarind line with list amount 6500 paise and a discount of 100 paise
- **WHEN** the line is written with total 6400, taxable 6095, CGST 152 and SGST 153
- **THEN** it is accepted
- **AND** Covered by check-invariants: "line discount reconciles"

#### Scenario: A discount claimed against a zero list amount
- **GIVEN** a line whose list amount is 0 paise, the column default
- **WHEN** the line claims a discount of 99999 paise
- **THEN** it is refused, because the old bypass that skipped the check at zero is gone
- **AND** Covered by check-invariants: "discount with list_amount 0 (old bypass)"

### Requirement: One tax amount splits into CGST and SGST by floor half
The system SHALL split a line's GST into CGST as `floor(tax / 2)` and SGST as the remainder, so the two halves
never differ by more than one paisa and the register and the server produce the same figure. (D23; 0008
trg_lines_tax_split)

#### Scenario: An odd paisa goes to SGST
- **GIVEN** a ₹10.29 line at 5% carrying 49 paise of GST
- **WHEN** the line is written with CGST 24 and SGST 25
- **THEN** it is accepted
- **AND** the same line written with CGST 23 and SGST 26 is refused
- **AND** Covered by check-invariants: "5% inclusive 1029 split cgst=24 sgst=25"
- **AND** Covered by check-invariants: "split 23/26 violates floor rule"

### Requirement: IGST replaces CGST and SGST on a line and never joins them
The system SHALL refuse a line that carries both IGST and either CGST or SGST, because a sale is either
intra-state or inter-state and never both. (0003 trg_lines_tax_exclusive)

#### Scenario: A line claiming both splits
- **GIVEN** an inter-state line of taxable 10000 paise at 18%
- **WHEN** it is written with IGST 1800, CGST 900 and SGST 900
- **THEN** it is refused
- **AND** the same line with IGST 1800 and no CGST or SGST is accepted
- **AND** Covered by check-invariants: "IGST plus CGST on one line"

### Requirement: An inclusive price carries its GST inside the charged amount
The system SHALL compute a tax-inclusive line's tax as the charged amount multiplied by the rate and divided by
one hundred percent plus the rate, rounded once to the nearest paisa, and SHALL store the remainder as the taxable
value. (D23; api-routes §3.4)

#### Scenario: ₹65.00 of tamarind at 5%
- **GIVEN** a Tamarind line charged 6500 paise at 5% inclusive
- **WHEN** the register computes the tax
- **THEN** the tax is 310 paise, from 6500 × 500 / 10500 rounded
- **AND** the taxable value is 6190 paise, split into CGST 155 and SGST 155

<!-- OPEN parity-billing #H: nothing ties the stored tax amounts to the line's own tax_rate_bp, so an 18% line can carry zero tax. -->

### Requirement: Selling prices are tax-inclusive and the flag is copied as a record
The system SHALL treat every selling price as inclusive of GST, and SHALL copy the item's `price_includes_tax`
value onto the line as the fact the register used. (api-routes §9.5; bill_lines.price_includes_tax)

#### Scenario: A retail line states its basis
- **GIVEN** an item priced at ₹40.00 per 1000 g with GST 5% included in that price
- **WHEN** a 1500 g line is written
- **THEN** the line carries `price_includes_tax` 1 and a total of 6500 paise
- **AND** the taxable value plus the GST components equal that same 6500 paise

<!-- OPEN parity-billing #D: an item with price_includes_tax = 0 cannot be billed honestly — the discount identity forces list_amount = taxable. -->

### Requirement: Tax is computed per line and the bill's tax is their sum
The system SHALL compute GST on each line against that line's own amount, and SHALL derive the bill's tax, and any
per-rate summary, by summing the lines rather than by computing the same tax a second way. (D23; bill_lines,
bills.tax_minor)

#### Scenario: Three ₹10.01 lines at 18%
- **GIVEN** three lines of 1001 paise each at GST 18% inclusive
- **WHEN** the bill is written
- **THEN** each line carries 153 paise of tax and the bill carries 459
- **AND** the per-rate summary reports 459, not the 458 an invoice-level calculation would give

### Requirement: A bill's header reconciles against its own lines
The system SHALL write the lines before the header and SHALL refuse a header whose `subtotal_minor` differs from
the sum of line taxable values, whose `tax_minor` differs from the sum of the lines' CGST, SGST, cess and IGST, or
whose `total_minor` differs from the sum of line totals plus the rounding. (D23; 0008 trg_bills_reconcile_lines)

#### Scenario: A header tax computed at the invoice level
- **GIVEN** three lines of 1001 paise at 18% carrying 153 paise of tax each
- **WHEN** the header is written with subtotal 2544, tax 458 and total 3003
- **THEN** it is refused, because the lines sum to 459 paise of tax
- **AND** Covered by check-invariants: "header tax at invoice level (458) vs lines (459)"

### Requirement: A bill's total is its subtotal plus its tax plus its rounding
The system SHALL store each bill so that `total_minor` equals `subtotal_minor` plus `tax_minor` plus
`rounding_minor`. (D23; 0002 trg_bills_identity_ins)

#### Scenario: A header total that ignores its lines
- **GIVEN** a bill whose only line is the ₹10.29 dal line
- **WHEN** the header is written with total 1, subtotal 0 and tax 1
- **THEN** it is refused
- **AND** the header written with subtotal 980, tax 49 and total 1029 is accepted
- **AND** Covered by check-invariants: "header total 1 against a 1029 line"

### Requirement: A bill has at least one line
The system SHALL refuse to store a bill that has no lines, so no header can record money without a receipt line
behind it. (D23; 0008 trg_bills_reconcile_lines)

#### Scenario: A header pushed with an empty cart
- **GIVEN** a bill id with no line rows
- **WHEN** the header is written with zero subtotal, tax and total
- **THEN** it is refused
- **AND** Covered by check-invariants: "bill with no lines"

### Requirement: Rounding is a single header adjustment of at most one rupee
The system SHALL apply the store's rounding mode as one `rounding_minor` value between -100 and 100 paise on the
header, and SHALL leave the line amounts untouched. (D23; 0001 bills.rounding_minor, store_settings.rounding_mode)

#### Scenario: ₹10.29 rounded to the rupee
- **GIVEN** a bill of one line at 1029 paise with nearest-rupee rounding
- **WHEN** the bill is saved with rounding -29
- **THEN** the header carries subtotal 980, tax 49, rounding -29 and total 1000
- **AND** the line still reads 1029 paise, so the tax on it did not change
- **AND** Covered by check-invariants: "nearest-rupee rounding on top of lines"

<!-- OPEN parity-billing #K: rounding_mode is not copied onto the bill and no report sums rounding_minor, so a day's sales never equal a day's cash. -->
