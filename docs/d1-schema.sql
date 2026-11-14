-- D1 schema for one tenant: one enterprise, one database. One Worker serves every tenant and
-- picks this database by Host; the auth_* tables (migration 0007) follow Better Auth's
-- conventions instead of the storage rules below.
-- Applied from this one file by `wrangler d1 migrations apply` and, unchanged, by the
-- terminal's Go migration runner to its local SQLite. Where the two sides differ, the
-- comment says so; nothing is dropped from either copy.
--
-- Storage rules, stated once for the file:
--   ids            TEXT, lowercase UUIDv7, canonical 8-4-4-4-12 hex with dashes, 36 chars,
--                  e.g. 0192f3c4-8a5b-7c3d-9e1f-2a3b4c5d6e7f. UUIDv7 sorts by creation time,
--                  so TEXT ordering is creation ordering. No prefixes, no integer ids.
--   money          INTEGER, paise.
--   quantities     INTEGER, base units (g, ml, pcs).
--   timestamps     INTEGER, Unix epoch seconds, UTC.
--   business_date  TEXT, 'YYYY-MM-DD', the store's business day (may start at 04:00).
--   booleans       INTEGER 0/1. Never TEXT 'true'.
--   JSON           TEXT validated by json_valid(); no query filters on JSON contents.
-- Neither money nor quantity is ever REAL, and every table is STRICT so SQLite rejects a
-- REAL in an INTEGER column instead of silently coercing 40.5 into it.
--
-- Foreign keys are declared on every relationship. D1 enforces them on every query and
-- migration, and runs each in an implicit transaction, so only PRAGMA defer_foreign_keys
-- can relax them there. A bare sqlite3 shell and modernc.org/sqlite default to off: the
-- migration runner and the terminal must enable them outside a transaction
-- (PRAGMA foreign_keys = ON; for the Go driver, _pragma=foreign_keys(1) in the DSN).


-- ---------------------------------------------------------------------------
-- Provisioning and the catalog cursor
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS schema_meta (
  id              INTEGER PRIMARY KEY CHECK (id = 1),
  schema_version  INTEGER NOT NULL CHECK (schema_version > 0),
  tenant_id       TEXT NOT NULL,
  app_id          TEXT NOT NULL,
  provisioned_at  INTEGER NOT NULL
) STRICT;

-- Single allocator row for catalog_changes.seq. Writers reserve a block in the same batch
-- that inserts the change rows, so a rolled-back batch gives the numbers back.
CREATE TABLE IF NOT EXISTS catalog_seq (
  id            INTEGER PRIMARY KEY CHECK (id = 1),
  last_seq      INTEGER NOT NULL CHECK (last_seq >= 0),
  pruned_below  INTEGER NOT NULL DEFAULT 0 CHECK (pruned_below >= 0 AND pruned_below <= last_seq)
) STRICT;


-- ---------------------------------------------------------------------------
-- Reference data
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS units (
  unit            TEXT PRIMARY KEY,
  unit_group      TEXT NOT NULL CHECK (unit_group IN ('mass', 'volume', 'count')),
  precision       INTEGER NOT NULL CHECK (precision BETWEEN 0 AND 6),
  factor_to_base  INTEGER NOT NULL CHECK (factor_to_base > 0),
  active          INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
) STRICT;

CREATE TABLE IF NOT EXISTS tax_rates (
  id        TEXT PRIMARY KEY,
  name      TEXT NOT NULL,
  rate_bp   INTEGER NOT NULL CHECK (rate_bp BETWEEN 0 AND 10000),
  active    INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
) STRICT;

CREATE TABLE IF NOT EXISTS stores (
  id          TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  name_local  TEXT,
  active      INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
  created_at  INTEGER NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS store_settings (
  store_id                    TEXT PRIMARY KEY REFERENCES stores(id) ON DELETE CASCADE,
  business_day_start_minutes  INTEGER NOT NULL DEFAULT 0
                              CHECK (business_day_start_minutes BETWEEN 0 AND 1439),
  rounding_mode               TEXT NOT NULL DEFAULT 'none'
                              CHECK (rounding_mode IN ('none', 'nearest_rupee')),
  updated_at                  INTEGER NOT NULL
) STRICT, WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS terminals (
  id          TEXT PRIMARY KEY,
  store_id    TEXT NOT NULL REFERENCES stores(id) ON DELETE RESTRICT,
  name        TEXT NOT NULL,
  active      INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
  created_at  INTEGER NOT NULL
) STRICT;

-- Both the config and the counter live here. Scope 'store' is the server-side legal invoice
-- series; scope 'register' is the terminal-local display series. The server never advances a
-- register counter and the terminal never advances a store counter.
CREATE TABLE IF NOT EXISTS numbering_policy (
  id            TEXT PRIMARY KEY,
  store_id      TEXT NOT NULL REFERENCES stores(id) ON DELETE RESTRICT,
  scope         TEXT NOT NULL CHECK (scope IN ('store', 'register')),
  terminal_id   TEXT REFERENCES terminals(id) ON DELETE RESTRICT,
  prefix        TEXT NOT NULL DEFAULT '',
  padding       INTEGER NOT NULL DEFAULT 6 CHECK (padding BETWEEN 1 AND 12),
  reset_cadence TEXT NOT NULL DEFAULT 'yearly'
                CHECK (reset_cadence IN ('never', 'daily', 'monthly', 'yearly')),
  next_value    INTEGER NOT NULL DEFAULT 1 CHECK (next_value >= 1),
  last_reset_on TEXT,
  updated_at    INTEGER NOT NULL,
  CHECK ((scope = 'register') = (terminal_id IS NOT NULL))
) STRICT;

CREATE TABLE IF NOT EXISTS terminal_config (
  terminal_id  TEXT PRIMARY KEY REFERENCES terminals(id) ON DELETE CASCADE,
  version      INTEGER NOT NULL CHECK (version > 0),
  config_json  TEXT NOT NULL CHECK (json_valid(config_json)),
  updated_at   INTEGER NOT NULL
) STRICT, WITHOUT ROWID;


-- ---------------------------------------------------------------------------
-- Admin identity, devices, approval grants
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS users (
  id                    TEXT PRIMARY KEY,
  email                 TEXT,
  name                  TEXT NOT NULL,
  role                  TEXT NOT NULL CHECK (role IN ('owner', 'staff', 'cashier')),
  must_change_password  INTEGER NOT NULL DEFAULT 0 CHECK (must_change_password IN (0, 1)),
  active                INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
  created_at            INTEGER NOT NULL,
  CHECK (email IS NULL OR email = lower(email))
) STRICT;

CREATE TABLE IF NOT EXISTS user_stores (
  user_id   TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  store_id  TEXT NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
  PRIMARY KEY (user_id, store_id)
) STRICT, WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS sessions (
  id                   TEXT PRIMARY KEY,
  user_id              TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash           TEXT NOT NULL,
  token_prefix         TEXT NOT NULL,
  created_at           INTEGER NOT NULL,
  last_seen_at         INTEGER NOT NULL,
  expires_at           INTEGER NOT NULL,
  absolute_expires_at  INTEGER NOT NULL,
  revoked_at           INTEGER,
  ua_hash              TEXT,
  CHECK (expires_at > created_at),
  CHECK (absolute_expires_at >= expires_at)
) STRICT;

CREATE TABLE IF NOT EXISTS devices (
  id                TEXT PRIMARY KEY,
  store_id          TEXT NOT NULL REFERENCES stores(id) ON DELETE RESTRICT,
  terminal_id       TEXT NOT NULL REFERENCES terminals(id) ON DELETE RESTRICT,
  name              TEXT NOT NULL,
  credential_prefix TEXT NOT NULL,
  credential_hash   TEXT NOT NULL,
  install_id        TEXT NOT NULL,
  enrolled_by       TEXT REFERENCES users(id) ON DELETE RESTRICT,
  enrolled_at       INTEGER NOT NULL,
  last_seen_at      INTEGER,
  revoked_at        INTEGER,
  -- Issued with each token (JWT TTL + grace). Lets the terminal tell "offline for an hour"
  -- from "revoked three weeks ago"; policy, not a billing gate, lives in terminal_config.
  lease_expires_at  INTEGER,
  app_version       TEXT
) STRICT;

CREATE TABLE IF NOT EXISTS enrol_codes (
  code_hash       TEXT PRIMARY KEY,
  store_id        TEXT NOT NULL REFERENCES stores(id) ON DELETE RESTRICT,
  terminal_id     TEXT NOT NULL REFERENCES terminals(id) ON DELETE RESTRICT,
  created_by      TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  created_at      INTEGER NOT NULL,
  expires_at      INTEGER NOT NULL,
  used_at         INTEGER,
  used_device_id  TEXT REFERENCES devices(id) ON DELETE SET NULL,
  CHECK (expires_at > created_at)
) STRICT;

CREATE TABLE IF NOT EXISTS approval_grants (
  id                TEXT PRIMARY KEY,
  user_id           TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  terminal_id       TEXT NOT NULL REFERENCES terminals(id) ON DELETE CASCADE,
  scope             TEXT NOT NULL CHECK (scope IN ('approve')),
  max_overrides     INTEGER NOT NULL DEFAULT 0 CHECK (max_overrides >= 0),
  max_discount_pct  INTEGER NOT NULL DEFAULT 0 CHECK (max_discount_pct BETWEEN 0 AND 100),
  issued_at         INTEGER NOT NULL,
  expires_at        INTEGER NOT NULL,
  revoked_at        INTEGER,
  CHECK (expires_at > issued_at)
) STRICT;


-- ---------------------------------------------------------------------------
-- Catalog: items, prices, identifiers, change log
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS items (
  id                  TEXT PRIMARY KEY,
  name                TEXT NOT NULL,
  name_local          TEXT,
  dimension           TEXT NOT NULL CHECK (dimension IN ('mass', 'volume', 'count')),
  pricing_mode        TEXT NOT NULL CHECK (pricing_mode IN ('fixed_only', 'tiered', 'linear')),
  base_unit           TEXT NOT NULL REFERENCES units(unit) ON DELETE RESTRICT,
  default_sales_unit  TEXT NOT NULL REFERENCES units(unit) ON DELETE RESTRICT,
  tax_rate_id         TEXT REFERENCES tax_rates(id) ON DELETE RESTRICT,
  hsn                 TEXT,
  price_includes_tax  INTEGER NOT NULL DEFAULT 1 CHECK (price_includes_tax IN (0, 1)),
  mrp_minor           INTEGER CHECK (mrp_minor IS NULL OR mrp_minor >= 0),
  allow_discount_pct  INTEGER NOT NULL DEFAULT 0 CHECK (allow_discount_pct BETWEEN 0 AND 100),
  restrict_price_edit INTEGER NOT NULL DEFAULT 0 CHECK (restrict_price_edit IN (0, 1)),
  prompt_qty          INTEGER NOT NULL DEFAULT 0 CHECK (prompt_qty IN (0, 1)),
  scale_qty           INTEGER NOT NULL DEFAULT 0 CHECK (scale_qty IN (0, 1)),
  active              INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
  -- Informational, server clock. NOT a sync cursor: catalog_changes is the only cursor.
  updated_at          INTEGER NOT NULL
) STRICT;

-- SCD2. store_id NULL is the franchise default; a non-null store_id overrides it.
-- version is per item: one PUT closes the open rows of the scope it writes and opens the
-- next version for every tier of that scope.
CREATE TABLE IF NOT EXISTS item_prices (
  item_id     TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  store_id    TEXT REFERENCES stores(id) ON DELETE CASCADE,
  qty_base    INTEGER NOT NULL CHECK (qty_base > 0),
  version     INTEGER NOT NULL CHECK (version > 0),
  price_minor INTEGER NOT NULL CHECK (price_minor >= 0),
  mrp_minor   INTEGER CHECK (mrp_minor IS NULL OR mrp_minor >= 0),
  valid_from  INTEGER NOT NULL,
  valid_to    INTEGER,
  created_by  TEXT REFERENCES users(id) ON DELETE RESTRICT,
  created_at  INTEGER NOT NULL,
  CHECK (valid_to IS NULL OR valid_to > valid_from)
) STRICT;

CREATE TABLE IF NOT EXISTS item_identifiers (
  id          TEXT PRIMARY KEY,
  store_id    TEXT NOT NULL REFERENCES stores(id) ON DELETE RESTRICT,
  alias       TEXT NOT NULL
              CHECK (alias = upper(alias) AND alias = trim(alias) AND length(alias) BETWEEN 1 AND 64),
  kind        TEXT NOT NULL CHECK (kind IN ('alias', 'sku', 'ean', 'plu')),
  item_id     TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  qty_base    INTEGER CHECK (qty_base IS NULL OR qty_base > 0),
  active      INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
  updated_at  INTEGER NOT NULL
) STRICT;

-- Append-only. A row says "document X changed"; it is not a copy of the document. seq is
-- allocated from catalog_seq, never by AUTOINCREMENT. entity_id is a document key:
--   item/item_price/item_identifier/user -> the id; unit -> the unit; tax_rate -> the id;
--   store/store_settings -> the store id; numbering_policy -> the policy id;
--   terminal_config -> the terminal id.
-- For item_price, entity_id is the item and version is the price version to (re)read in full.
CREATE TABLE IF NOT EXISTS catalog_changes (
  seq         INTEGER PRIMARY KEY,
  entity      TEXT NOT NULL CHECK (entity IN (
                'item', 'item_price', 'item_identifier', 'unit', 'tax_rate', 'user',
                'store', 'store_settings', 'numbering_policy', 'terminal_config')),
  entity_id   TEXT NOT NULL,
  op          TEXT NOT NULL CHECK (op IN ('upsert', 'delete')),
  version     INTEGER CHECK (version IS NULL OR version > 0),
  changed_at  INTEGER NOT NULL,
  CHECK (entity <> 'item_price' OR version IS NOT NULL)
) STRICT;


-- ---------------------------------------------------------------------------
-- Billing
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS shifts (
  id            TEXT PRIMARY KEY,
  store_id      TEXT NOT NULL REFERENCES stores(id) ON DELETE RESTRICT,
  terminal_id   TEXT NOT NULL REFERENCES terminals(id) ON DELETE RESTRICT,
  opened_by     TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  closed_by     TEXT REFERENCES users(id) ON DELETE RESTRICT,
  business_date TEXT NOT NULL
                CHECK (business_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  opened_at     INTEGER NOT NULL,
  closed_at     INTEGER,
  CHECK (closed_at IS NULL OR closed_at >= opened_at)
) STRICT;

-- Immutable once written. The only permitted mutation is voiding; money never changes.
-- received_at, invoice_no, staff_inactive and synced_at are written by the server, so they
-- are NULL (or 0) on the terminal's copy until the push is acknowledged.
CREATE TABLE IF NOT EXISTS bills (
  id              TEXT PRIMARY KEY,
  store_id        TEXT NOT NULL REFERENCES stores(id) ON DELETE RESTRICT,
  terminal_id     TEXT NOT NULL REFERENCES terminals(id) ON DELETE RESTRICT,
  device_id       TEXT REFERENCES devices(id) ON DELETE SET NULL,
  user_id         TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  auth_method     TEXT NOT NULL CHECK (auth_method IN ('password', 'pin_offline')),
  shift_id        TEXT REFERENCES shifts(id) ON DELETE SET NULL,
  business_date   TEXT NOT NULL
                  CHECK (business_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  local_seq       INTEGER NOT NULL CHECK (local_seq > 0),
  display_no      TEXT NOT NULL,
  invoice_no      TEXT,
  total_minor     INTEGER NOT NULL CHECK (total_minor >= 0),
  rounding_minor  INTEGER NOT NULL DEFAULT 0 CHECK (rounding_minor BETWEEN -100 AND 100),
  created_at      INTEGER NOT NULL,
  received_at     INTEGER,
  synced_at       INTEGER,
  voided          INTEGER NOT NULL DEFAULT 0 CHECK (voided IN (0, 1)),
  voided_at       INTEGER,
  voided_by       TEXT REFERENCES users(id) ON DELETE RESTRICT,
  void_reason     TEXT,
  staff_inactive  INTEGER NOT NULL DEFAULT 0 CHECK (staff_inactive IN (0, 1)),
  CHECK (voided = 1 OR (voided_at IS NULL AND voided_by IS NULL AND void_reason IS NULL)),
  CHECK (voided = 0 OR voided_at IS NOT NULL)
) STRICT;

-- The receipt. item_name, prices and tax are copied here on purpose: reports read this table
-- and never re-derive money from item_prices, which is mutable history.
CREATE TABLE IF NOT EXISTS bill_lines (
  bill_id          TEXT NOT NULL REFERENCES bills(id) ON DELETE CASCADE,
  line_no          INTEGER NOT NULL CHECK (line_no > 0),
  item_id          TEXT NOT NULL REFERENCES items(id) ON DELETE RESTRICT,
  item_name        TEXT NOT NULL,
  dimension        TEXT NOT NULL CHECK (dimension IN ('mass', 'volume', 'count')),
  qty_base         INTEGER NOT NULL CHECK (qty_base > 0),
  price_version    INTEGER NOT NULL CHECK (price_version > 0),
  price_source     TEXT NOT NULL CHECK (price_source IN ('list', 'tier', 'override', 'manual')),
  list_price_minor INTEGER NOT NULL CHECK (list_price_minor >= 0),
  price_minor      INTEGER NOT NULL CHECK (price_minor >= 0),
  discount_minor   INTEGER NOT NULL DEFAULT 0 CHECK (discount_minor >= 0),
  price_breakdown  TEXT NOT NULL CHECK (json_valid(price_breakdown)),
  line_total_minor INTEGER NOT NULL CHECK (line_total_minor >= 0),
  tax_rate_bp      INTEGER NOT NULL CHECK (tax_rate_bp BETWEEN 0 AND 10000),
  cgst_minor       INTEGER NOT NULL DEFAULT 0 CHECK (cgst_minor >= 0),
  sgst_minor       INTEGER NOT NULL DEFAULT 0 CHECK (sgst_minor >= 0),
  cess_minor       INTEGER NOT NULL DEFAULT 0 CHECK (cess_minor >= 0),
  override_by      TEXT REFERENCES users(id) ON DELETE RESTRICT,
  override_reason  TEXT CHECK (override_reason IS NULL OR override_reason IN
                    ('damaged', 'near_expiry', 'haggling', 'staff', 'other')),
  override_note    TEXT,
  policy_violation INTEGER NOT NULL DEFAULT 0 CHECK (policy_violation IN (0, 1)),
  stale_price      INTEGER NOT NULL DEFAULT 0 CHECK (stale_price IN (0, 1)),
  -- Server-computed from the price version valid at bills.created_at. The terminal's
  -- list_price_minor is a claim; a difference sets price_mismatch. Both are diagnostics:
  -- money stays in price_minor and line_total_minor and is never rewritten.
  computed_list_price_minor INTEGER
                    CHECK (computed_list_price_minor IS NULL OR computed_list_price_minor >= 0),
  price_mismatch   INTEGER NOT NULL DEFAULT 0 CHECK (price_mismatch IN (0, 1)),
  PRIMARY KEY (bill_id, line_no),
  CHECK (price_source NOT IN ('override', 'manual') OR override_by IS NOT NULL)
) STRICT, WITHOUT ROWID;

-- Append-only stock ledger hook. One row per billed line; adjustments have no bill.
CREATE TABLE IF NOT EXISTS inventory_movements (
  id            TEXT PRIMARY KEY,
  item_id       TEXT NOT NULL REFERENCES items(id) ON DELETE RESTRICT,
  bill_id       TEXT,
  bill_line_no  INTEGER,
  direction     TEXT NOT NULL CHECK (direction IN ('in', 'out')),
  qty_base      INTEGER NOT NULL CHECK (qty_base > 0),
  occurred_at   INTEGER NOT NULL,
  created_at    INTEGER NOT NULL,
  CHECK ((bill_id IS NULL) = (bill_line_no IS NULL)),
  FOREIGN KEY (bill_id, bill_line_no) REFERENCES bill_lines(bill_id, line_no) ON DELETE CASCADE
) STRICT;

-- Server-written. kind is an open vocabulary (no CHECK) so new audit kinds do not need a
-- table rebuild; the ones the UI filters on are listed in the index section.
CREATE TABLE IF NOT EXISTS audit_events (
  id              TEXT PRIMARY KEY,
  at              INTEGER NOT NULL,
  actor_user_id   TEXT REFERENCES users(id) ON DELETE SET NULL,
  actor_device_id TEXT REFERENCES devices(id) ON DELETE SET NULL,
  kind            TEXT NOT NULL,
  entity          TEXT NOT NULL,
  entity_id       TEXT NOT NULL,
  before_json     TEXT CHECK (before_json IS NULL OR json_valid(before_json)),
  after_json      TEXT CHECK (after_json IS NULL OR json_valid(after_json)),
  note            TEXT
) STRICT;


-- ---------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------

-- Catalog pull. The table's rowid btree is the cursor index; no second index is possible
-- or useful on seq. This one answers "what changed about this document last".
CREATE INDEX IF NOT EXISTS idx_catalog_changes_entity
  ON catalog_changes(entity, entity_id, seq);

-- Price set for one item version: a cover for the whole pull payload of that document.
CREATE INDEX IF NOT EXISTS idx_item_prices_lookup
  ON item_prices(item_id, version, store_id, qty_base, price_minor);

-- "Which version was valid at bill.created_at" per scope (store override, then default).
CREATE INDEX IF NOT EXISTS idx_item_prices_validity
  ON item_prices(item_id, store_id, valid_from);

-- IFNULL is required: SQLite treats NULLs as distinct in a unique index, so without it the
-- franchise default (store_id NULL) could have two rows for the same tier and version.
CREATE UNIQUE INDEX IF NOT EXISTS uq_item_prices_key
  ON item_prices(item_id, IFNULL(store_id, ''), qty_base, version);

-- At most one open row per tier per scope, so an interrupted price write is visible as a
-- constraint failure rather than two competing "current" prices.
CREATE UNIQUE INDEX IF NOT EXISTS uq_item_prices_open
  ON item_prices(item_id, IFNULL(store_id, ''), qty_base)
  WHERE valid_to IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_item_identifiers_alias
  ON item_identifiers(store_id, alias) WHERE active = 1;

CREATE INDEX IF NOT EXISTS idx_item_identifiers_item
  ON item_identifiers(item_id) WHERE active = 1;

CREATE INDEX IF NOT EXISTS idx_devices_prefix ON devices(credential_prefix);
CREATE UNIQUE INDEX IF NOT EXISTS uq_devices_credential_hash ON devices(credential_hash);

CREATE INDEX IF NOT EXISTS idx_sessions_prefix ON sessions(token_prefix);
CREATE UNIQUE INDEX IF NOT EXISTS uq_sessions_token_hash ON sessions(token_hash);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id, expires_at);

CREATE UNIQUE INDEX IF NOT EXISTS uq_users_email ON users(email) WHERE email IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_user_stores_store ON user_stores(store_id);

CREATE INDEX IF NOT EXISTS idx_approval_grants_terminal
  ON approval_grants(terminal_id, expires_at) WHERE revoked_at IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_numbering_policy_scope
  ON numbering_policy(store_id, scope, IFNULL(terminal_id, ''));

-- Second line of defence against a terminal reusing a sequence number with a new bill id.
CREATE UNIQUE INDEX IF NOT EXISTS uq_bills_store_terminal_seq
  ON bills(store_id, terminal_id, local_seq);

CREATE UNIQUE INDEX IF NOT EXISTS uq_bills_invoice_no
  ON bills(store_id, invoice_no) WHERE invoice_no IS NOT NULL;

-- Covers the report join: store + date range yields bill ids without touching the row, and
-- total_minor makes the per-day summary index-only.
CREATE INDEX IF NOT EXISTS idx_bills_store_date
  ON bills(store_id, business_date, id, total_minor) WHERE voided = 0;

-- Terminal outbox scan. On D1 every row has synced_at set at insert, so the predicate is
-- empty there and the index costs one page; the terminal runs this same DDL.
CREATE INDEX IF NOT EXISTS idx_bills_unsynced ON bills(created_at) WHERE synced_at IS NULL;

-- Covering for the per-store, per-date aggregate: lines of one bill are one range.
CREATE INDEX IF NOT EXISTS idx_bill_lines_report
  ON bill_lines(bill_id, item_id, qty_base, line_total_minor);

CREATE INDEX IF NOT EXISTS idx_bill_lines_item ON bill_lines(item_id, bill_id);

CREATE INDEX IF NOT EXISTS idx_bill_lines_override
  ON bill_lines(override_by, bill_id) WHERE override_by IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_inventory_movements_item
  ON inventory_movements(item_id, occurred_at);

CREATE INDEX IF NOT EXISTS idx_audit_events_kind_at ON audit_events(kind, at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_events_entity
  ON audit_events(entity, entity_id, at DESC);


-- ---------------------------------------------------------------------------
-- Seed rows: reference data only. Idempotent: provisioning may be re-run against an
-- existing database. Tenant-specific rows (first store, settings, terminal, numbering
-- policy, terminal config, owner) are NOT seeded here: provisioning inserts them with
-- random UUIDv7 ids and appends their catalog_changes rows. Fixed ids would be identical
-- in every tenant's database, so a token's store/terminal claims would resolve in any of
-- them (docs/review-architecture.md, failure 1).
-- ---------------------------------------------------------------------------

INSERT OR IGNORE INTO schema_meta (id, schema_version, tenant_id, app_id, provisioned_at)
VALUES (1, 1, 'tnt_REPLACE', 'app_REPLACE', unixepoch());

INSERT OR IGNORE INTO catalog_seq (id, last_seq, pruned_below) VALUES (1, 10, 0);

INSERT OR IGNORE INTO units (unit, unit_group, precision, factor_to_base) VALUES
  ('g',   'mass',   0, 1),
  ('kg',  'mass',   3, 1000),
  ('ml',  'volume', 0, 1),
  ('l',   'volume', 3, 1000),
  ('pcs', 'count',  0, 1);

INSERT OR IGNORE INTO tax_rates (id, name, rate_bp) VALUES
  ('0192f3c0-0000-7000-8100-000000000001', 'GST 0%',  0),
  ('0192f3c0-0000-7000-8100-000000000002', 'GST 5%',  500),
  ('0192f3c0-0000-7000-8100-000000000003', 'GST 12%', 1200),
  ('0192f3c0-0000-7000-8100-000000000004', 'GST 18%', 1800),
  ('0192f3c0-0000-7000-8100-000000000005', 'GST 28%', 2800);

-- Seeds are catalog rows too: a terminal whose cursor is 0 must learn about them through the
-- same log as everything else. Fresh databases only: on a pruned log, re-inserting seqs the
-- floor says are gone would resurrect them.
INSERT INTO catalog_changes (seq, entity, entity_id, op, version, changed_at)
SELECT column1, column2, column3, 'upsert', NULL, unixepoch() FROM (VALUES
  (1,  'unit', 'g'),
  (2,  'unit', 'kg'),
  (3,  'unit', 'ml'),
  (4,  'unit', 'l'),
  (5,  'unit', 'pcs'),
  (6,  'tax_rate', '0192f3c0-0000-7000-8100-000000000001'),
  (7,  'tax_rate', '0192f3c0-0000-7000-8100-000000000002'),
  (8,  'tax_rate', '0192f3c0-0000-7000-8100-000000000003'),
  (9,  'tax_rate', '0192f3c0-0000-7000-8100-000000000004'),
  (10, 'tax_rate', '0192f3c0-0000-7000-8100-000000000005'))
WHERE NOT EXISTS (SELECT 1 FROM catalog_changes);
