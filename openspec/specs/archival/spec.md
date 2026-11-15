# Archival Specification

## Purpose
Defines how bills leave the live database for object storage on a per-tenant retention policy, so the database
stays under its size cap while the sale record survives for the statutory period.

## Requirements

### Requirement: Archival is a per-tenant flag with an owner-set retention
The system SHALL run archival only for a tenant whose archival flag is enabled, and SHALL archive only bills older than the retention in months the owner set, both held as tenant policy rather than as billing columns. (D22; D21 `tenant_flags`)

#### Scenario: A tenant with archival off
- **GIVEN** a tenant whose archival flag is disabled
- **WHEN** the monthly job reaches that tenant
- **THEN** no bill is exported or deleted
- **AND** the bills keep accumulating in the database
<!-- OPEN parity-platform #F12: retention_months has no defined minimum or default, and the six-year floor is unconfirmed with the accountant. -->

### Requirement: The job runs monthly, outside shop hours, one store-month at a time
The system SHALL run one archival pass per tenant per month on the shared schedule, outside shop hours, and SHALL process one store-month per pass. (D22; `deploy-inventory.md` cron and archive section)

#### Scenario: The monthly pass for October
- **GIVEN** five stores with October bills older than the retention window
- **WHEN** the tenant's archive day runs
- **THEN** each store's October month is exported and removed in turn
- **AND** the job is bounded to those store-months

### Requirement: A store-month is exported to object storage before anything is deleted
The system SHALL write one compressed JSONL export per store-month holding the bills, their lines and their voids, at the tenant, store and month path. (D22; `deploy-inventory.md` archive path)

#### Scenario: October of store Kukatpally
- **GIVEN** 900 October bills for store s1 of tenant reliance
- **WHEN** the export runs
- **THEN** one compressed file exists at archive/reliance/s1/2026-10.jsonl.gz
- **AND** it holds that month's bills, lines and voids

### Requirement: The export is verified against the database before any delete
The system SHALL re-read the export and compare both its row counts and its summed bill totals with the live rows, and MUST NOT delete anything for a store-month whose export fails the check. (D22; `deploy-inventory.md` archive section)

#### Scenario: A truncated upload
- **GIVEN** an export that wrote 899 of 900 bills
- **WHEN** verification compares counts and the total sum
- **THEN** the mismatch is detected
- **AND** no bill of that month is deleted

### Requirement: The retention watermark moves before the delete
The system SHALL advance the database's archive-before date past the exported month before deleting, and SHALL accept deletes only for bills older than that date. (D22; `schema_meta.archive_before`, `trg_bills_delete_archived_only`)

#### Scenario: A delete ahead of the watermark
- **GIVEN** the watermark at 2026-11-01
- **WHEN** a delete targets a bill dated 2026-11-12
- **THEN** the delete is refused as not archivable
- **AND** Covered by check-invariants: "delete a bill inside retention"

### Requirement: The watermark never moves backwards
The system SHALL keep the archive-before date monotonically forward, so a mis-ordered job cannot re-admit the current month for deletion. (D22 "The floor moves forward only")

#### Scenario: A second pass for the same month
- **GIVEN** the watermark already past 2026-10-31
- **WHEN** a job re-runs October
- **THEN** the export finds nothing to export
- **AND** the watermark is unchanged

### Requirement: Deletes run in bounded batches, voids before bills
The system SHALL delete a store-month's voids first and then its bills in batches of at most 500 selected ids, letting the lines and movements go with their bill. (D22; `query-catalogue.md` §16)

#### Scenario: Deleting 900 archived bills
- **GIVEN** 900 exported bills with their voids, lines and movements
- **WHEN** the delete runs in 500-id batches
- **THEN** nothing is left of those bills' lines or movements
- **AND** Covered by check-invariants: "archive leaves no lines behind"

### Requirement: The daily rollup is never deleted
The system SHALL keep the per store, date, item rollup after the bills that produced it are gone, so reports on archived months still answer. (D22; D13)

#### Scenario: Rollup survives the archive
- **GIVEN** a store-month with sales rolled up per item
- **WHEN** the month's bills are deleted
- **THEN** the rollup rows for that month remain
- **AND** Covered by check-invariants: "archive keeps sales_daily"

### Requirement: Archival does not change on-hand
The system SHALL leave every batch's on-hand balance untouched when archived sale movements are removed, and this MUST be the documented limit: a balance cannot be rebuilt from the live database alone after archival. (D27 cost; D22)

#### Scenario: On-hand after the archive
- **GIVEN** soap batch APR-24 at 6 bars including one bill that is later archived
- **WHEN** that bill is deleted
- **THEN** the batch still reports 6
- **AND** Covered by check-invariants: "archival does not change stock"

### Requirement: A void for an archived bill is parked, never dropped
The system SHALL refuse to apply a void whose bill is older than the archive-before date, SHALL tell the terminal the bill is archived, and MUST NOT silently discard the void. (D22; `api-routes.md` §3.4, §9.16)

#### Scenario: A late void arrives in December
- **GIVEN** a September bill already exported and deleted
- **WHEN** a register pushes a void for it
- **THEN** the response identifies it as archived
- **AND** the terminal parks the void rather than retrying it
<!-- OPEN parity-platform #F5: there is no server-side table to park an archived void, and the rollup could not be corrected from the void's payload anyway. -->

### Requirement: The archive outlives the database
The system SHALL keep the archived record for at least six years, and decommissioning a tenant SHALL keep its archive prefix even after the database is deleted. (D22; `release-management.md` decommission)

#### Scenario: Retiring a tenant
- **GIVEN** a tenant leaving the service with three years of archived months
- **WHEN** its database is deleted
- **THEN** its archive prefix remains readable
- **AND** the exported months still restore into a scratch database
