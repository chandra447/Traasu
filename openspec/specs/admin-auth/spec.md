# Admin Auth Specification

## Purpose

Authenticate people in the admin web and authorise what they may do: one library-owned session per tenant host, our role and store scope on top, TOTP and Google sign-in, a CSRF token on every write, and the rate limits that make a password endpoint safe on a shared back-office browser.

## Requirements

### Requirement: The admin and device planes never share a handler

The system SHALL authenticate the admin web with a session cookie and the register with a device token, SHALL accept neither credential on the other plane's routes, and SHALL answer `401 wrong_plane` when one is presented to the other. (D17; api-routes.md §1)

#### Scenario: Each credential on the wrong plane

- **GIVEN** a live admin session cookie and a valid 5-minute device token
- **WHEN** the device token is presented to `GET /v1/admin/items` and the cookie to `POST /v1/bills`
- **THEN** the first answers `401 wrong_plane` and the second `401 missing_token`
- **AND** no route accepts both credentials

### Requirement: The auth instance is built per request for the routed tenant

The system SHALL construct the admin auth instance inside each request from the routed tenant's database, that tenant host's base URL and trusted origins, and SHALL never cache an instance across requests, because a module-level instance wrote one tenant's owner and session into another tenant's database. (D17 revised, D20; auth-better-auth.md)

#### Scenario: One owner email on two hosts

- **GIVEN** the same email registered as owner in two tenants, with a session issued on reliance.pos.app
- **WHEN** that cookie is presented to coles.pos.app
- **THEN** the Coles host resolves no session, because its instance is bound to the Coles database and host
- **AND** signing in on coles.pos.app creates a session only in the Coles database

### Requirement: The library owns its own tables

The system SHALL let the auth library own its user, session, account, verification, two-factor and rate-limit tables with its own 32-character ids and ISO-8601 text dates, SHALL cross into domain tables only through the unique `users.auth_user_id` link, and SHALL add no domain column to the library's user table. (D17 revised; 0007 auth_*, users.auth_user_id)

#### Scenario: Resolving an admin request

- **GIVEN** a signed-in owner whose library user id is `kR2m...` and whose domain user id is `0192f3c0-...8501`
- **WHEN** an admin route resolves the session
- **THEN** the domain user is found by the unique `auth_user_id` link, once per request
- **AND** a cashier row has no `auth_user_id` and no library user at all

### Requirement: An admin session expires on idle and absolutely

The system SHALL expire an admin session after 12 hours without activity, refresh it at most once an hour, and refuse any session created more than 30 days ago with `401 session_expired` and delete it, whatever the library's own expiry says. (D17; auth_session.created_at, expires_at)

#### Scenario: A session refreshed forever

- **GIVEN** a session created on 1 November at 09:00 and refreshed hourly
- **WHEN** day 31 arrives
- **THEN** the request is refused with `401 session_expired` and the session row is deleted
- **AND** an unused session is refused 12 hours after its last activity, not 7 days later

### Requirement: Sign-up is closed and the one-time password gates the panel

The system SHALL refuse public sign-up, create domain users only through the owner's user route (which also creates a library user for owner and staff and returns a one-time password shown once), and SHALL gate every admin route except the current-user, change-password and sign-out routes with `403 password_change_required` while the password change is pending. (D17 revised; api-routes.md §3.5, users.must_change_password)

#### Scenario: A staff user's first sign-in

- **GIVEN** an owner who created Sita as staff for Kukatpally with a one-time password
- **WHEN** she signs in and opens `/v1/admin/items`
- **THEN** `POST /v1/admin/auth/sign-up/email` is refused for anyone, and her own request answers `403 password_change_required`
- **AND** after changing the password the same request answers 200

### Requirement: A cashier cannot reach the admin web

The system SHALL keep cashiers out by construction, because a cashier has no library user and therefore no credential, and SHALL answer `403 forbidden_role` for an authenticated library user with no linked domain user or one that is inactive. (D17 revised; users.auth_user_id, auth_user)

#### Scenario: A cashier's email at the sign-in page

- **GIVEN** a cashier row with a name, a PIN and no `auth_user_id`
- **WHEN** that email and any password are submitted, and separately a valid session belongs to a deactivated user
- **THEN** the first gains no session and the second gets `403 forbidden_role` with no admin data
- **AND** the check holds even if a route forgets its own role test

### Requirement: Authorisation is our role and store scope

The system SHALL authorise from the domain role (owner, staff, cashier) and store scope: owners write, owners and staff read, a store outside the user's scope is `403 store_forbidden`, an action outside the role is `403 forbidden_role`, and a change that would leave no active owner is `409 last_owner`. (D17; users.role, user_stores)

#### Scenario: A staff report outside scope

- **GIVEN** staff scope for Kukatpally only
- **WHEN** that user requests the day summary for KPH, and separately the owner deactivates the only other owner
- **THEN** the report answers `403 store_forbidden` and the deactivation answers `409 last_owner`
- **AND** a staff session attempting to change another user's role answers `403 forbidden_role`

<!-- OPEN parity-platform #8: /v1/approvals admits only an active owner, and users carries no approval capability, so in a five-store chain a staff manager cannot approve an over-ceiling discount. -->

### Requirement: Every admin write carries a CSRF token

The system SHALL issue a signed CSRF token bound to the session from the current-user route, SHALL require it in the request header on every non-read admin route, SHALL check the request's origin against the tenant host, and SHALL answer `403 csrf_failed` on a miss; no readable CSRF cookie SHALL exist. (D17; api-routes.md §1)

#### Scenario: A write with a missing token

- **GIVEN** a live owner session and a form POST from another origin
- **WHEN** it arrives without the header, and then again with the token returned by the current-user route
- **THEN** the first answers `403 csrf_failed` and writes nothing
- **AND** the second answers 200, proving the token is per-session and required

### Requirement: The session cookie is host-only

The system SHALL issue the session cookie with the `__Host-` prefix, Secure, HttpOnly, SameSite Lax, path `/` and no domain attribute, and SHALL serve the admin web from the same origin as the API so the cookie stays same-origin per tenant host. (D17; api-routes.md §3.5)

#### Scenario: A cookie does not travel between tenants

- **GIVEN** a session cookie set on coles.pos.app
- **WHEN** the browser requests reliance.pos.app
- **THEN** the cookie is not sent, because it has no parent domain
- **AND** a cross-site form post carries no usable session

### Requirement: TOTP two-factor is available to every admin user

The system SHALL support TOTP enrolment and verification: an enrolled user's sign-in SHALL return a two-factor challenge instead of a session, enrolment SHALL return the authenticator URI and backup codes, and disabling SHALL require a CSRF-protected write. (D17; 0007 auth_two_factor)

#### Scenario: An owner with an authenticator

- **GIVEN** an owner who enabled two-factor and stored the backup codes
- **WHEN** the owner signs in with the correct password
- **THEN** the response carries the two-factor challenge and no session cookie
- **AND** a correct TOTP code completes the sign-in, and a backup code works once instead of the app

### Requirement: Google sign-in never provisions a user

The system SHALL keep one OAuth client for the deployment with a per-tenant redirect URI, SHALL create a library user on a callback only when the email already links to a domain user, SHALL leave email and password enabled for at least one owner, and SHALL answer `403 forbidden_role` for an email with no linked domain user. (D17; Google OAuth, api-routes.md §3.5)

#### Scenario: A personal Gmail account and a lost Google account

- **GIVEN** an unlinked Gmail address and an owner whose Google account is lost
- **WHEN** the first completes the callback and the second signs in with email and password
- **THEN** the first answers `403 forbidden_role` and no user row is created
- **AND** the owner still reaches the admin web, so the business cannot lose access to its own POS

### Requirement: Sign-in is rate-limited in the database

The system SHALL limit sign-in to 10 attempts a minute per address and 5 per email, admin reads and other auth routes to 120 a minute per session, and admin writes to 30 a minute per session, SHALL count attempts again with atomic counters inside the tenant database, and SHALL answer `429` with `Retry-After` when either bound is hit. (D17; 0007 auth_rate_limit)

#### Scenario: Six wrong passwords in a minute

- **GIVEN** an owner email targeted by six wrong passwords inside 60 seconds
- **WHEN** the sixth attempt arrives
- **THEN** it answers `429` with `Retry-After`, before the password hash is computed
- **AND** the counters live in the tenant database, so two isolates cannot both admit the same attempt

### Requirement: There is one password check in the system

The system SHALL hash admin passwords with the library's own password hashing and SHALL verify a manager's password for a money approval through that same verifier rather than a second hash, so a user has one password on both planes. (D17, D8; api-routes.md §3.5)

#### Scenario: The same password on both planes

- **GIVEN** an owner whose admin password was set through the admin web
- **WHEN** the owner authorises a discount override at the register
- **THEN** the approval route accepts that one password and mints a 12-hour grant
- **AND** changing the password in the admin web changes what the approval route accepts

### Requirement: Admin writes are allowlisted and revocation follows a credential change

The system SHALL accept only explicit allowlisted fields on an admin write — never a password hash, a PIN hash, an id or a creation time, and never a role from a non-owner — SHALL revoke a user's live sessions when the user is deactivated, other sessions on a password change, and all sessions on demand. (D17; api-routes.md §1, §2)

#### Scenario: Deactivating a staff user mid-session

- **GIVEN** Sita signed in on two browsers
- **WHEN** the owner deactivates her
- **THEN** both sessions stop working on their next request and she gets `403 forbidden_role`
- **AND** a body attempting to set `role: "owner"` from a staff session is refused before any write
