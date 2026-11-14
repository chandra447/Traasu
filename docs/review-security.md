# Adversarial security review — auth, tenancy, attribution, overrides

Reviewer: `security-reviewer` sub-agent, 2026-11-14. Scope: `DESIGN-BRIEF.md`, notes 03/04/05/09/10/11.
No code exists, so every finding is against the design. **Verdict: ISSUES — 2 critical, 7 high, 8 medium.**

| # | Finding | Sev | Attacker needs | Practical |
|---|---|---|---|---|
| F1 | Worker trusts terminal-supplied `price_version`, `list_price_minor`, `price_minor`; reports never recompute → recorded revenue and the violation baseline are attacker-controlled | **Crit** | device credential or modified client | yes |
| F2 | Beyond-ceiling override is always honoured offline → unlimited self-attributed discount by unplugging the LAN | **High** | physical access + cashier PIN | yes |
| F3 | A revoked-but-offline terminal keeps billing, not just syncing → off-book sales from a fired/stolen register | **High** | possession of the terminal | yes |
| F4 | `override_note` free text → stored XSS in the admin feed; the CSRF token is a readable double-submit cookie, so XSS = owner takeover. Plus CSV formula injection in report exports | **Crit** | low-priv input + owner opens page | yes |
| F5 | Roster `pin_hash` + `pin_salt` ship in `/v1/catalog` and land in local SQLite → offline brute of every rostered PIN, bypassing the in-app lockout | **High** | disk read | yes |
| F6 | `device_credential` and `install_id` sit in the same keychain → keychain theft impersonates the device; `install_id` only defeats a copied data folder | **High** | physical/malware | yes |
| F7 | Catalog may not be store-scoped → one terminal leaks the franchise's pricing and every store's cashier PIN hashes | **High** | one device credential | if filter missing |
| F8 | `staff → owner` escalation via mass-assignment; owner has no MFA until "later" | **High** | browser + admin session | depends |
| F9 | Enrolment code is typed in the open for 15 min; observe-and-race lets an attacker enrol first | **Med** | sight of code | opportunistic |
| F10 | Revocation latency up to 15 min (JWT TTL) + 60 s KV cache | **Med** | stolen credential | bounded |
| F11 | Rotation keeps the old credential valid "until the next successful call" → a stolen old secret survives rotation | **Med** | stolen old credential | bounded |
| F12 | `/v1/enroll` and `/v1/bills` flooding; tenant self-exhaustion of the 10 GB D1 | **Med** | network / one credential | per-tenant blast radius |
| F13 | `policy_violation` is a passive list nobody is forced to read | **Med** | — | process gap |
| F14 | `created_at` is terminal clock, used for `stale_price` and `business_date` → spoof to dodge detection and shift sales between report days | **Med** | device credential | yes |
| F15 | Model-B (shared Worker) needs per-tenant signing keys, or A's token is signature-valid at B | **Med** | future | theoretical |
| F16 | Login PBKDF2 CPU-DoS, reset flooding, session fixation | **Low-Med** | network / browser | nuisance |
| F17 | Contradiction between notes 10 and 11: if the manager PIN is cached like a cashier PIN, beyond-ceiling authority is brute-forceable | **High** | disk read | yes |

## Defended by design (verify with tests, do not rebuild)

1. Body `tenant_id`/`app_id` ignored; token claims authoritative.
2. Pinned `tenant_id`/`app_id`/`api_base` stop silent DNS re-pointing.
3. Device JWT (`scope: "sync"`) cannot reach `/v1/admin/*`; admin session cannot push bills.
4. Bill push idempotent on `id` plus unique `(store_id, terminal_id, local_seq)`.
5. Session ids opaque and hashed at rest; device credential stored as sha256 of 256-bit randomness.
6. Per-tenant D1 bounds every leak to one franchisee.

## The two fixes that matter most

**(a) Recompute money policy server-side.** The Worker must derive the applicable list price and ceiling from
its own `item_prices` for `(item, store, version valid at created_at)`. Keep the charged amount (the customer
already paid) but store a claimed-vs-computed delta and set `policy_violation`/`stale_price` from server math,
never from the line's own `list_price_minor`.

**(b) Cap offline override authority, not the sale.** Offline with no valid `approval_grant`, the line falls
back to the store ceiling or list price; the sale still completes. A beyond-ceiling price needs a cached,
unexpired grant.

## Other accepted changes

- Split "offline" from "revoked": a terminal that has learned it is revoked hard-stops billing, and a
  max-offline-age lease (e.g. 7 days without a successful sync) locks new bills.
- Two-step enrolment: terminal posts `install_id`, owner approves that specific install, then a credential is
  issued. Kills the observe-and-race window.
- Device JWT TTL down to ~5 min; revocation pushed to KV immediately; rotation invalidates the old credential
  on first use of the new one.
- Store-scope the catalog: roster and price overrides filtered by the token's `store_id`.
- Encrypt local SQLite at rest; minimum 6-digit PINs; state plainly that the lockout is UX, not a control.
- Owner-only `/v1/admin/users` writes with an explicit field allowlist; owner TOTP before go-live.
- No GET with a side effect (SameSite=Lax still sends the cookie on top-level navigation).
- CSV export escapes leading `= + - @`; audit/override text rendered as text, never HTML.
- `created_at` sanity-bounded against `received_at`; `business_date` validated within ±1 day of server date.
- `policy_violation` surfaced as a dashboard counter or shift-close acknowledgement, not a passive page.

## Residual risk to state honestly

Offline attribution is terminal-asserted. The trail proves "a device holding a valid credential asserted these
facts at `received_at`". It does not prove the human, the terminal's clock, or that a manager approved —
unless an online-minted `approval_grant` or shift assertion backs it. Cashier/manager collusion is a detection
problem, not a prevention one.
