# Releases Specification

## Purpose

Move one Worker and one database per enterprise forward together without losing a bill: migrate every database before the code that needs it, declare the oldest schema a build can run against, promote through staging and a one-week canary shop, and make every recovery path end in records that reconcile.

## Requirements

### Requirement: Migration runs before the deploy, in one workflow

The system SHALL run the per-tenant migration job before the deploy job in one workflow where the deploy depends on the migration, and SHALL smoke each tenant after the deploy, so a failed migration leaves the old build serving. (D20, D21; release-management.md)

#### Scenario: A migration that fails on the third tenant

- **GIVEN** ten tenant databases at schema 8 and a release carrying migration `0009`
- **WHEN** `0009` fails on the third database
- **THEN** the loop stops, no Worker deploy runs, and all ten tenants keep serving on the previous build
- **AND** the two migrated tenants are at 9 against a build declaring `min_schema` at or below 8, which is a healthy state

### Requirement: Staging, then a canary shop, then the fleet

The system SHALL deploy staging on every merge, SHALL expose a release to one real shop as canary at least a week before the rest of the fleet, and SHALL promote to the remaining tenants only after the canary survives. (D20; release-management.md)

#### Scenario: Release 1.9.0 on a five-store enterprise

- **GIVEN** a canary shop on release 1.9.0 since Monday
- **WHEN** the seventh day passes with no incident
- **THEN** the remaining four stores are migrated and deployed together
- **AND** one deploy serves all five, with no per-store environment or version pin

### Requirement: The database is the schema truth, the copies are caches

The system SHALL treat the tenant database's own migrations table, together with `schema_meta.schema_version`, as the only truth, and SHALL treat the control-plane copy and the versions file as caches the migration job rewrites; on disagreement the database wins and the run logs the rewrite. (D21 revised; d1_migrations, schema_meta)

#### Scenario: A cache that disagrees with the database

- **GIVEN** a versions file claiming schema 8 for a tenant whose database reports 7
- **WHEN** the migration job reads both
- **THEN** it records 7, rewrites the cache, and logs the correction
- **AND** the deploy's `min_schema` decides whether the new build may serve that tenant

### Requirement: Every migration sets its own schema level

The system SHALL set `schema_meta.schema_version` inside the same migration file that changes the shape, so a database never reports a level its tables do not match. (D21 revised; 0002 to 0008 each set the version)

#### Scenario: A shape change without a level

- **GIVEN** a migration that adds a table and does not set the level
- **WHEN** review and the release checks run
- **THEN** it is refused, because the level and the tables would disagree and every route would answer `503 schema_mismatch` against a build that expects the new level
- **AND** covered by check-invariants: "schema_version after all migrations"

<!-- OPEN parity-platform #20: a migration file is not atomic — D1 sends it without BEGIN and commits each statement, ALTER ADD COLUMN is not idempotent, and the documented "re-run the job" recovery aborts on the first statement it already applied. One statement per file plus a table-info fingerprint check is the fix. -->

### Requirement: An applied migration is never edited

The system SHALL treat a migration file as immutable once it has been applied anywhere, SHALL fail the release on checksum drift, and SHALL ship a correction as a new file. (D21; checksums.sha256)

#### Scenario: A typo found after the canary run

- **GIVEN** `0009` applied to the canary database with a wrong default of 5000 paise
- **WHEN** someone edits `0009` in place
- **THEN** the checksum check fails the release before any database is touched
- **AND** the correction ships as `0010` and is applied to every database including the canary

### Requirement: A release declares the oldest schema it can run against

The system SHALL declare `min_schema` on every release, SHALL run the test suite against a database one schema level behind, and SHALL refuse a drop or rename unless every tenant reports the version that stopped using the column. (D20; release-management.md, /health)

#### Scenario: A build that only works on the newest schema

- **GIVEN** release 1.9.0 declaring `min_schema` 8 and a test database at 7
- **WHEN** the suite runs
- **THEN** a query that reads a schema-8 column fails the build, not the fleet
- **AND** the same build serves tenants at 8 and at 9 during a rollout

### Requirement: The request path refuses a database the build cannot serve

The system SHALL answer `503 schema_mismatch` on every route except the health route when the routed database is older than the build's `min_schema`, and SHALL report `schema_ahead` when the database is newer. (D21 revised; /health)

#### Scenario: One tenant behind during a rollout

- **GIVEN** a build with `min_schema` 8 and a tenant database still at 7
- **WHEN** its registers push bills
- **THEN** every sync route answers `503 schema_mismatch` and nothing is written
- **AND** its health route reports `schema_version: 7`, `min_schema: 8`, and the admin diagnostics page shows both

### Requirement: A Worker rollback is bounded by min_schema

The system SHALL roll back a bad Worker deploy in seconds with the platform rollback, and SHALL permit that only when the previous release's `min_schema` is at or below every tenant's current schema. (D20; release-management.md)

#### Scenario: Rolling back 1.9.0

- **GIVEN** 1.9.0 breaking bill push, and 1.8.0 declaring `min_schema` 7
- **WHEN** every tenant is at schema 8 or 9
- **THEN** the rollback is valid and the fleet serves 1.8.0 immediately
- **AND** if a tenant had already reached a schema 1.8.0 cannot read, the rollback is refused and the fix ships forward

### Requirement: The loop stops on the first failure

The system SHALL stop the migration loop at the first failing tenant rather than continuing, SHALL leave the untouched tenants at the previous schema, and SHALL report the per-tenant result the loop reached. (D20; release-management.md)

#### Scenario: A half-migrated fleet

- **GIVEN** ten tenants and a loop that fails on the fourth
- **WHEN** the run ends
- **THEN** four tenants are migrated and six are untouched, and the report names each
- **AND** the old build keeps serving the six, because they still meet its `min_schema`

### Requirement: Migrations expand, then contract

The system SHALL keep migrations expand-only during a rollout — add tables and columns and backfill idempotently — SHALL write both shapes in the release that adds one, read the new shape in the next, and drop the old column only after every tenant reports the version that stopped writing it. (D20; release-management.md)

#### Scenario: Adding a column in three releases

- **GIVEN** release N adds a nullable column with an idempotent backfill
- **WHEN** N writes both shapes, N+1 reads the new one, and N+2 drops the old one after every tenant reports N+1
- **THEN** no tenant ever runs code against a column it has not been given
- **AND** a drop proposed before every tenant reports N+1 is refused in review

### Requirement: The repository checks gate every migration

The system SHALL run the schema, invariant and query-plan checks before touching any database, and SHALL stop the run with nothing applied when one fails. (D21; docs/check-schema.py, check-invariants.py, explain-queries.py)

#### Scenario: A query that lost its index

- **GIVEN** a change that makes the day report scan the whole bill history
- **WHEN** the query-plan check runs before the migration job
- **THEN** the run stops with zero databases touched and the report names the query
- **AND** the archive delete still reads 33,600 rows for 900 bills, not 73 million

### Requirement: A restore ends with a new epoch and a terminal re-push

The system SHALL restore with the platform's point-in-time recovery, SHALL capture the pre-restore maximum epoch before restoring and set it to that value plus one, SHALL rebuild the daily rollup from bill lines, verify the catalog floor, check health, and confirm a terminal gets a stale-cursor reset and completes a staged snapshot. (D21; schema_meta.sync_epoch)

#### Scenario: A bad data write at 11:00

- **GIVEN** a tenant at sync epoch 4 restored to 10:59
- **WHEN** the epoch is bumped to 5 and the fleet reconnects
- **THEN** every terminal resets its catalog through a staged snapshot and re-pushes bills, voids and sessions created at or after 09:59
- **AND** the re-push is idempotent by id and payload hash, so a replayed bill matches and a restored-away bill comes back
- **AND** bumping to the restored epoch plus one is forbidden, because a terminal may already hold that epoch

### Requirement: The versions file is a cache written after a green smoke

The system SHALL write the versions file after a green smoke with the Worker version, `min_schema`, deploy time and each tenant's schema and migration count, and SHALL lose nothing if that file is deleted. (D21; infra/versions.json)

#### Scenario: Rebuilding the versions file

- **GIVEN** a deleted versions file and ten tenant databases
- **WHEN** the migration job runs without applying anything
- **THEN** it reads every tenant's level and rewrites the file
- **AND** no deploy or migration is triggered by the rebuild

### Requirement: A contracted tenant can be released on its own

The system SHALL give a tenant that contracts for version isolation its own Worker and its own release path, running identical code, without changing the shared path for the rest of the fleet. (D20; the stated exception)

#### Scenario: A tenant demanding version isolation

- **GIVEN** nineteen tenants on the shared release and one that contracted isolation
- **WHEN** the isolated tenant's release is prepared
- **THEN** it deploys from the same source through a second workflow and a second binding set
- **AND** the nineteen keep the shared release, the shared rollback and the shared canary
