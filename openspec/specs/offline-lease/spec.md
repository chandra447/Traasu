# Offline Lease Specification

## Purpose
Bounds how long a register may keep selling with no successful sync, and says exactly what a register may and may
not do while it is offline. It keeps the shop billing through a dead uplink while removing the reward for a
register that never reconnects, and it never makes a completed sale the thing that is blocked.

## Requirements

### Requirement: Every successful sync refreshes the register's lease
The system SHALL issue a lease expiry with every token exchange and SHALL extend it on each successful sync, so a
register that is reaching the server never runs out of authority. (D4; devices.lease_expires_at, POST /v1/token)

#### Scenario: A register that syncs every morning
- **GIVEN** the Kukatpally terminal 2 synced at 09:00 on 2026-11-14 and has a 7-day lease
- **WHEN** it exchanges its device credential for a token at 09:00 on 2026-11-15 and 2026-11-16
- **THEN** its stored lease expiry moves to 2026-11-22 and then 2026-11-23
- **AND** no dialog interrupts billing on any of those days

### Requirement: Past the lease the register stops creating bills
The system SHALL refuse to create a new bill once the stored lease expiry has passed, and SHALL show a blocking
dialog that says the register must reach the server, instead of failing silently at the next push. (D4;
devices.lease_expires_at)

#### Scenario: A register offline for nine days
- **GIVEN** the lease expired at 18:00 on 2026-11-21 and the register has had no network since
- **WHEN** the cashier starts a bill at 09:15 on 2026-11-22
- **THEN** no bill is created and billing is blocked
- **AND** the dialog stays until a sync succeeds and refreshes the lease

<!-- OPEN parity-billing #O: 7 days is too short for Indian retail — the report proposes a per-store value in terminal_config defaulting to 14 with warnings at 5 and 2 days. -->

### Requirement: A revoked register stops billing immediately
The system SHALL stop creating bills as soon as the register learns it is revoked, without waiting for the lease to
expire and without retrying in a loop. (D4, D6; api-routes §4 terminal states)

#### Scenario: The owner revokes a stolen register
- **GIVEN** a register with 5 days left on its lease
- **WHEN** its next token request is answered `401 device_revoked`
- **THEN** billing stops at once and a blocking dialog says the register is revoked
- **AND** the register offers an export of its unsynced bills instead of retrying

### Requirement: A bill already written survives the stop and is accepted later
The system SHALL keep every bill and line written before the stop, and the server SHALL accept them when the
register reconnects, because a bill from a past-lease or revoked device records a sale that happened. (D4;
d1-schema.md offline lease section)

#### Scenario: Forty bills written before the stop
- **GIVEN** the register wrote 40 bills on 2026-11-21 and then learned it was revoked
- **WHEN** its bills are pushed after the device is approved again
- **THEN** all 40 are accepted and stored
- **AND** none is parked or dropped for having been written past the lease

### Requirement: Billing inside the lease needs no network and no server route
The system SHALL complete a sale entirely against local state while the lease is valid, and SHALL never read the
network, the token or a server response on the sale path. (D26, D3; register local SQLite)

#### Scenario: A day of sales with the router off
- **GIVEN** the lease is valid until 2026-11-21 and the shop's router has been off since morning
- **WHEN** the cashier bills 300 customers, including 40 with discounts inside the ceiling
- **THEN** every sale completes and prints
- **AND** no step of a sale waits on a network call

### Requirement: The lease is enforced from local state before a bill is started
The system SHALL hold the lease expiry locally and SHALL consult that stored value before a bill is started, so the
sale transaction itself carries no policy read and never calls the server. (D4; d1-schema.md offline lease section)

#### Scenario: The lease running out mid-evening
- **GIVEN** the stored lease expiry is 18:00 on 2026-11-21
- **WHEN** one bill is started at 17:58 and the next at 18:01
- **THEN** the 17:58 bill is saved
- **AND** the 18:01 bill is refused with the blocking dialog and nothing is written for it

### Requirement: A token expiry pauses sync and never billing
The system SHALL continue billing when its device token expires or a sync fails, and SHALL restrict the consequence
of a failed exchange to the pause of syncing. (D6; api-routes §3.2)

#### Scenario: A token that expired an hour ago
- **GIVEN** the register's 5-minute token expired and the token route is unreachable
- **WHEN** the cashier takes the next three sales
- **THEN** all three bills are written and printed
- **AND** the outbox grows until the exchange succeeds

### Requirement: Offline, a discount is bounded by the ceiling and not by a refusal
The system SHALL allow an offline discount only up to the store ceiling or a cached unexpired approval grant, and
SHALL complete the sale at that bound rather than refusing the sale. (D3; approval_grants, store_settings)

#### Scenario: 20% asked for with no grant cached
- **GIVEN** the register is offline, holds no unexpired grant, and the store ceiling is 10%
- **WHEN** the cashier asks for a 20% discount on a 6500 paise line
- **THEN** the line is billed with 650 paise of discount, the ceiling
- **AND** the sale completes and no bill is held back

### Requirement: Offline, an over-quantity or out-of-stock sale only warns
The system SHALL let an offline register sell stock it cannot see, and SHALL warn when the quantity exceeds the
batch on hand instead of blocking the line. (D27, D28; batch_stock)

#### Scenario: The last soap sold on two registers
- **GIVEN** the shop has one pack of soap at MRP ₹100 and two registers are offline
- **WHEN** both registers sell that pack inside their leases
- **THEN** both sales complete
- **AND** the on-hand count reconciles to -1 when the stock pull next runs
