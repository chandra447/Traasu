-- Migration 0007 — Better Auth for the admin plane (D17, revised 2026-11-14).
--
-- Better Auth owns its own tables, separate from the domain tables. Verified on
-- Better Auth 1.7.23 against a local D1:
--   * modelName alone renames tables, not columns; Better Auth's startup schema
--     check then refuses to run (camelCase columns, twoFactor table missing).
--   * it needs sessions.token and refuses required columns it never writes
--     (our old token_hash, token_prefix, last_seen_at, absolute_expires_at).
--   * it writes dates as ISO-8601 TEXT; a STRICT INTEGER column rejects them.
-- So these tables follow Better Auth's conventions (TEXT dates, its own 32-char
-- ids) and are the one exception to the epoch-seconds and UUIDv7 rules in
-- d1-schema.sql. Column names are snake_case through Better Auth's `fields`
-- mapping (see auth-better-auth.md).
--
-- Domain identity stays in `users` (bills.user_id, role, user_stores). An admin
-- user links by users.auth_user_id; a cashier has none, which is what keeps
-- cashiers out of the admin web.

CREATE TABLE IF NOT EXISTS auth_user (
  id                  TEXT PRIMARY KEY,
  name                TEXT NOT NULL,
  email               TEXT NOT NULL UNIQUE,
  email_verified      INTEGER NOT NULL DEFAULT 0 CHECK (email_verified IN (0, 1)),
  image               TEXT,
  two_factor_enabled  INTEGER DEFAULT 0 CHECK (two_factor_enabled IN (0, 1)),
  created_at          TEXT NOT NULL,
  updated_at          TEXT NOT NULL
) STRICT;

-- Session token is stored as issued (Better Auth looks it up by cookie value).
-- A D1 dump therefore yields live admin sessions until they expire: bought
-- back with a 12 h sliding expiry, a 30-day absolute cap in our middleware,
-- and revoke-all on password or OAuth change.
CREATE TABLE IF NOT EXISTS auth_session (
  id          TEXT PRIMARY KEY,
  user_id     TEXT NOT NULL REFERENCES auth_user(id) ON DELETE CASCADE,
  token       TEXT NOT NULL UNIQUE,
  expires_at  TEXT NOT NULL,
  ip_address  TEXT,
  user_agent  TEXT,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
) STRICT;

CREATE INDEX IF NOT EXISTS idx_auth_session_user ON auth_session(user_id);

-- One row per credential or social provider; the password hash lives here.
CREATE TABLE IF NOT EXISTS auth_account (
  id                        TEXT PRIMARY KEY,
  user_id                   TEXT NOT NULL REFERENCES auth_user(id) ON DELETE CASCADE,
  account_id                TEXT NOT NULL,
  provider_id               TEXT NOT NULL,
  access_token              TEXT,
  refresh_token             TEXT,
  id_token                  TEXT,
  access_token_expires_at   TEXT,
  refresh_token_expires_at  TEXT,
  scope                     TEXT,
  password                  TEXT,
  created_at                TEXT NOT NULL,
  updated_at                TEXT NOT NULL,
  UNIQUE (provider_id, account_id)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_auth_account_user ON auth_account(user_id);

-- Email verification, password reset, OAuth state.
CREATE TABLE IF NOT EXISTS auth_verification (
  id          TEXT PRIMARY KEY,
  identifier  TEXT NOT NULL,
  value       TEXT NOT NULL,
  expires_at  TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
) STRICT;

CREATE INDEX IF NOT EXISTS idx_auth_verification_identifier ON auth_verification(identifier);

-- twoFactor() plugin.
CREATE TABLE IF NOT EXISTS auth_two_factor (
  id                         TEXT PRIMARY KEY,
  user_id                    TEXT NOT NULL REFERENCES auth_user(id) ON DELETE CASCADE,
  secret                     TEXT NOT NULL,
  backup_codes               TEXT NOT NULL,
  verified                   INTEGER DEFAULT 1 CHECK (verified IN (0, 1)),
  failed_verification_count  INTEGER DEFAULT 0,
  locked_until               TEXT
) STRICT;

CREATE INDEX IF NOT EXISTS idx_auth_two_factor_user ON auth_two_factor(user_id);

-- Rate-limit counters need atomic writes; Workers KV cannot provide them.
CREATE TABLE IF NOT EXISTS auth_rate_limit (
  id            TEXT PRIMARY KEY,
  key           TEXT NOT NULL UNIQUE,
  count         INTEGER NOT NULL,
  last_request  INTEGER NOT NULL
) STRICT;

-- The custom admin session table is replaced by auth_session.
DROP INDEX IF EXISTS idx_sessions_prefix;
DROP INDEX IF EXISTS uq_sessions_token_hash;
DROP INDEX IF EXISTS idx_sessions_user;
DROP TABLE IF EXISTS sessions;

ALTER TABLE users ADD COLUMN auth_user_id TEXT REFERENCES auth_user(id) ON DELETE SET NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_users_auth_user ON users(auth_user_id) WHERE auth_user_id IS NOT NULL;
ALTER TABLE users ADD COLUMN updated_at INTEGER NOT NULL DEFAULT 0;

-- PINs are ours: offline attribution, verified on the terminal against this
-- hash. A PIN is never an admin credential.
CREATE TABLE IF NOT EXISTS staff_credentials (
  user_id     TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  pin_hash    TEXT NOT NULL,
  pin_salt    TEXT NOT NULL,
  updated_at  INTEGER NOT NULL,
  set_by      TEXT REFERENCES users(id) ON DELETE SET NULL
) STRICT;

UPDATE schema_meta SET schema_version = 7 WHERE id = 1;
