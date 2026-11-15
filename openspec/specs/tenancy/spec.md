# Tenancy Specification

## Purpose

Decide, before any handler runs, which enterprise a request belongs to: the Host header selects a compiled routing entry, that entry's database must name the same tenant, and every credential is derived for that tenant, so a mistake in the routing table or a stolen token cannot read or write another franchisee's bills.

## Requirements

### Requirement: The Host header selects the tenant

The system SHALL resolve the tenant from the request's Host header against a routing map compiled at deploy time, and SHALL take tenant, store and terminal identity from that resolution rather than from the request body. (D20; deploy-inventory.md §2)

#### Scenario: A bill push carries the token's store, not the body's

- **GIVEN** a fleet where reliance.pos.app routes to `tnt_reliance` and coles.pos.app routes to `tnt_coles`
- **WHEN** a register on coles.pos.app pushes bills worth 18,750 paise with a body `store_id` naming a reliance store
- **THEN** the request is served by the Coles database only
- **AND** the body `store_id` is ignored and the token's store and terminal are used

### Requirement: An unknown Host is refused before anything is read

The system SHALL reject a request whose Host has no routing entry with `421 unknown_host` without opening a database or reading a row. (D20; api-routes.md §1)

#### Scenario: A decommissioned hostname

- **GIVEN** `old-shop.pos.app` was removed from the routing map at the last deploy
- **WHEN** a register still configured with that API base sends `GET /v1/catalog?since=8412&epoch=1`
- **THEN** the response is `421 unknown_host` with no database read
- **AND** the terminal treats its stored API base as wrong and needs re-enrolment

### Requirement: The routed database must name the same tenant

The system SHALL compare `schema_meta.tenant_id` with the routed tenant before any handler runs, cache the result per instance per database binding, and answer `503 tenant_mismatch` on a mismatch with no fallback to another database. (D20, D21; schema_meta.tenant_id)

#### Scenario: A routing entry pointing at the wrong database

- **GIVEN** the compiled map sends coles.pos.app to the binding whose `schema_meta.tenant_id` is `tnt_reliance`
- **WHEN** the owner signs in on coles.pos.app and the register pushes its 41st bill of the day
- **THEN** every route except none answers `503 tenant_mismatch`
- **AND** nothing is read or written until the binding is fixed

### Requirement: A request body cannot choose its tenant

The system SHALL ignore a body `tenant_id` on device routes and SHALL refuse a body `tenant_id` that differs from the routed tenant with `409 wrong_tenant` on enrolment and `401 wrong_tenant` on a token, writing nothing. (D20; api-routes.md §1, §3.1)

#### Scenario: A token minted for one tenant presented at another

- **GIVEN** a device credential enrolled at `tnt_reliance` and a valid 5-minute token for it
- **WHEN** the token is presented at coles.pos.app with a bill of 6,500 paise for 1,500 g of rice
- **THEN** the response is `401 wrong_tenant` and no bill row is written
- **AND** an enrolment on coles.pos.app carrying `tenant_id: "tnt_reliance"` answers `409 wrong_tenant` and creates no device

### Requirement: Routing is a deploy artifact

The system SHALL generate the host-to-binding map, the Worker's database bindings and its routes from the reviewed tenant registry at build time, so adding a tenant needs a registry entry and a deploy and never a code change, and CI SHALL fail when a generated block drifts from the registry. (D20, D21; infra/tenants.json)

#### Scenario: Onboarding a third franchisee

- **GIVEN** a registry listing reliance and coles, one Worker and one deploy
- **WHEN** a pull request adds a third tenant with slug `srilakshmi` and the generated binding and route blocks are committed
- **THEN** the deploy serves the new host with no handler change
- **AND** a hand-edited binding block that disagrees with the registry fails the build

### Requirement: The control plane holds policy, never routing

The system SHALL keep tenant metadata — the tenant record, each tenant's last known schema level, feature flags including archival and retention, and a deploy log — in a control-plane database that is never consulted to route a request. (D21; META tenants, tenant_schema, tenant_flags, deploy_log)

#### Scenario: Turning archival on without a deploy

- **GIVEN** a tenant whose control-plane flags carry `archival_enabled = true` and `retention_months = 15`
- **WHEN** the owner's admin session reads and writes its own policy
- **THEN** the flag is read from the control plane and cached for 60 seconds
- **AND** routing for every tenant continues to come from the compiled map

### Requirement: A control-plane failure does not stop routing

The system SHALL continue routing on the compiled map when the control plane is unreachable, SHALL fall back to the last cached policy value and then to allow, and SHALL raise an alert. (D21; deploy-inventory.md §2)

#### Scenario: The metadata database is down during the evening rush

- **GIVEN** a register pushing 60 bills an hour at 19:00 and an unreachable control plane
- **WHEN** the register pushes and pulls its catalog
- **THEN** every request is served from the compiled map and the last cached flags
- **AND** an alert fires and no bill is lost

### Requirement: A tenant change of policy takes effect within the cache window

The system SHALL cache control-plane policy for 60 seconds, so suspending a tenant, bumping its store limit or flipping a feature flag takes effect without a deploy. (D21; env.META, cached 60 s)

#### Scenario: Suspending a tenant

- **GIVEN** an active tenant and a 60-second policy cache
- **WHEN** the tenant is suspended in the control plane at 11:00:00
- **THEN** requests routed after 11:01:00 observe the suspension
- **AND** no deploy is needed and other tenants are unaffected

### Requirement: Device keys are derived for the routed tenant

The system SHALL derive every device-plane signing key with HKDF-SHA256 from the master key named by the token header's `kid`, salt `"traasu"`, and info `"device-jwt/v1/"` plus the routed tenant's id, never from a claim, and SHALL use the labels `staff-assertion/v1/` and `approval-grant/v1/` for the other two token kinds. (D20; MASTER_KEY_<kid>, devices)

#### Scenario: A token cannot choose its tenant

- **GIVEN** `MASTER_KEY_1` and two tenants, with a token signed for `tnt_reliance`
- **WHEN** that token is presented at coles.pos.app
- **THEN** the Coles key, derived from the Coles tenant id, fails verification and the response is `401 invalid_token`
- **AND** a key derived for grants never verifies a device token

### Requirement: Token claims and the device row must agree with the routed tenant

The system SHALL require `typ` equal to `device`, `scope` containing `sync`, `aud` equal to the routed tenant's app id, `tenant_id` equal to the routed tenant, and an existing unrevoked device row for `sub`, answering `401 wrong_app`, `401 wrong_tenant` or `401 invalid_token` respectively. (D20, D6; devices)

#### Scenario: A token whose audience is another application

- **GIVEN** a token with `aud: "pos-prod-reliance"` presented at coles.pos.app
- **WHEN** the device middleware verifies it
- **THEN** the response is `401 wrong_app` and sync stops with a blocking dialog
- **AND** a token naming a `sub` that has no row in the routed database answers `401 invalid_token`

### Requirement: Master-key rotation keeps both keys for 12 hours

The system SHALL accept the previous master key alongside a newly added `MASTER_KEY_<newkid>` for 12 hours, sign new tokens with the new key, and remove the old key afterwards; a suspected leak SHALL skip the window and remove the old key immediately. (D6, D20; api-routes.md §1)

#### Scenario: Rotating without dropping a shift

- **GIVEN** tokens signed with key 1, including a 12-hour approval grant minted at 09:00
- **WHEN** `MASTER_KEY_2` is added and signing moves to it at 10:00
- **THEN** tokens carrying `kid = 1` still verify until 22:00 and not after
- **AND** every 5-minute device token has already converged on key 2, so no register re-enrols
