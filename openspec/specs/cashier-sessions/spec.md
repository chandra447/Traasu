# Cashier Sessions Specification

## Purpose

Say who is standing at a register at the moment a bill is made: one live session per terminal, a session window the bill must fall inside, offline PIN attribution, and a logout that works with no network — with every mismatch accepted and flagged rather than costing the shop a sale.

## Requirements

### Requirement: One live session per terminal

The system SHALL allow at most one cashier session per terminal with no end time, SHALL answer a second login while one is live with `409 session_already_open`, and SHALL treat switch-user as a logout followed by a login rather than two carts. (D16; 0006 uq_staff_sessions_live)

#### Scenario: A second cashier at the same counter

- **GIVEN** Ravi logged in on counter 2 at 10:14 with a cart of three items
- **WHEN** Sita tries to log in on counter 2 at 10:26
- **THEN** the login is refused with `409 session_already_open` and Ravi's session stays live
- **AND** Sita can log in after Ravi logs out at 14:02

### Requirement: Every bill names the session it was made in

The system SHALL record the staff session on the bill and SHALL judge validity by the session window rather than by push order, so a bill created before a logout stays valid when pushed afterwards. (D16; bills.staff_session_id)

#### Scenario: A bill pushed after its cashier logged out

- **GIVEN** a session from 10:14 to 14:00 and a bill created at 13:58 for 960 paise
- **WHEN** the register, still offline, pushes that bill at 14:05
- **THEN** the bill is accepted and attributed to that session
- **AND** no flag is set, because the bill falls inside the window

### Requirement: A session-window mismatch is flagged, never rejected

The system SHALL accept a bill whose creation time falls more than 5 minutes outside its session window, or whose session names another user, terminal, store or auth method, and SHALL set `unverified_staff` with `staff_window_mismatch`; window checks SHALL use session order rather than wall clock, so a clock step cannot invalidate a shift. (D16 revised; 0008 dropped the window triggers, bills.unverified_staff)

#### Scenario: A clock step and a switched user

- **GIVEN** a session started at 1,000 seconds on the terminal's clock
- **WHEN** a bill is pushed with a creation time of 998 seconds, and another with 2,005 seconds after the session ended at 2,000 for switch-user
- **THEN** both bills are stored and attributed, and both carry `unverified_staff`
- **AND** covered by check-invariants: "bill 2 s before session start (clock step)"
- **AND** covered by check-invariants: "cart finished after switch-user"

### Requirement: A password session's assertion is verified once, at first push

The system SHALL verify a password session's assertion at first push — session id equal to the bill's session, issue time within 300 seconds of the session start, expiry at or after the session start — and SHALL record the verification time on the session; a failed verification SHALL leave the session unverified instead of aborting anything. (D7, D16; staff_sessions.assertion_verified_at)

#### Scenario: An assertion issued before the login

- **GIVEN** a session started at 10:14:00 and an assertion issued at 10:13:20
- **WHEN** the first push of that session arrives
- **THEN** the assertion verifies and `assertion_verified_at` is recorded
- **AND** an assertion issued at 10:06 fails verification, the session row is still stored, and no bill is rejected

### Requirement: An offline PIN shift is attribution, not authorisation

The system SHALL store an offline PIN session with `auth_method` `pin_offline` and no assertion, SHALL flag every bill in it `unverified_staff`, and SHALL verify the PIN only on the terminal against the cashier's stored hash; no route SHALL set or read a PIN. (D7, D8, D16; staff_credentials, api-routes.md §8)

#### Scenario: A shift that never reached the server

- **GIVEN** a cashier who authenticates with a 6-digit PIN at 08:00 on a day the uplink is down
- **WHEN** she bills 2 kg of sugar for 9,600 paise and the push happens at 20:00
- **THEN** the session row is stored with `pin_offline` and no assertion
- **AND** the bill is accepted and flagged `unverified_staff`, with the cashier's user id kept

### Requirement: A password bill with no session is accepted and flagged

The system SHALL accept a bill claiming `password` authentication with no session and SHALL set `unverified_staff` with `password_bill_needs_session`. (D16; api-routes.md §6)

#### Scenario: A terminal that lost its session row

- **GIVEN** a bill of 1,029 paise carrying `auth_method: "password"` and no session id
- **WHEN** it is pushed
- **THEN** the bill is stored and flagged `unverified_staff`
- **AND** the money is reported, because a completed sale is never parked for attribution

### Requirement: Logout is local-first

The system SHALL end the session in the terminal's own database immediately, queue the row for the next push, and SHALL offer the online logout route only to revoke within seconds; that route SHALL be idempotent and return the stored end time. (D16; staff_sessions.ended_at)

#### Scenario: Logging out with the network down

- **GIVEN** an uplink that has been down since 17:00 and a cashier logging out at 18:30
- **WHEN** the terminal ends the session locally and the push happens at 19:00
- **THEN** the register shows its login screen at 18:30 and the cashier's session ends at 18:30 on the server
- **AND** a bill created at 18:25 and pushed at 19:00 is still valid and accepted

### Requirement: A session closes once

The system SHALL apply an end time to a session once, SHALL treat a repeated close as a no-op in the session acknowledgement, and SHALL refuse a later different end time with `session_already_closed`. (D16; 0006 trg_staff_sessions_close_once)

#### Scenario: A duplicated close from two pushes

- **GIVEN** a session ended at 2,000 seconds for switch-user
- **WHEN** a retried push attempts to set 3,000 seconds
- **THEN** the write fails with `session_already_closed` and the stored end time stays 2,000
- **AND** covered by check-invariants: "session close is one-way"

### Requirement: Every session records why it ended

The system SHALL record an end reason from `logout`, `switch_user`, `idle`, `shift_close`, `revoked` and `crash`, and SHALL close a stale open session at startup with `crash` before a new login is allowed. (D16; staff_sessions.end_reason)

#### Scenario: A power cut during a shift

- **GIVEN** a session opened at 09:00 and left open when the counter lost power at 20:00
- **WHEN** the register starts the next morning at 09:00
- **THEN** the stale session is closed with `crash` before the login screen accepts a cashier
- **AND** no second live session is created for that terminal

### Requirement: Session rows reach the server before their bills

The system SHALL apply pushed session rows in start-time order before any bill in the same push, as upserts that can only close a session, and SHALL acknowledge each row so the terminal can clear it. (D16 revised; api-routes.md §3.4)

#### Scenario: A push carrying two shifts and 100 bills

- **GIVEN** an outbox with 2 session rows and 100 bills spanning both
- **WHEN** the push is applied
- **THEN** both session rows are stored first and acknowledged
- **AND** a replay of the same push acknowledges them with `duplicate: true` and changes nothing

### Requirement: Session times are terminal time, bounded against the server

The system SHALL take session start and end in terminal-clock time, bound them against the server's time (no more than 5 minutes ahead, no more than 24 hours behind), and store the receipt time separately, so a clock jump shows up as a flag and never as a refused sale. (D16; staff_sessions.started_at, received_at)

#### Scenario: A terminal clock 4 minutes fast

- **GIVEN** a terminal whose clock runs 4 minutes ahead of the server
- **WHEN** it opens a session and pushes a bill two days later than its own session start
- **THEN** the session start is stored as sent and the receipt time is stored separately
- **AND** the bill is flagged `unverified_staff` rather than refused

### Requirement: First-run activation replaces the one-time password

The system SHALL let a cashier replace an admin-issued one-time password, set a 6-digit PIN and open the first session, returning the session id, its expiry and the shift, and SHALL refuse a weak credential with `422 weak_credential`; a store outside the user's scope is `403 store_forbidden` and an inactive user is `403 user_inactive`. (D16, D17; api-routes.md §2 staff routes)

#### Scenario: A new cashier's first shift

- **GIVEN** an owner who created Sita as a cashier for Kukatpally with a one-time password
- **WHEN** she activates at counter 2 with a new password and PIN 481920, and later signs in with the old password
- **THEN** activation opens a session and returns a 12-hour assertion
- **AND** the old password answers `401 invalid_credentials` on the next login

### Requirement: An open cart blocks switch-user

The system SHALL refuse switch-user on the terminal while a cart holds items, requiring the cart to be completed or cleared first. (D16 revised; terminal session rules)

#### Scenario: Switching with three items in the cart

- **GIVEN** Ravi's cart holding 3 items worth 21,430 paise
- **WHEN** Sita is chosen from the switch-user screen
- **THEN** the switch is blocked until the cart is completed or cleared
- **AND** no second session is opened and no bill is created for Sita
