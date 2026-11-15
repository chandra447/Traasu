# Catalog Sync Specification

## Purpose

Defines how a register learns the catalog: one ordered change log read by a scalar checkpoint, paged snapshots when that checkpoint is unusable, and the store projection that keeps other stores' rows out.

## Requirements

### Requirement: Catalog writes append to one ordered change log

Every catalog mutation SHALL append `catalog_changes` rows whose sequence numbers come from the single `catalog_seq` allocator inside the same batch, so commit order is sequence order and a rolled-back batch returns its numbers. (D1; 0001 `catalog_changes`, `catalog_seq`)

#### Scenario: one admin save touching an item and its price

- **GIVEN** `catalog_seq.last_seq` is 8430
- **WHEN** an admin saves an item edit that writes one change row and a price revision that writes another
- **THEN** both rows commit at 8431 and 8432 with `last_seq` 8432
- **AND** a batch that fails leaves no change row and `last_seq` at 8430

### Requirement: The checkpoint is a cursor of format, epoch and sequence

A catalog pull SHALL send cursor format 2, the epoch and the last applied sequence; a cursor of any other format SHALL be answered `cursor_format_unsupported` and never coerced into a sequence. (api-routes §4, §9.8)

#### Scenario: an old terminal with a timestamp cursor

- **GIVEN** a register still stores a timestamp cursor from an earlier build
- **WHEN** it pulls with cursor format 1
- **THEN** the Worker answers 400 `cursor_format_unsupported`
- **AND** the register takes a snapshot instead of treating the timestamp as a sequence

### Requirement: Floor, head and page come from one read

The Worker SHALL read the epoch, the allocator, the prune floor, the page with one lookahead row and the highest committed sequence in one read transaction pinned to the primary, and SHALL report the committed maximum as head, never the allocator value. (api-routes §4, §9.10; query-catalogue §1)

#### Scenario: a price edit commits mid-pull

- **GIVEN** the terminal pulls from sequence 8412
- **WHEN** a price revision commits as the page is read
- **THEN** the page carries the rows the transaction saw and head is the highest committed sequence
- **AND** `alloc_head` may be one allocation higher than head without changing the cursor

### Requirement: A page delivers documents, never half a tier set

Each change SHALL be expanded to its current document inside the same batch, and a price change SHALL carry that item's whole price set for the token's scopes, so a page boundary cannot split a revision. (D1 revised; api-routes §3.3, §9.12; d1-schema.md)

<!-- OPEN parity-catalog #12: the document resends every version the item ever had, so a bulk price update costs many pages; nothing narrows it by time. -->

#### Scenario: a two-tier revision in one change row

- **GIVEN** Tamarind's version 8 holds a 1000 g tier and a 500 g tier
- **WHEN** the change for version 8 lands on a page
- **THEN** both tiers arrive in one document with version, start and end
- **AND** the page cannot end between the two tiers

### Requirement: The journal names every catalogued document type

Each change row SHALL name the document type from the catalog journal's own vocabulary — item, item_price, item_identifier, item_batch, book, unit, tax_rate, user, store, store_settings, numbering_policy, terminal_config — with the document key, plus the price version for a price set. (D1, D27; 0001 `catalog_changes`)

<!-- OPEN parity-stock #F1 / parity-repack #F1: api-routes §3.3's delta entity list omits item_batch and book, so a batch never reaches a register as a delta. -->

#### Scenario: a stock receipt of May soap

- **GIVEN** a receipt creates batch MAY-24 for soap with MRP ₹200 and a 1000 g tier at 11000 paise
- **WHEN** the receipt is saved
- **THEN** the journal gains an `item_batch` row for that batch and an `item_price` row naming the item and version 9
- **AND** a later price change to the same batch carries a new version rather than repeating version 9

### Requirement: The cursor advances over every row read

The response cursor SHALL be the last raw sequence in the page, including rows dropped by store filtering, and `has_more` SHALL come from the raw lookahead row; a fully filtered page SHALL return an empty change list and still advance the cursor. (api-routes §4, §9.10)

#### Scenario: a page of another store's cashiers

- **GIVEN** a page of 200 changes mentions only Secunderabad users and Kukatpally pulls
- **WHEN** the projection drops every row
- **THEN** the response carries no changes and the cursor is the last raw sequence read
- **AND** the terminal commits that cursor and asks for the next page

### Requirement: Store projection keeps other stores out

The pull SHALL project each document for the token: items, units and tax rates to every store; prices limited to the default and the token's store; another store's identifier or roster user as a delete; store rows, settings and numbering policies only for the token's store; terminal configuration only for the token's terminal. (D9; api-routes §4)

#### Scenario: an EAN moving between two stores

- **GIVEN** EAN 8901234567890 moves from Kukatpally to Secunderabad
- **WHEN** both stores pull the same sequence
- **THEN** Kukatpally receives a delete for that identifier and Secunderabad an upsert
- **AND** Kukatpally never receives Secunderabad's cashier PIN hashes

### Requirement: The epoch makes a reset sticky

`schema_meta.sync_epoch` SHALL be bumped on every database restore, re-create or destructive migration — never on routine compaction — to the pre-restore maximum plus one, and any epoch mismatch SHALL answer 410 regardless of head. (0002; api-routes §4)

#### Scenario: a Time Travel restore to 09:00

- **GIVEN** every terminal holds epoch 1 and the database is restored to 09:00 and then bumped to epoch 2
- **WHEN** a terminal pulls with epoch 1
- **THEN** it receives 410 `epoch_mismatch` with the new epoch
- **AND** it keeps billing on its old catalog until a snapshot swap completes

### Requirement: A stale or ahead cursor is a 410, not a gap

The response SHALL carry `pruned_below` and `reset_to` as the prune floor minus one; a cursor below `reset_to` SHALL answer `stale_cursor` and one above head `cursor_ahead`; the floor SHALL advance only to the lowest applied sequence over active devices. (D1; 0008 `devices.last_catalog_seq`; api-routes §4)

#### Scenario: a terminal back after a hundred days

- **GIVEN** the log was pruned below 9000 while a register was offline holding sequence 8412
- **WHEN** it pulls from 8412
- **THEN** it receives 410 `stale_cursor` with `reset_to` 8999
- **AND** pulling from 8999 returns the first retained row, while a device that never pulled holds the floor at 0

### Requirement: Gaps are detected, not assumed

The terminal SHALL verify that raw sequences strictly increase, that the last raw sequence read equals the new cursor, that the last kept change is at or below the cursor, and that no more pages means the cursor equals head; a violation SHALL be retried once and then reset. (api-routes §4)

#### Scenario: sparse sequence numbers

- **GIVEN** a rolled-back allocation leaves the page with sequences 8412, 8415 and 8416
- **WHEN** the terminal applies it
- **THEN** the page is accepted and the cursor becomes 8416
- **AND** a page whose last raw sequence disagrees with the cursor triggers one retry and then a snapshot

### Requirement: A snapshot is paged and staged

A snapshot SHALL be paged by document type; the first page freezes epoch, head and prune floor, later pages re-read them and answer `snapshot_invalidated` when they move; the terminal keeps billing until it swaps shadow tables and the cursor in one transaction. (api-routes §3.3, §9.11)

<!-- OPEN parity-stock #F1: batches and books are in no snapshot page and no delta entity list. -->

#### Scenario: a fresh register and a floor that moves

- **GIVEN** a new register snapshots items, prices, identifiers, users, reference and store across six pages
- **WHEN** the prune floor moves between page two and page three
- **THEN** page three answers 410 `snapshot_invalidated` with the live values
- **AND** the register discards the staged pages, keeps billing on the old catalog and restarts

### Requirement: The register applies by version and advances last

The terminal SHALL apply a page in one local transaction, in ascending version order, write the cursor only after every row applied, and treat a crash before that as a replay of the same page. (D1 revised; api-routes §4)

<!-- OPEN parity-catalog #11: the pull contract reads sync_state, but no migration creates it. -->

#### Scenario: power cut in the middle of a page

- **GIVEN** a page of 200 changes is half applied to the local database
- **WHEN** the register restarts
- **THEN** its stored cursor is still the previous sequence and the whole page is applied again
- **AND** no duplicate price row results, because the document carries versions

### Requirement: A rebuild publishes a generation only when complete

A chunked re-seed SHALL record a rebuild generation, mark it complete only after the last chunk is committed together with its end sequence, and publish the new floor only then; a snapshot taken mid-rebuild SHALL finish or answer `snapshot_invalidated`. (deploy-inventory §7; 0008 `catalog_rebuild`)

<!-- OPEN parity-catalog #21: catalog_rebuild accepts complete = 1 with end_seq NULL, so a half-finished generation can be published past a gap. -->

#### Scenario: re-seeding forty thousand documents in twelve chunks

- **GIVEN** the compaction writes twelve chunks and the last one ends at sequence 40000
- **WHEN** the new floor is published
- **THEN** the generation is complete with an end sequence of 40000
- **AND** terminals still behind pull the old floor normally until that moment

### Requirement: A fresh terminal starts at zero or with a snapshot

A newly enrolled or re-enrolled terminal SHALL start at sequence 0 or with `snapshot=1`, never at the head the token reports, and every catalog response SHALL carry the token's store and terminal so a store change is detected. (api-routes §3.2, §4)

#### Scenario: a terminal enrolled at head 8431

- **GIVEN** `/v1/token` reports head 8431 for a brand-new terminal
- **WHEN** it starts syncing
- **THEN** it asks for `since=0` or a snapshot and receives the complete catalog
- **AND** a response naming a different store than its pin takes the same staged reset
