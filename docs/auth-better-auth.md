# Better Auth for the admin plane

Decision: adopt **Better Auth** for the admin web (email/password, Google OAuth, TOTP, sessions). Keep the
**device plane** custom. Migration `0007-better-auth.sql`.

Verified against Better Auth's Cloudflare guide and the 1.5 release notes (fetched 2026-11-14):
D1 is a first-class database (`database: env.DB`, auto-detected), Hono mounts the handler with no adapter,
and Workers need `nodejs_compat` for compatibility dates before 2026-08-04.

## Why split the two planes

| | Admin plane | Device plane |
|---|---|---|
| Actor | a person in a browser | a register |
| Credential | password or Google OAuth | device credential in the OS keychain |
| Session | cookie, 12 h idle | 5 min JWT, no refresh token |
| Who owns it | Better Auth | us |
| Why | OAuth, TOTP, CSRF, session rotation are solved problems with a security team behind them | Better Auth does not model device credentials, and we must not bend it to |

Trying to force the register through Better Auth would mean inventing a "device user" and a password it never
types. The two planes stay separate; only the admin one gets a library.

## What Better Auth owns

`users`, `sessions`, `account`, `verification` — with our table names mapped in config, so
`bills.user_id` and every existing foreign key keep working:

```ts
export const auth = betterAuth({
  database: env.DB,
  secret: env.BETTER_AUTH_SECRET,
  basePath: "/v1/admin/auth",
  user:    { modelName: "users" },
  session: { modelName: "sessions" },
  account: { modelName: "account" },
  verification: { modelName: "verification" },
  socialProviders: {
    google: { clientId: env.GOOGLE_CLIENT_ID, clientSecret: env.GOOGLE_CLIENT_SECRET },
  },
  emailAndPassword: { enabled: true, requireEmailVerification: false },
  advanced: { ipAddress: { ipAddressHeaders: ["cf-connecting-ip"] } },
  plugins: [twoFactor(), admin()],
});
```

Mount:

```ts
app.on(["GET", "POST"], "/v1/admin/auth/*", (c) => auth.handler(c.req.raw));
```

Every other admin route reads the session through `auth.api.getSession({ headers: c.req.raw.headers })` and
then applies our own `role` / `user_stores` checks. Better Auth authenticates; we authorise.

## What stays ours

| Thing | Why it cannot move |
|---|---|
| Device credential, `/v1/enroll`, `/v1/token` | no browser, no cookie, 15-minute JWT, offline lease |
| Cashier PIN | offline verification against a cached hash; a PIN is attribution, not a login |
| `staff_sessions` | the window a bill must fall inside; not a browser session |
| `approval_grants` | time-boxed money authority, minted online |
| Roles and store scope | Better Auth's `admin` plugin gives roles, but store scoping is ours |

A cashier is a `users` row with **no `account` row**, so there is no credential to log in with. That is the
mechanism that keeps cashiers out of the admin web — not a UI check.

## Google OAuth: worth it, with a break-glass

Owner login through Google gives MFA for free and removes a password to leak. Two conditions:

1. **Keep email/password enabled** for one owner account. If the Google account is lost, the business must not
   lose access to its own POS.
2. **One OAuth client per tenant** (or one client with per-tenant redirect URIs). A shared client with a
   wildcard redirect is how a tenant ends up authenticating into another tenant's deployment.

Google is for the owner and staff who need admin access. Cashiers never see a browser login.

## The regression to accept

Better Auth stores the session token **in plaintext** in the `sessions` row — it looks sessions up by cookie
value. Our earlier design hashed them at rest. A D1 dump therefore yields live admin sessions until they
expire.

Bought back with: 12 h idle / 30 d absolute expiry, immediate revocation on logout, and `revoke all sessions`
on password or OAuth change. If that is not enough, the alternative is a custom session layer and no Better
Auth — a worse trade for the OAuth and 2FA work it replaces.

## Rate limiting: not KV

Better Auth's rate limiter needs atomic counters. Workers KV is eventually consistent, cannot expire a key
below 60 s, and has no atomic operations — Better Auth's own Cloudflare guide says KV cannot serve as
`secondaryStorage`. Use the database storage (D1) or a Durable Object. Our earlier "rate-limit counters in KV"
line is wrong; this supersedes it.

## Migration shape (0007)

- `users` gains `email_verified`, `image`, `updated_at`; loses `password_hash` (Better Auth keeps the hash on
  `account`).
- `sessions` gains `updated_at`, `ip_address`, `user_agent`.
- New: `account` (one row per credential or social provider), `verification` (email, reset, OAuth state),
  `staff_credentials` (PINs, moved off `users`).
- `user_stores` and `role` stay ours.

## What to check before trusting it

1. Confirm the field mapping (`createdAt` ↔ `created_at`, `emailVerified` ↔ `email_verified`) against the
   installed version's schema output — run `npx auth generate` and diff it against `0007`.
2. Confirm the session lookup works with our `sessions.id` TEXT primary key and Better Auth's expected `token`
   unique column; if it insists on `token`, add it and keep `token_hash` unused.
3. Load-test login on a Worker: scrypt is CPU-heavy, and a login burst is a cheap DoS unless rate-limited.
4. Decide the Google client strategy (per tenant vs shared) before the second franchisee.
