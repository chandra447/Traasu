# Better Auth for the admin plane

Decision: Better Auth 1.7.23 for the admin web (email/password, Google OAuth, TOTP, sessions). The device
plane stays custom. Tables in `0007-better-auth.sql`, session and enrolment columns in `0008-audit-fixes.sql`.
Evidence: `review-architecture.md`, decision D17 in `DECISIONS.md`.

## Why split the two planes

| | Admin plane | Device plane |
|---|---|---|
| Actor | a person in a browser | a register |
| Credential | password or Google OAuth | device credential in the OS keychain |
| Session | cookie, 12 h sliding, 30 d absolute | 5 min JWT, no refresh token |
| Who owns it | Better Auth | us |
| Why | OAuth, TOTP, CSRF, session rotation are solved problems with a security team behind them | Better Auth does not model device credentials, and we must not bend it to |

Forcing the register through Better Auth would mean inventing a "device user" with a password it never types.
The planes stay separate; only the admin one gets a library.

## What the trial proved

Three configurations of the config in this repo's earlier draft, against Better Auth 1.7.23 on a local D1
(miniflare), 2026-11-14, in order. Each failed for a different reason, which is why the shape below is narrow.

1. **`modelName` only.** Renaming the model renames the table, not the columns. Better Auth's startup schema
   check refuses to boot: 30+ camelCase columns it expects (`emailVerified`, `createdAt`, `updatedAt`,
   `twoFactorEnabled`) are missing, and there is no two-factor table at all.
2. **Full `fields` mapping onto the old tables.** Still refuses. Our `sessions` had no `token` column, and the
   rest of our columns (`token_hash`, `token_prefix`, `last_seen_at`, `absolute_expires_at`) are not its
   contract; it also requires columns we never wrote. Mapping names onto a table built for a different model is
   the wrong repair.
3. **Tables rebuilt to its shape, INTEGER dates.** `cannot store TEXT value in INTEGER column`. Better Auth
   writes dates as ISO-8601 TEXT (`"2026-11-14T09:31:22.118Z"`). With TEXT date columns it works.

The cached instance failed as well. A module-level `betterAuth()` wrote tenant B's owner and session into tenant
A's D1, reproduced on local D1: the instance closes over `database` at construction. Construct it **per
request** from the routed tenant's binding (T2 step 5).

What works is the combination: its own `auth_*` tables, snake_case names through `fields`, ISO TEXT dates, and
one instance per request. Nothing else was made to work.

The final shape is verified end to end on Better Auth 1.7.23 against the repo's `0001` to `0008` on local D1:
sign-up, sign-in and `get-session` all work, a session expires 12 h after issue, tenant A's cookie returns null
on tenant B's host, and A's owner cannot sign in on B. That is the isolation claim tested from the outside, not
inferred from the binding.

## The config that works

```ts
// Constructed inside the request, after tenant resolution (T2), never at module scope.
const auth = betterAuth({
  database: db,                                  // env[tenant.binding], already tenant-checked
  baseURL: `https://${host}`,
  trustedOrigins: [`https://${host}`],
  secret: env.BETTER_AUTH_SECRET,
  basePath: "/v1/admin/auth",
  user: {
    modelName: "auth_user",
    fields: {
      emailVerified: "email_verified",
      createdAt: "created_at",
      updatedAt: "updated_at",
    },
  },
  session: {
    modelName: "auth_session",
    expiresIn: 60 * 60 * 12,                     // 12 h sliding
    updateAge: 60 * 60,                          // at most one refresh per hour
    fields: {
      userId: "user_id",
      expiresAt: "expires_at",
      ipAddress: "ip_address",
      userAgent: "user_agent",
      createdAt: "created_at",
      updatedAt: "updated_at",
    },
  },
  account: {
    modelName: "auth_account",
    fields: {
      userId: "user_id",
      accountId: "account_id",
      providerId: "provider_id",
      accessToken: "access_token",
      refreshToken: "refresh_token",
      idToken: "id_token",
      accessTokenExpiresAt: "access_token_expires_at",
      refreshTokenExpiresAt: "refresh_token_expires_at",
      createdAt: "created_at",
      updatedAt: "updated_at",
    },
  },
  verification: {
    modelName: "auth_verification",
    fields: { expiresAt: "expires_at", createdAt: "created_at", updatedAt: "updated_at" },
  },
  rateLimit: {
    enabled: true,
    storage: "database",
    modelName: "auth_rate_limit",
    fields: { lastRequest: "last_request" },
  },
  plugins: [
    twoFactor({
      schema: {
        user: { fields: { twoFactorEnabled: "two_factor_enabled" } },
        twoFactor: {
          modelName: "auth_two_factor",
          fields: {
            backupCodes: "backup_codes",
            userId: "user_id",
            failedVerificationCount: "failed_verification_count",
            lockedUntil: "locked_until",
          },
        },
      },
    }),
  ],
  socialProviders: {
    google: { clientId: env.GOOGLE_CLIENT_ID, clientSecret: env.GOOGLE_CLIENT_SECRET },
  },
});
```

`auth` is per request, so the handler comes from request context, not a module import:

```ts
app.on(["GET", "POST"], "/v1/admin/auth/*", (c) => c.get("auth").handler(c.req.raw));
```

Every other admin route reads the session through `auth.api.getSession({ headers: c.req.raw.headers })`, then
resolves `auth_user.id` to a domain `users` row and applies our `role` and `user_stores` checks. Better Auth
authenticates; we authorise.

## What Better Auth owns

`auth_user`, `auth_session`, `auth_account`, `auth_verification`, `auth_two_factor`, `auth_rate_limit`. Keys are
Better Auth's 32-character random strings, not UUIDv7; timestamps are ISO TEXT, not epoch seconds. That is the
one exception to the storage rules in `d1-schema.md`, and these are the only tables in the file that no domain
query joins: `users.auth_user_id` is the single crossing.

Isolation is the point. A domain column added to `auth_user` would be recreated by the library's own schema
tooling, and a library migration would land in the middle of the billing tables. The cost is one join from
`users.auth_user_id` to `auth_user.id`, paid once per admin request.

The domain `users` table stays ours because `bills.user_id`, `bill_lines.override_by`, `shifts.opened_by`,
`user_stores` and the cashier roster all reference it. It keeps `role`, `active`, `name`, `email` and gains
`auth_user_id` (unique, nullable). It loses nothing to Better Auth except the two things that were never domain
data: `password_hash` and `pin_hash`. The first lives on `auth_account.password`; the second on
`staff_credentials`.

A cashier is a `users` row with `auth_user_id IS NULL`. There is no `auth_user` row and therefore no credential
to log in with. That is the mechanism that keeps cashiers out of the admin web, not a UI check, and it holds
even if a route forgets its role test: the session has nothing to point at.

## What stays ours

| Thing | Why it cannot move |
|---|---|
| Device credential, `/v1/token` | no browser, no cookie, 5 min JWT, offline lease |
| `/v1/enroll` and owner approval | D5: the device stays pending until the owner approves it in admin |
| Cashier PIN | offline verification on the terminal against `staff_credentials`; a PIN is attribution, not a login |
| `staff_sessions` | the window a bill must fall inside; not a browser session |
| `approval_grants` | time-boxed money authority, minted online |
| Roles and store scope | `users.role` and `user_stores`; the `admin` plugin is dropped |

The `admin` plugin is dropped. It models roles and impersonation on `auth_user`, which would be a second source
of truth next to `users.role` and `user_stores`, and a Better Auth migration would rewrite it.

`/v1/approvals` verifies the manager's password through Better Auth's password verifier rather than our own
hashing, so there is one credential check in the system.

## Sessions

`expiresIn` 12 h with `updateAge` 1 h is a sliding idle window. Better Auth's default is 7 days, which is too
long for a shared back-office browser. On top of the sliding window, our middleware enforces a 30-day absolute
cap read from `auth_session.created_at`; a session older than that is rejected and deleted, so a refresh loop
cannot extend it forever.

## Google OAuth: worth it, with a break-glass

Google gives the owner MFA for free and removes a password to leak. Two conditions.

1. **One OAuth client for the whole deployment, with a per-tenant redirect URI** (`https://<host>/v1/admin/auth/callback/google`,
   D20). Tenant identity comes from the host of the redirect, which is the same Host the Worker routed on. A
   shared client with a wildcard redirect is how one tenant ends up authenticating into another's database.
   Google's limit on redirect URIs per client is UNVERIFIED; if tenants exceed it, the fallback is one client
   per enterprise, which forces the per-tenant Worker exception already contracted in D20.
2. **Keep email/password enabled** for one owner account. If the Google account is lost, the business must not
   lose access to its own POS.

Google is for the owner and staff who need admin access. Cashiers never see a browser login.

## The regression to accept

Better Auth stores the session token **in plaintext** in `auth_session.token`; it looks sessions up by cookie
value. Our earlier design hashed them at rest. A D1 dump therefore yields live admin sessions until they expire.

Bought back with a 12 h sliding expiry, the 30-day absolute cap, immediate deletion on logout, and revoke-all on
password or OAuth change. If that is not enough, the alternative is a custom session layer and no Better Auth,
which is a worse trade for the OAuth and TOTP work it replaces.

## Rate limiting: the database, not KV

Better Auth's rate limiter needs atomic counters. Workers KV is eventually consistent, cannot expire a key
below 60 s, and has no atomic operations; Better Auth's own Cloudflare guide says KV cannot serve as
`secondaryStorage`. Storage is `database`, table `auth_rate_limit` (one row per key, count, last request). The
earlier "rate-limit counters in KV" line is wrong and superseded.

## Migration shape (0007, plus 0008)

- New: `auth_user`, `auth_session`, `auth_account`, `auth_verification`, `auth_two_factor`, `auth_rate_limit`.
- `users` gains `auth_user_id` (unique where not null) and `updated_at`. It holds no credential: the password
  hash is `auth_account.password`, the PIN hash is in `staff_credentials`.
- `sessions` is dropped. It was a custom hashed-token table with no consumer once the admin plane moves.
- New `staff_credentials` (PIN hash, salt, who set it). PINs stay ours.
- `user_stores` and `role` are untouched.
- 0008 adds `devices.approved_at`/`approved_by` for the two-step enrolment that gates `/v1/token`.

Both migrations set `schema_meta.schema_version` (7, then 8).

## What is still unverified

1. **scrypt login CPU on Workers.** Login is the only path that runs it, and a login burst is a cheap DoS
   unless the database rate limit is live first. Measure one login on a paid Worker before opening the panel.
   Workers Free stays out of production anyway (D24).
2. **Rate-limit counters under load.** The `auth_rate_limit` path is wired and works for single requests; that
   two concurrent attempts cannot both slip through the same counter is untested.
3. **Google's per-client redirect-URI limit.** Sets whether one client can serve every tenant. If it cannot,
   the fallback is one client per enterprise, which is the contracted per-tenant Worker exception (D20).
