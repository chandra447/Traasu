-- Migration 0006 — cashier sessions on a shared terminal.
--
-- A register is used by several cashiers in a day, and one cashier may log out
-- and back in. "Who is at the register" is therefore a session, not a device
-- property. The session is the window in which a bill can be attributed; the
-- token is only how the server learns about it.

-- The 0004 column was a token reference with no window. Replace it with the
-- session, which carries both the identity and the time bounds.
DROP TRIGGER IF EXISTS trg_bills_assertion_required;
ALTER TABLE bills DROP COLUMN staff_assertion_id;

CREATE TABLE IF NOT EXISTS staff_sessions (
  id            TEXT PRIMARY KEY,
  user_id       TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  store_id      TEXT NOT NULL REFERENCES stores(id) ON DELETE RESTRICT,
  terminal_id   TEXT NOT NULL REFERENCES terminals(id) ON DELETE RESTRICT,
  shift_id      TEXT REFERENCES shifts(id) ON DELETE SET NULL,
  auth_method   TEXT NOT NULL CHECK (auth_method IN ('password', 'pin_offline')),
  token_hash    TEXT,                       -- opaque assertion, hashed; NULL for offline sessions
  started_at    INTEGER NOT NULL,
  last_seen_at  INTEGER,
  ended_at      INTEGER,
  end_reason    TEXT CHECK (end_reason IS NULL OR end_reason IN
                  ('logout', 'switch_user', 'idle', 'shift_close', 'revoked', 'crash')),
  ended_by      TEXT REFERENCES users(id) ON DELETE RESTRICT,
  revoked_at    INTEGER,
  synced_at     INTEGER,
  CHECK (ended_at IS NULL OR ended_at >= started_at),
  CHECK (ended_at IS NULL OR end_reason IS NOT NULL)
) STRICT;

-- One live session per terminal: two cashiers cannot bill from the same cart.
CREATE UNIQUE INDEX IF NOT EXISTS uq_staff_sessions_live
  ON staff_sessions(terminal_id) WHERE ended_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_staff_sessions_user ON staff_sessions(user_id, started_at);
CREATE INDEX IF NOT EXISTS idx_staff_sessions_unsynced
  ON staff_sessions(started_at) WHERE synced_at IS NULL;

ALTER TABLE bills ADD COLUMN staff_session_id TEXT
  REFERENCES staff_sessions(id) ON DELETE RESTRICT;

-- A bill must fall inside the session that claims it, on the same terminal, for
-- the same user. This runs on D1 and on the terminal, so an offline bill cannot
-- be back-dated into someone else's session.
CREATE TRIGGER IF NOT EXISTS trg_bills_session_window
BEFORE INSERT ON bills
WHEN NEW.staff_session_id IS NOT NULL AND NOT EXISTS (
  SELECT 1 FROM staff_sessions s
  WHERE s.id = NEW.staff_session_id
    AND s.user_id = NEW.user_id
    AND s.terminal_id = NEW.terminal_id
    AND NEW.created_at >= s.started_at
    AND (s.ended_at IS NULL OR NEW.created_at <= s.ended_at)
)
BEGIN
  SELECT RAISE(ABORT, 'bill_outside_staff_session');
END;

-- A password session must exist; a PIN session is allowed to stand alone
-- (offline attribution), which is what `unverified_staff` records.
CREATE TRIGGER IF NOT EXISTS trg_bills_password_needs_session
BEFORE INSERT ON bills
WHEN NEW.auth_method = 'password' AND NEW.staff_session_id IS NULL
BEGIN
  SELECT RAISE(ABORT, 'password_bill_needs_session');
END;

-- Closing a session is one-way.
CREATE TRIGGER IF NOT EXISTS trg_staff_sessions_close_once
BEFORE UPDATE OF ended_at ON staff_sessions
WHEN OLD.ended_at IS NOT NULL AND NEW.ended_at IS NOT OLD.ended_at
BEGIN
  SELECT RAISE(ABORT, 'session_already_closed');
END;

UPDATE schema_meta SET schema_version = 6 WHERE id = 1;
