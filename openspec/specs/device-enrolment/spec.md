# Device Enrolment Specification

## Purpose

Bind a physical register to one store and terminal through a single-use code and an owner's approval, issue it a five-minute device token, and bound how long it can keep billing without reaching the server, so a stolen code or a cloned disk costs the shop data at most, never a franchisee's money.

## Requirements

### Requirement: A code binds a pending device and nothing more

The system SHALL create the device in a pending state at enrolment, holding no lease and no catalog, and SHALL issue no token until an owner approves it. (D5; devices, enrol_codes)

#### Scenario: A code read from a drawer

- **GIVEN** an owner-minted code that a passer-by photographed, and the install id `9f2c41d8a0b34e77b6e1c2d3f4a59601`
- **WHEN** that install enrols and then calls `/v1/token` every 30 seconds
- **THEN** enrolment answers `status: "pending"` and every token call answers `403 approval_pending` with no token
- **AND** no catalog row, bill route or stock row is served to it

<!-- OPEN parity-billing #P: enrolment returns no local sequence seed, so a re-installed register starts at local_seq 1 and every bill collides with uq (store, terminal, local_seq) and is parked duplicate_seq forever. -->

### Requirement: Enrolment codes are short-lived and single-use

The system SHALL mint a 40-bit code that lives 15 minutes, is stored only as an HMAC, is bound to an existing store and terminal inside one tenant, and is consumed by one enrolment; minting a new code for a terminal SHALL invalidate that terminal's unused codes. (D5; enrol_codes.code_hash, used_at, used_device_id)

#### Scenario: An expired and a superseded code

- **GIVEN** code `A-7K2M-9QX4` minted at 10:00 and a second code minted for the same terminal at 10:10
- **WHEN** the first code is typed at 10:20 and the second at 10:12
- **THEN** the first answers `410 code_expired` and a replay of the consumed second answers `409 code_used`
- **AND** the superseded first code answers `404 code_not_found`

### Requirement: The credential is returned once and stored hashed

The system SHALL return the device credential exactly once at enrolment, store only its SHA-256 hash and a short lookup prefix, require the prefix to be unique, and expect the terminal to pin the credential, install id, tenant id, app id and API base in the operating system keychain. (D5, D6; devices.credential_hash, credential_prefix)

#### Scenario: A credential is never re-readable

- **GIVEN** an enrolment response carrying a 43-character credential beginning `v1_9tR2`
- **WHEN** the enrolment response is lost before the terminal persists it
- **THEN** the credential cannot be fetched again and a new code must be minted
- **AND** the database holds only the hash, the prefix and no plaintext

### Requirement: The owner's approval activates a pending device

The system SHALL set the approving user and time on a pending device, return `status: "active"`, and answer an already-approved device with the same body and `duplicate: true`; approving a revoked device SHALL answer `409 device_revoked`. (D5, T13; devices.approved_at, approved_by)

#### Scenario: Approval from the admin web

- **GIVEN** a device pending for 2 minutes at counter 2 of the Kukatpally store
- **WHEN** the owner approves it and the terminal retries `/v1/token`
- **THEN** `approved_at` and `approved_by` are recorded and a 5-minute token is issued
- **AND** a second approval call returns `duplicate: true` and mints nothing

### Requirement: The token lives five minutes and is the only sync credential

The system SHALL issue a token valid for 300 seconds, mint a fresh `jti` on every call, return the lease expiry, the catalog diagnostics and a rotation target, and SHALL have no refresh token: the device credential is the refresh mechanism. (D6; api-routes.md §3.2, devices)

#### Scenario: A token used past its expiry

- **GIVEN** a token issued at 09:00:00 for the Kukatpally counter
- **WHEN** the register pushes a bill of 4,250 paise at 09:06:00 with that token
- **THEN** the response is `401 token_expired` and the outbox row is kept
- **AND** the next `/v1/token` call issues a new 5-minute token and the push succeeds

### Requirement: A device token reaches device routes only

The system SHALL carry `typ`, `sub`, `aud`, `tenant_id`, `store_id`, `terminal_id`, `scope`, issue time, expiry and `jti` on the token, SHALL accept it only where `typ` is `device` and `scope` contains `sync`, and SHALL ignore an admin cookie presented to a device route. (D6, D17; api-routes.md §1)

#### Scenario: A device token on an admin route

- **GIVEN** a valid 5-minute device token for counter 2
- **WHEN** it is presented to `GET /v1/admin/items`
- **THEN** the response is `401 wrong_plane` and no admin data is returned
- **AND** an admin session cookie on `POST /v1/bills` is ignored and answers `401 missing_token`

### Requirement: The credential is bound to one install

The system SHALL require the install id on every token call, SHALL answer `401 device_moved` on a mismatch, and SHALL enforce one live device per install id while allowing a re-enrolment after revocation. (D5; devices.install_id, uq_devices_install_live)

#### Scenario: A cloned disk

- **GIVEN** a credential copied to a second machine whose install id is `7a1e...c9` while the enrolled one is `9f2c...01`
- **WHEN** the clone calls `/v1/token`
- **THEN** the response is `401 device_moved`
- **AND** the terminal wipes its credential, keeps its bills and catalog, and asks for a new code
- **AND** a second live install reusing an existing install id is refused

### Requirement: Revocation takes effect within the token lifetime

The system SHALL record revocation on the device row, SHALL check that row on every token call, and SHALL bound the effect on in-flight tokens by the 5-minute lifetime; the revocation cache SHALL be a shortcut, never the truth. (D6; devices.revoked_at)

#### Scenario: Revoking a lost register

- **GIVEN** a device holding a token issued at 09:00 and revoked by the owner at 09:01
- **WHEN** it pushes a bill at 09:03 and calls `/v1/token` at 09:04
- **THEN** the push is accepted under the still-valid token and the token call answers `401 device_revoked`
- **AND** the terminal stops sync and billing, shows the revoked state and offers an export path
- **AND** the revocation is read from the database, so a stale cache cannot extend it

### Requirement: A device is revoked, never deleted

The system SHALL refuse a delete of a device row with `device_deactivate_only`, because bills and sessions reference it. (D25; 0008 trg_devices_no_delete)

#### Scenario: Deleting a device row

- **GIVEN** a device enrolled at the Kukatpally store
- **WHEN** a delete reaches the device row
- **THEN** the write fails with `device_deactivate_only` and the row survives
- **AND** covered by check-invariants: "delete a device"

### Requirement: The offline lease bounds billing without the server

The system SHALL extend the device's lease on every successful sync for a default of 7 days, and SHALL stop creating bills once the lease has expired, showing a blocking dialog until a token refresh succeeds. (D4; devices.lease_expires_at)

#### Scenario: A shop offline for eight days

- **GIVEN** a counter that last synced on 2026-11-08 at 14:00, holding a lease to 2026-11-15 at 14:00
- **WHEN** the cashier starts the 1,200th bill of the day at 14:01 with the uplink still down
- **THEN** the register refuses to start the bill and shows a blocking dialog naming the last successful sync
- **AND** every bill already saved, its stock movements and its outbox rows are intact
- **AND** billing resumes after one successful token refresh

<!-- OPEN parity-billing #O: a lease that silently expires stops a shop billing with no server-side signal and no audit row when it reconnects. -->

### Requirement: The credential rotates before it ages out

The system SHALL return a rotation target once a credential is older than 90 days, SHALL mark the old credential superseded on the first use of the new one, and SHALL require the terminal to persist the new credential before its next call. (D6; devices.credential_hash)

#### Scenario: The ninety-first day

- **GIVEN** a credential enrolled on 2026-08-16 and a token call on 2026-11-15
- **WHEN** the response carries `rotate_to`
- **THEN** the terminal persists it before its next request
- **AND** a later call with the previous credential after the new one has been used answers `401 invalid_credential`

### Requirement: An outdated register is told to upgrade

The system SHALL answer `426 upgrade_required` on device routes when the client version is below the release's minimum terminal version, exempting the heartbeat and the health route, and SHALL keep billing working offline. (D6; MIN_TERMINAL_VERSION)

#### Scenario: A register two releases behind

- **GIVEN** a minimum terminal version of 1.4.0 and a register reporting 1.3.9
- **WHEN** it pushes bills and then sends a heartbeat
- **THEN** the push answers `426 upgrade_required` and the heartbeat still answers 200 with `update_required: true`
- **AND** the register keeps billing offline while sync is paused

### Requirement: No admin route can mint a credential

The system SHALL keep credential issuance to enrolment only: approval changes a pending device's state and returns no credential, and no admin route creates a device. (D5; api-routes.md §8)

#### Scenario: An owner trying to shortcut enrolment

- **GIVEN** an owner's admin session
- **WHEN** it calls an admin route to create a device or fetch a credential
- **THEN** no such route exists and no credential is returned
- **AND** the only path to a credential is a minted code plus enrolment plus approval
