-- Migration 0007 — adopt Better Auth for the admin plane.
--
-- Better Auth owns browser sessions and credentials: user, session, account,
-- verification. The device plane (credential -> JWT) stays custom, because a
-- register is not a browser and Better Auth does not model device credentials.
--
-- Our table names are kept and mapped with `user: { modelName: "users" }` etc.,
-- so `bills.user_id` and every existing FK keep working.

-- ---------------------------------------------------------------------------
-- users: add the fields Better Auth expects.
-- ---------------------------------------------------------------------------
ALTER TABLE users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 0
  CHECK (email_verified IN (0, 1));
ALTER TABLE users ADD COLUMN image TEXT;
ALTER TABLE users ADD COLUMN updated_at INTEGER NOT NULL DEFAULT 0;

-- Better Auth stores the password on `account`, not on the user. The base DDL
-- no longer carries `password_hash`; two sources of truth for one credential is
-- how accounts drift.

-- PINs are ours, not Better Auth's: a cashier is a user with no account row and
-- therefore cannot log into the admin web at all.
CREATE TABLE IF NOT EXISTS staff_credentials (
  user_id     TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  pin_hash    TEXT NOT NULL,
  pin_salt    TEXT NOT NULL,
  updated_at  INTEGER NOT NULL,
  set_by      TEXT REFERENCES users(id) ON DELETE SET NULL
) STRICT;

-- ---------------------------------------------------------------------------
-- sessions: Better Auth keeps the token in the row (it looks it up by cookie
-- value). That is a real regression from hashing at rest; it is bought back with
-- a short idle expiry and immediate revocation.
-- ---------------------------------------------------------------------------
ALTER TABLE sessions ADD COLUMN updated_at INTEGER NOT NULL DEFAULT 0;
ALTER TABLE sessions ADD COLUMN ip_address TEXT;
ALTER TABLE sessions ADD COLUMN user_agent TEXT;

-- ---------------------------------------------------------------------------
-- account: one row per credential or social provider. Google OAuth lands here.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS account (
  id                       TEXT PRIMARY KEY,
  user_id                  TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  account_id               TEXT NOT NULL,
  provider_id              TEXT NOT NULL,
  access_token             TEXT,
  refresh_token            TEXT,
  id_token                 TEXT,
  access_token_expires_at  INTEGER,
  refresh_token_expires_at INTEGER,
  scope                    TEXT,
  password                 TEXT,
  created_at               INTEGER NOT NULL,
  updated_at               INTEGER NOT NULL,
  UNIQUE (provider_id, account_id)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_account_user ON account(user_id);

-- ---------------------------------------------------------------------------
-- verification: email verification, password reset, OAuth state.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS verification (
  id         TEXT PRIMARY KEY,
  identifier TEXT NOT NULL,
  value      TEXT NOT NULL,
  expires_at INTEGER NOT NULL,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
) STRICT;

CREATE INDEX IF NOT EXISTS idx_verification_identifier ON verification(identifier);
