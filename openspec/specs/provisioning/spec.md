# Provisioning Specification

## Purpose

Turn a signed franchisee into a database a register can bill from: validate the input before creating anything, apply the migrations, seed one store with a complete catalog and an owner who can sign in once, then hand over only after a smoke test, alerts and a registry entry exist.

## Requirements

### Requirement: Input is validated before a resource exists

The system SHALL validate the slug against `^[a-z][a-z0-9-]{2,30}$`, tenant uniqueness, an active Workers Paid plan, zone and DNS access, a valid owner email, the expected store count and a retention window before creating any resource, and SHALL abort with nothing created on a failure. (D24; deploy-inventory.md §4 step 1)

#### Scenario: A slug that would break a binding name

- **GIVEN** an operator running provisioning with `--slug Reliance_POS`
- **WHEN** the input check runs
- **THEN** provisioning aborts before any database or registry change
- **AND** a Free-plan account aborts at the same step, because 500 MB per database cannot hold a year of bills

### Requirement: Provisioning is resumable and idempotent

The system SHALL record progress in a per-tenant state file and SHALL make every create step get-or-create, every seed `INSERT OR IGNORE`, and every verification read-only, so a re-run after a failure resumes without duplicating anything. (deploy-inventory.md §4)

#### Scenario: A failure after the database exists

- **GIVEN** a run that created database `pos-srilakshmi` and failed while generating bindings
- **WHEN** the operator re-runs the same command
- **THEN** the recorded database id is reused and no second database is created
- **AND** the seed step inserts only what is still missing

### Requirement: The tenant registry entry is reviewed code

The system SHALL append slug, host, tenant id, app id, binding `DB_<SLUG>`, database id and expected store count to the tenant registry in a pull request, and SHALL generate the database binding and route blocks from that registry, failing the build on drift. (D20, D21; infra/tenants.json)

#### Scenario: A new tenant's deploy config

- **GIVEN** a registry entry for `srilakshmi` with host `srilakshmi.pos.app`
- **WHEN** the binding generator runs and the result is committed
- **THEN** the deploy carries one `DB_SRILAKSHMI` binding and one custom-domain route
- **AND** a hand-edited binding that disagrees with the registry fails the drift check

### Requirement: The database is migrated before anything can reach it

The system SHALL apply every migration, `0001` through the latest, to the new database before the Worker release that carries its binding ships, so the first request that resolves to it finds the current schema. (D21; deployment order)

#### Scenario: First request on a new host

- **GIVEN** a freshly created database at migration `0008`
- **WHEN** the Worker with its binding is deployed and `/health` is called on the new host
- **THEN** `schema_version` is 8, the migrations table holds 8 rows, and `min_schema` is at or below it
- **AND** no route answers `503 schema_mismatch`

### Requirement: The seed leaves a store, a register and an owner

The system SHALL seed, in one batch, `schema_meta` with the tenant id, app id and time; a store with its settings, a Main book at priority 1, a terminal with its configuration and a store-scope numbering policy; and the owner with a one-time password and a pending password change, every id a fresh UUIDv7. (D27, D28; schema_meta, stores, books, terminals, numbering_policy)

#### Scenario: The first store of a new tenant

- **GIVEN** a tenant being provisioned for Kukatpally with one register
- **WHEN** the seed batch commits
- **THEN** the store, terminal and numbering policy exist with freshly generated ids, the policy prefix `KTP/` and padding 6
- **AND** the owner row carries `must_change_password = 1` and no password of its own
- **AND** those ids differ from every other tenant's, so two tenants' exports never look like the same bill

### Requirement: A fresh terminal never sees an empty catalog

The system SHALL seed the complete catalog — items, prices and identifiers — and SHALL append its catalog change rows and advance the catalog allocator head in the same batch, so the first terminal to enrol pulls the whole catalog. (D1, D27; catalog_changes, catalog_seq)

#### Scenario: A first pull on a new terminal

- **GIVEN** a seeded tenant with 240 items and an allocator head of 8,431
- **WHEN** a newly enrolled terminal pulls its catalog from `since=0`
- **THEN** it receives 8,431 change rows covering every catalog document
- **AND** no document is missing because its change row was written in a later batch

<!-- OPEN parity-platform #3: stores has no GSTIN, State or address, so a printed invoice cannot carry the supplier particulars and GSTR-1 cannot be partitioned by registration. -->

### Requirement: Handover waits for a green smoke on the new host

The system SHALL smoke the new host before handover: `/health` with app id, tenant id, schema version and min schema; the admin SPA; owner sign-in; `/v1/admin/me`; minting an enrolment code; enrolling a test client and receiving `403 approval_pending`; owner approval; `/v1/token`; a catalog snapshot; and one fake bill push. Any failure SHALL block handover and the database SHALL be kept for diagnosis. (D5, D21; deploy-inventory.md §4 step 8)

#### Scenario: The full smoke path

- **GIVEN** a seeded tenant and a minted 15-minute enrolment code
- **WHEN** the test client enrols, the owner approves it, and it pushes one bill of two lines totalling 18,750 paise
- **THEN** enrol answered `403 approval_pending` before approval and `/v1/token` issued a 5-minute token after it
- **AND** the bill is accepted with an invoice number and its lines are readable in the report

### Requirement: A token from another tenant is refused on the new host

The system SHALL verify, before handover, that a device token minted for a previously provisioned tenant is rejected on the new host, and SHALL stop handover if such a token verifies. (D20; HKDF per-tenant keys)

#### Scenario: Cross-tenant token check

- **GIVEN** a valid 5-minute token minted for `tnt_kukatpally`
- **WHEN** it is presented to `GET /v1/catalog` on the new host
- **THEN** the response is `401 invalid_token` and no catalog row is served
- **AND** a token that verifies is treated as a resolver bug and blocks handover

### Requirement: The custom hostname and its certificate come before the URL is issued

The system SHALL wait for DNS and an active certificate on the tenant's custom hostname before issuing the URL, smoking through the workers.dev address in the meantime. (deploy-inventory.md §4 step 9)

#### Scenario: A pending certificate

- **GIVEN** DNS created and the certificate still provisioning
- **WHEN** `/health` is called on the custom host
- **THEN** the URL is not handed over and the smoke is repeated against the workers.dev address
- **AND** the URL is issued only when `/health` answers 200 on the custom host

### Requirement: Per-tenant alerts exist before handover

The system SHALL have, before handover, alerts for Worker 5xx above 1%, that database past 70% of 10 GB, cron failure, archive job failure, `/health` non-200, and an uptime check, and SHALL block handover while any is missing. (D18, D22; deploy-inventory.md §7)

#### Scenario: Growth on a five-store tenant

- **GIVEN** a tenant writing about 4,961 bytes per four-line bill
- **WHEN** its database passes 7 GB
- **THEN** the 70% alert fires to the operator, not the owner
- **AND** the response is a shorter retention window or a store-group split, not a larger database

### Requirement: The handover sheet records what the owner needs once

The system SHALL write the registry entry and print a handover sheet carrying the admin URL, the owner email, the one-time password exactly once, the store and terminal ids, versions and retention months. (deploy-inventory.md §4 step 12)

#### Scenario: Handing over to the owner

- **GIVEN** a green smoke on the new host
- **WHEN** the handover sheet is printed
- **THEN** it carries the admin URL, the owner email and a one-time password
- **AND** a later re-run of provisioning does not reprint that password

### Requirement: Adding a store is a data change, not a provision

The system SHALL add a store by inserting the store, its settings, a Main book at priority 1, its numbering policy, a terminal and its configuration, appending those catalog change rows and advancing the allocator in one batch, with no database, Worker or deploy. (D27, D28; catalog_changes)

#### Scenario: A second outlet in the same enterprise

- **GIVEN** one enterprise with store Kukatpally billing 600 bills a day
- **WHEN** store KPH is added in one batch
- **THEN** KPH gets its own store-scope numbering series and a Main book
- **AND** every existing terminal receives the new store documents on its next catalog delta, with no redeploy

### Requirement: Decommissioning keeps the record longer than the database

The system SHALL decommission by exporting the database, keeping the tenant's archive prefix for the retention period, revoking every device, exporting audit rows, removing the registry entry and deploying, then deleting the database, and SHALL keep the registry entry marked retired for a year. (D22; archive prefix)

#### Scenario: A franchisee leaving

- **GIVEN** a tenant with six years of archived bills under its archive prefix
- **WHEN** decommissioning completes
- **THEN** the database is deleted, every device is revoked, and the archive prefix is untouched
- **AND** no bill is deleted before its verified export exists

<!-- OPEN parity-platform #12: the retention window is owner-set with no floor, and section 36 of the CGST Act needs records until 31 December of the seventh year after the bill's financial year, so a 24-month setting is unlawful. -->
