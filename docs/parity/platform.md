# Platform slice — GoFrugal RetailEasy (RPOS7) parity and robustness audit

Scope: multi-store/HQ and the one-D1-per-enterprise shape (D18–D21), users/groups/menu rights, cashier and
device identity (D5–D8, D16, D17), practice/training login, purge vs archival (D22) and GST retention,
GST-style report output, printing, settings export/import, and release/migration safety.

Everything below was read from the design docs as they stand, and every defect was reproduced against the real
DDL with `docs/d1-schema.sql` + `docs/migrations/0002`–`0008` applied in order, foreign keys on, bills pushed
lines → header inside one transaction with `PRAGMA defer_foreign_keys = on` — the same harness `docs/check-invariants.py`
uses. The three repo checks are green as a baseline (`check-schema.py` ok, `check-invariants.py` 118/118
assertions ok, `explain-queries.py` ok).

Scratch scripts (not in the repo):
`docs/parity/repro/`P1-gst-report.py`, `P2-voids-practice.py`,
`P3-migration-atomicity.py`, outputs `P1.out`, `P2.out`, `P3.out`.

---

## 1. Verdicts

| # | Use case | GoFrugal (page) | Traasu (D#, file) | Verdict | Severity |
|---|---|---|---|---|---|
| 1 | Company/TIN data separation | `Multi-DB.md`, `FAQs-Multi-DB.md`, `Creating-Multi-company.md` — one **database per company with its own TIN**, custom licence, up to 3 extra companies, no offline billing in the extra companies | D18/D19: one D1 per enterprise, stores as rows (`DECISIONS.md`) | differs-by-design | — |
| 2 | Central roll-up of every outlet | `Multi-DB.md` (only the default company syncs with HQ) | D20/D21: one Worker, N D1s, hostname routing, control-plane META | differs-by-design | — |
| 3 | Legal identity per outlet (GSTIN, State, address) | `Multi-DB.md` FAQ 14: "This feature is for multiple TIN" — the TIN is the unit of separation | nothing: `stores` has `id, name, name_local, active, created_at` | **defect** | High |
| 4 | Users and their credentials | `Adding-a-new-user.md` (User name/Password/Display name/Reporting to/Activation/Default company-division-location/User Type Live\|Practice) | D17: Better Auth on the admin plane, `users` + `auth_user_id`; cashiers are PIN-only (D16) | differs-by-design | — |
| 5 | Groups and role-based rights | `Adding-a-new-Group.md`, `Security-Type.md` (User vs Role) | `users.role IN ('owner','staff','cashier')`, fixed (0001) | differs-by-design | — |
| 6 | Menu rights and per-screen password | `Configuring-the-Menu-Settings-for-the-new-User.md` (Function keys / Screen Operations / Tools Configuration, each with "set password to operate") | none — the admin UI is fixed and gated by role only | differs-by-design | — |
| 7 | Who may cancel a bill | same page: cancellation is a screen operation with an optional password, per role | `POST /v1/bills/:id/void` needs only a device JWT; `voids[]` carries a body-asserted `user_id` | **gap** | High |
| 8 | Who may approve a money exception | same page (role password gates the operation) | `POST /v1/approvals`; error list: `not_approver = user is not an active owner` | **risk** | Medium |
| 9 | Report access control | `Report-Settings.md` (per screen-operation enable + password) | reports are `/v1/admin/reports/*`, `owner\|staff` read, `user_stores` scoped (`api-routes.md` §2) | differs-by-design | — |
| 10 | Practice / training login | `Practice-Login.md`, `FAQs-Practice-Login.md`, `Creating-Practice-login.md`, `Creating-Practice-login-for-the-new-user.md` — a **second database** seeded from the live one, restricted, never visible in live data | nothing; and there is no column anywhere that could mark a bill as unreal | **gap** | Medium |
| 11 | Remove old transactions | `Purge-data.md` — permanent delete to a date, optionally keeping stock/outstandings, restart bill numbering | D22: archive to R2, keep `sales_daily`, never drop | differs-by-design | — |
| 12 | How long records must survive | `Purge-data.md` (no statutory guidance) | D22: "R2 keeps at least six years"; `tenant_flags.retention_months` owner-set in META | **defect** | Medium |
| 13 | GST output (rate-wise) | `GST-Reports.md` (Sales and SR register/summary — GST% wise, sales + returns + net) | no GST route; rate-wise **is** derivable from `bill_lines` (repro A) | **gap** | High |
| 14 | GST output (HSN summary) | GSTR-1 table 12 is mandatory (GSTN advisory, 25-04-2025), 4- or 6-digit by turnover, split B2B/B2C | `bill_lines.hsn` copied; **no UQC, no unit** on the line; `items.base_unit` mutable | **defect** | High |
| 15 | GST output (cancelled invoices, table 13) | table 13 mandatory from May 2025 | `bill_voids` keeps the bill and its number, but the archive job deletes both | **defect** | High |
| 16 | Report on an archived month | n/a (GoFrugal keeps everything until a purge) | `/v1/admin/reports/*` answer from `sales_daily` after archival (`api-routes.md` §3.6) | **defect** | High |
| 17 | Receipt / invoice content | `Print-Profile-Configurations.md` (profile design, regional-language item name) | `items.name_local` rides the catalog; the Rule 46 particulars have nowhere to come from | **gap** | High |
| 18 | Export/import settings between outlets | `Export-Import-Settings.md` (role-based security settings exported from one outlet, imported at another) | one tenant, one config; store overrides ride the catalog delta (D9) | differs-by-design | — |
| 19 | Hardware change / licence move | `Surrender-License.md`, `Surrender-Process.md`; `9a24ee6967-Offline-Billing-RPOS7.pdf.md` "Risks": restoring an old client backup duplicates bill numbers | D5/D6: `install_id` detects a cloned disk; re-enrol; `uq (store, terminal, local_seq)` + `duplicate_seq` parking | differs-by-design | — |
| 20 | Releasing a schema change to every outlet | `FAQs-Multi-DB.md` 13: "the service pack applied for one time will be automatically applied to all the databases" | `release-management.md`, D20/D21: migrate → deploy, canary first, `min_schema`, N-1 CI | **defect** | High |
| 21 | Invoice numbering, offline | `9a24ee6967-Offline-Billing-RPOS7.pdf.md` §Configuration: enabling offline billing **prompts to configure counter-wise bill series**; "Allow to edit bill number" and "Allow manual bill number" are incompatible with it | `invoice_no` is server-assigned per store (§9.6); the terminal prints `display_no` from a separate register series (0001 `numbering_policy` comment) | **defect** | Critical |
| 22 | Duplicate legal invoice number under concurrency | implied by the Offline-Billing "Risks" list | `uq_bills_invoice_no` + `trg_bills_invoice_no_once` — fails closed (repro §3) | match | — |

---

## 2. Findings

### F1 (row 21) — Critical — an offline bill is not a tax invoice: no legal number exists on the paper

**Scenario.** A register loses its uplink at 09:00 and bills 300 customers. Each bill prints
`bills.display_no` (`d1-schema.sql:303`, `TEXT NOT NULL`). The legal series is
`numbering_policy` scope `store`, which the **server** advances: "scope 'store' is the server-side legal
invoice series; scope 'register' is the terminal-local display series. The server never advances a register
counter and the terminal never advances a store counter" (`d1-schema.sql`, `numbering_policy`). §9.6 repeats it:
"Invoice numbers are server-assigned per store from `numbering_policy`; the terminal treats `invoice_no` as
opaque."

So the customer walks out holding a number that is not the invoice number, and the invoice number that appears
in GSTR-1 is one nobody saw. Rule 46(b) (CBIC, CGST Rules) requires "a consecutive serial number not exceeding
sixteen characters, in one or multiple series … unique for a financial year"; Rule 46(c) requires the date of
issue on the same document. GoFrugal's answer is the opposite one: enabling offline billing makes the product
prompt for **counter-wise bill series** at configuration time, and it refuses to combine offline billing with
"Allow to edit bill number" or "Allow manual bill number" — the number is fixed at the counter, in the counter's
own series.

**Repro.** `P1-gst-report.py` §F:

```
a terminal-local bill has invoice_no = [(None,)] (server assigns it on push)
uq_bills_invoice_no is partial, so NULL invoice numbers are unlimited: [(3,)]
```

Three bills in D1 with no invoice number, and the partial unique index (`d1-schema.sql:439`) is happy with any
number of them. `P2-voids-practice.py` §3 also shows nothing constrains the number's shape:
`a 22-character invoice_no: [(20, 'KTP/2026-27/00001234')]`.

**Smallest fix.** Let the register own the number. The `numbering_policy` row for `scope='register'` already
exists and already carries `prefix`/`padding`/`reset_cadence`; have the terminal allocate `invoice_no` from it at
save time, make the column effectively NOT NULL on the push path, and keep the store series for
admin-created corrections only. Add `CHECK (length(invoice_no) <= 16)` and make the per-register prefix
mandatory and distinct per store (`numbering_conflict` already exists to police collisions). The uniqueness
guarantee does not change: still one series per store (`uq_bills_invoice_no`).

**Cost.** One migration (the CHECK and, if desired, a per-store prefix uniqueness index), one terminal write
path, one Worker validation; the store counter stays for the rare admin bill. Half a day of design churn,
one day of code. It also makes F10 safe for free (see below) and removes the "number assigned later" edge
case from the archive story.

---

### F2 (row 3) — High — there is no GSTIN, no State and no address anywhere: GSTR-1 cannot be filed

**Scenario.** GSTR-1 is filed per GSTIN. A grocery chain with outlets in two States has at least two
registrations, and Rule 46(a)/(e)/(n) make the supplier's name, address and GSTIN, the recipient's State code
above ₹50,000, and the place of supply compulsory particulars. GoFrugal keeps company-per-TIN for exactly this
reason (`Multi-DB.md` FAQ 14: "This feature is for multiple TIN").

Traasu has nowhere to put any of it. `stores` is `id, name, name_local, active, created_at`
(`d1-schema.sql:68`), and the schema-wide search finds no other candidate:

```
stores columns: id, name, name_local, active, created_at
any gstin/state/address column anywhere in the schema:
  [('auth_session.ip_address',), ('staff_credentials.pin_hash',), ('staff_credentials.pin_salt',)]
```

(P1 §C.) D18's "one D1 per enterprise" is not the problem — a store row can carry its own registration. The
problem is that the design has no registration concept at all, so "which bills belong to which return" is
unanswerable, and a printed invoice cannot carry the supplier's GSTIN.

**Smallest fix.** A `gst_registrations(id, gstin, state_code, legal_name, address_json, active)` table and
`stores.gst_registration_id` — or, if the enterprise really has one registration per State and one store per
State, three columns on `stores` (`gstin`, `state_code`, `address_json`). The flat version is cheaper and
adequate: `GROUP BY COALESCE(s.gstin,'')` then *is* the return partition. Add the registration block to the
receipt template at the same time. `stores` is already a catalogued document (`catalog_changes.entity='store'`),
so terminals get it with the next pull for printing.

**Cost.** One migration, one admin field set, one receipt block. Half a day. No billing-path change.

---

### F3 (row 14) — High — the HSN summary cannot be produced: no UQC on the line, and `items.base_unit` is not frozen

**Scenario.** GSTR-1 table 12 must report HSN, description, **UQC**, **quantity**, taxable value and the four
tax amounts, per rate; GSTN's advisory of 25-04-2025 makes HSN reporting mandatory at 4 or 6 digits and splits
the table into B2B and B2C. `bill_lines` copies `hsn` (`0002:34`) but not the unit — it stores `dimension`
(`mass|volume|count`) and `qty_base`. The only unit is `items.base_unit`, and `units` carries `unit_group`,
not a GST UQC. So the summary can only ever say "1000 of something mass-like", while table 12 wants `KGS` or
`GMS`.

Worse, `items.base_unit` is only frozen by a Worker rule (`immutable_field`, `api-routes.md` §6), not by the DDL.
`trg_items_version` (`0008:230`) only demands a version bump:

```
Now change one item's base_unit the way a PATCH does today (version bumped, DDL allows it):
  accepted: items.base_unit is NOT frozen by the DDL, only by a Worker rule
  historical HSN block for i_dal now: [('0713', 'kg', 6000)]
```

(P1 §B.) One PATCH retroactively rewrites the unit of every past sale of that item.

**Smallest fix.** Add `units.uqc TEXT` (g→GMS, kg→KGS, ml→MLT, l→LTR, pcs→NOS, …) and copy `uqc` onto
`bill_lines` at insert, exactly the way `hsn` is copied. That single copy is the whole fix: once the unit is a
per-line fact, a later `items.base_unit` edit is harmless and **no new trigger is needed** — which is the same
reason `hsn` and `tax_rate_bp` already survive rate changes. Freezing `items.base_unit` in the DDL would also
work but is the more expensive half of the same idea (a frozen column needs an escape hatch the day someone
really does re-base an item).

**Cost.** One migration (column + `units` mapping rows), one terminal insert column. Half a day, and it is the
prerequisite for the HSN block of F11.

---

### F4 (row 16) — High — an archived month has no tax left in D1, and the report route answers anyway

**Scenario.** D22 exports a store-month to R2 and deletes the bills; `sales_daily` is kept so "reports on
archived months still work" (`api-routes.md` §3.6). But `sales_daily` is
`(store_id, business_date, item_id, qty_base, amount_minor, bill_count)` — money and quantity only. Every GST
number is gone with the lines.

```
sales_daily columns: store_id, business_date, item_id, qty_base, amount_minor, bill_count
before archive, s1 2026-11-12 totals: [(22333, 667)]     # taxable, tax
archive_before set to 2026-11-13
deleted bills for 2026-11-12: [(0,)]   lines left: [(0,)]
rate-wise query for 2026-11-12 now returns: [(None, None)]
what sales_daily still holds for 2026-11-12: [('s1','i_nohsn',2000,9000,1), ('s1','i_oil',1000,14000,1)]
```

(P1 §D.) So the month report shows ₹23,000 of sales and ₹0 of tax, silently. A GSTR-3B reconciliation, a
re-run of GSTR-1 after a notice, or a rate-wise cut for a past quarter is impossible without restoring R2 into a
scratch database — and `sales_daily`'s own zero is worse than an error, because nothing tells the reader that
the tax columns are missing rather than zero.

**Smallest fix (two options, cheapest first).**

1. *No schema change*: make a report whose range crosses `schema_meta.archive_before` return
   `409 range_archived` with `restore_required: true` for any cut that needs tax, exactly as `voided_bills`
   already degrades to `null`. Cost: one guard in the report handler, a documented restore runbook. It removes
   the lie but leaves the owner dependent on a restore.
2. *One small rollup*: `sales_daily_tax(store_id, business_date, tax_rate_bp, taxable_minor, cgst_minor,
   sgst_minor, igst_minor, cess_minor, invoice_count)` maintained by `trg_bills_rollup` / `trg_bill_voids_apply`
   and rebuilt with the same statement pattern as `sales_daily` (`0002:86`). At most six rate rows per store-day.
   Cost: one migration, two trigger edits, one rebuild statement, one query. Recommended.

Either way, also state in D22 what the archive *cannot* answer: HSN-wise history needs R2.

---

### F5 (row 15) — High — a void for an archived bill has nowhere to be recorded, and could not fix the rollup anyway

**Scenario.** D22 and `api-routes.md` §3.4 both promise: "A void for an archived bill is parked
`bill_archived`, never dropped: the sale happened and the record must outlive the retention window." The
parking space is the terminal's local outbox — the server has none. The design's own position on the terminal
disk is that it is disposable (D4 revocation, D5 re-enrol, `device_moved` wipes the credential, 30 days of bills
kept for the restore re-push).

**Repro.** `P2-voids-practice.py` §1, with `archive_before = 2026-09-11` and the September bills exported and
deleted:

```
INSERT INTO bill_voids for the archived bill: refused -> void_store_mismatch
(with the trigger out of the way, the foreign key refuses it too)
  same insert, trigger dropped: refused -> FOREIGN KEY constraint failed
candidate tables that could hold a void for a bill that no longer exists: ['bill_voids']
bill_voids columns: [id, bill_id, store_id, terminal_id, user_id, reason, created_at, received_at, synced_at]
```

And even if a row could be stored, the money cannot be corrected: `sales_daily` needs
`(store, date, item, qty, amount)` to decrement and `bill_voids` carries no item, no quantity and no amount.
The September rollup stays overstated forever:

```
September totals after a late void that the schema cannot record:
  [('i1', 1000, 16000), ('i2', 500, 24000)]
```

**Smallest fix.** A `bill_voids_parked(id, store_id, terminal_id, user_id, bill_id, reason, created_at,
received_at, lines_json)` table with **no** foreign key to `bills`; the Worker inserts there and answers
`200 {parked:true, bill_archived}` instead of `409`; the terminal sends the bill's `lines[]` (it has them
locally) so the Worker can decrement `sales_daily` per item and, if F4's option 2 lands, the rate rollup too.
The monthly archive job appends the month's parked voids to `archive/<tenant>/<store>/<yyyy-mm>-voids.jsonl.gz`
so the R2 record is complete.

**Cost.** One table, one payload field, one route branch, one job step. One day.

---

### F6 (row 7) — High — nothing gates a void

**Scenario.** `POST /v1/bills/:id/void` authenticates the *device* and takes `{id, bill_id, user_id, reason,
created_at}` in the body; `voids[]` on the push works the same way. `bills.voided_by` and `bill_voids.user_id`
are then whatever the terminal asserted. `policy_violation` claims to flag a "void beyond ceiling", but no
ceiling exists in the schema: `approval_grants.scope` is `CHECK (scope IN ('approve'))`, `bill_voids` has no
grant column, and `store_settings` has only `business_day_start_minutes` and `rounding_mode`.

The consequence is the oldest one in retail: bill a cash sale, void it, keep the cash. Every other money
exception in this design has a defence — `approval_grants` for overrides, `price_source`/`override_by` on the
line, `audit_events` — and the void, which is the largest single-money action available at a register, has none.
GoFrugal treats cancellation as a **screen operation** with an optional per-role password
(`Configuring-the-Menu-Settings-for-the-new-User.md`, Screen Operations tab).

**Smallest fix.** Finish the mechanism that already exists: `ALTER TABLE bill_voids ADD COLUMN approval_grant_id
TEXT` (plain TEXT, no FK — same pattern as `bill_lines.approval_grant_id`, `0008:102`), add
`store_settings.void_requires_grant`, and let the Worker accept a void with no grant as it does today but flag
`policy_violation`. Offline, fall back to a cached unexpired grant or a per-store daily void ceiling, exactly as
D3 does for discounts.

**Cost.** One migration, one Worker rule, one terminal prompt. Half a day.

---

### F7 (row 20) — High — a migration file is not atomic, and the documented recovery does not work

**Scenario.** `release-management.md` says: "A failed migration leaves the tenant at the previous number and the
previous tables, and the deploy job never runs"; and `d1-schema.md:805` builds on that. Two facts contradict it:

1. `wrangler d1 migrations apply` sends the migration as `<file text>` + `INSERT INTO d1_migrations`
   (`workers-sdk`, `packages/wrangler/src/d1/migrations/helpers.ts`, `buildMigrationQuery`). There is no
   `BEGIN`/`COMMIT` around it.
2. D1 runs statements in auto-commit — the repo says so itself (`d1-schema.sql` header: D1 "runs each in an
   implicit transaction"; `d1-limits.md:60`: "Atomicity comes from `db.batch()`").

So each statement commits on its own, and the file's own `UPDATE schema_meta SET schema_version = 8` is the last
statement of `0008`.

**Repro.** `P3-migration-atomicity.py`, applying `0001`–`0007`, then `0008` up to a failure at statement 25:

```
0008: 732 lines, 106 statements. contains BEGIN: False
after 25 statements the next one fails
  schema_version      = 7  (the UPDATE ... = 8 is the last statement, so it never ran)
  bill_lines columns  = [..., 'price_basis_qty_base', 'approval_grant_id']
  items has version?  = False
  d1_migrations 0008  = 0 row  -> migrate.mjs counts it as NOT applied
the migrate job for that tenant is re-run:
  re-run aborted at statement 1: duplicate column name: archive_before
  schema_version      = 7
```

The tenant is wedged: `ALTER TABLE … ADD COLUMN` is not idempotent, so the documented "fix forward with a
compensating migration" is also blocked (the re-run cannot even reach a new statement). `/health` reports
`schema_version 7` against a Worker with `min_schema 8`, so every diagnostic route answers `503 schema_mismatch`
while the tables are neither at 7 nor at 8, and the terminals cannot sync.

**Smallest fix.**

1. One DDL statement per migration file from here on (a file is then atomic by construction). It costs
   file numbering, nothing else, and `infra/migrate.mjs` already reads back `schema_version` and the
   `d1_migrations` count.
2. Make the migrate job abort the *fleet* on a shape mismatch, not just on a non-zero exit: after each tenant,
   compare `schema_meta.schema_version` **and** a `pragma_table_info` fingerprint against the expected shape,
   and stop with the tenant's pre-run `wrangler` backup bookmark in the error message.
3. Correct `release-management.md` and `d1-schema.md:805` to say what is true: a failed file can leave partial
   DDL; recovery is the bookmark restore (`/health` then reports `schema_mismatch` until it completes), or
   manual surgery.

**Cost.** ~40 lines in `infra/migrate.mjs`, a policy sentence, a rename of `0009` onwards. Half a day, and it is
the difference between a recoverable fleet and a support incident.

---

### F8 (row 12) — Medium — the owner can configure an unlawful retention window

**Scenario.** D22 puts `retention_months` in META and lets the owner set it. Section 36 of the CGST Act requires
records to be kept "until the expiry of seventy-two months from the due date of furnishing of annual return for
the year pertaining to such accounts". For a bill dated 1 April 2026 (FY 2026-27), the annual return is due
31 December 2027, so the record must survive to 31 December 2033 — about 7 years 9 months after the sale. A
`retention_months = 24` in META deletes the invoice at 24 months, and the only copy left is R2, which is allowed
but is also the thing the shop must be able to produce for the officer.

D22's "R2 keeps at least six years" is right in spirit and about eight months short in the worst case.

**Smallest fix.** Validate in the admin route and in META: `retention_months >= 96`, or better, compute the
floor from the financial year ("keep until 31 December of the seventh year after the FY of the bill") and refuse
a value below it with `422 validation_failed`. Keep the owner's choice above the floor. Add the sentence to D22
so the accountant conversation is settled rather than pending.

**Cost.** One validation, one constant, one sentence. An hour.

---

### F9 (row 8) — Medium — "manager" cannot approve anything, only an owner can

**Scenario.** `POST /v1/approvals` is documented as "device JWT + manager email/password", but the error list
says `not_approver: user is not an active owner`. `users.role` is `('owner','staff','cashier')` and
`approval_grants` has no capability column:

```
users.role CHECK: (role IN ('owner', 'staff', 'cashier'))
approval_grants columns: [id, user_id, terminal_id, scope, max_overrides, max_discount_pct, issued_at,
                          expires_at, revoked_at]
a store manager is users.role = staff
```

(P2 §4.) In a five-store chain that leaves two choices: the owner's password lives at every till, or every
over-ceiling discount stops for a phone call to the owner. D3 then does what it has to — falls back to the
ceiling — so the practical effect is that discounts are capped permanently, and the owner's password is shared
informally. GoFrugal's model (a role password on a screen operation) is the same mechanism but assignable to
any group.

**Smallest fix.** `users.can_approve INTEGER NOT NULL DEFAULT 0` plus an optional `users.max_discount_pct`
ceiling, set by an owner; the approvals route checks the capability instead of `role='owner'`. No table rebuild,
no new vocabulary.

**Cost.** One migration, one check, one admin toggle. Two hours.

---

### F10 (row 10) — Medium — practice/training login: nothing exists, and the only safe place is a separate file

**Scenario.** GoFrugal ships a whole sub-product for this: `Practice-Login.md` describes a **separate practice
database** created from a live copy ("Tools >> Configuration >> Create Practice Database [Live copy]"), with
restricted menus ("printing is not allowed", "privilege for accessing security manager, VAT reports etc. is
restricted"), a different colour theme so the user knows, and its own users (`Creating-Practice-login.md`,
`User Type = Practice User` in `Adding-a-new-user.md`). `FAQs-Practice-Login.md` 09 admits there is **no way to
clear it** — "As of now, there is no option provided to clear the data" — and 12 says you cannot stop the setup
once it starts.

Traasu has no training mode. The important question is whether the design has a **safe place** for one, and the
answer is yes — but only one.

*Unsafe:* a `stores` row called "Training Room". Reproduced in `P2-voids-practice.py` §2:

```
Any column that could mark a bill as not-real: []
a 'training' store inside the same D1 sells 1 kg of dal. Immediately:
  it is in sales_daily: [('s_train', '2026-11-14', 'i1', 16000)]
  it is in the rate-wise GST query for the enterprise: [(500, 16238)]
```

There is no flag on `bills`, `stores` or `terminals` that any report could filter, and once F2 is fixed the
training store shares the enterprise's GSTIN unless someone remembers to give it a dummy one — so training
bills would land in the return.

*Safe:* a second local SQLite file, `training.db`, opened by the same Wails binary. `d1-schema.sql` already runs
"unchanged" on the terminal (file header), so the file is a complete register with items, prices and numbering,
and it has no way to reach the cloud:

```
a fresh training.db has devices rows: 0 | enrol_codes: 0
a training bill in training.db: invoice_no = None (server-assigned, and there is no server)
DELETE the training store's bill: ACCEPTED
```

`/v1/enroll` needs a one-time code minted by an owner inside a tenant D1, and `/v1/token` needs the `devices`
row the credential names; a training file has neither, so no route can accept a bill from it. And because
`invoice_no` comes from the server, a training bill can never consume a GST serial — the strongest guarantee in
the whole design for this feature.

**One trap, in writing.** Do not seed `training.db` by copying the live register's database (that is GoFrugal's
"Live copy" behaviour). A copy carries `numbering_policy` and `local_seq`, so the first training bill would
re-issue a number the shop has already put on a customer's paper. Seed from schema + a catalog snapshot, reset
the counters, and — once F1 makes the number local — force the prefix to `TRAIN/`. Printing should carry a
"TRAINING — NOT A TAX INVOICE" banner, since GoFrugal forbids printing in practice mode altogether and the
banner is the cheaper version of the same guarantee.

**Cost.** One menu item, one DB path, one seed routine, one banner. One to two days, mostly UI. It is the
cheapest of the three "missing" GoFrugal features in this slice precisely because the schema needs no change.

---

### F11 (row 13) — High — no GST report route, although the numbers are derivable

**Scenario.** GoFrugal's `GST-Reports.md` is Sales-and-SR register/summary **GST% wise**, with sales, returns and
net. Traasu has `/v1/admin/reports/sales-by-item` and `/day-summary` only; §8 explicitly refuses a report
builder. There is no route an accountant could use, and even the item cut cannot produce a rate-wise block.

The good news is that the rate-wise block is one query away, because tax is copied onto the line and never
re-derived (D2, D23). Reproduced with a 5% line, an 18% line, a voided 18% bill and an inter-state IGST line
(P1 §A):

```
store s1 (intra-state), 2026-11-13..14, voided excluded
  rate   taxable    cgst    sgst   igst  cess     value  invs
     5     15238     381     381      0     0     16000     1
    18     11441    1029    1030      0     0     13500     1
```

The voided bill is excluded without special-casing, the `cgst = floor(tax/2)` trigger rule holds, and IGST
survives because `0003` added it and `0008` fixed the identity that had omitted it.

**Smallest fix.** `GET /v1/admin/reports/gst-summary?from=&to=&store_id=` returning two blocks —
`by_rate` (the query above) and `by_hsn` (P1 §B, once F3 lands) — plus a `format=json` shaped like the GST
portal's GSTR-1 sections so the accountant can upload rather than retype. Keep `format=csv` for the
human-readable copy.

**Cost.** One route, two queries, no schema change for the rate-wise half. One day. Depends on F2 for the header
and F3 for table 12.

---

## 3. What holds up (verified, no action)

- **Concurrent invoice allocation fails closed.** Two bills claiming one invoice number:
  `refused -> UNIQUE constraint failed: bills.store_id, bills.invoice_no`, and the failed `db.batch()` takes the
  bill's lines with it (one batch per bill, D12). Re-writing an assigned number is
  `refused -> invoice_no_already_assigned` (`trg_bills_invoice_no_once`, `0004:40`). P2 §3.
- **One D1 per enterprise is the right call against GoFrugal's Multi-DB**, for a reason the GoFrugal page states
  itself: in `Multi-DB.md`'s limitation table, offline billing, snapshot and HQ are *default company only*. A
  chain that put its second company in another TIN lost offline billing for it. Traasu gives every store the
  same offline treatment and splits only where the 10 GB cap forces it (D18), which is measurable and reversible.
- **One deploy for the fleet matches GoFrugal's own behaviour** — `FAQs-Multi-DB.md` 13: a service pack "will be
  automatically applied to all the databases". D20's canary stage and `min_schema` are strictly better than that,
  and the N-1 CI rule is the part that actually makes the fleet safe. Only F7 undermines it.
- **Hardware replacement is safer than GoFrugal's.** `Surrender-License.md` requires surrendering a licence
  because it is bound to a hard-disk id, and the Offline-Billing "Risks" list records the consequence of getting
  it wrong: restoring an old client backup after upload "duplicates the same bill number twice and stock also
  collapses". Traasu's `install_id` + `uq (store, terminal, local_seq)` + `duplicate_seq` parking turns that into
  a flagged row instead of corrupted stock.
- **No purge is correct.** `Purge-data.md` permanently deletes transactions and can restart bill numbering from
  1 — which would break Rule 46(b)'s "unique for a financial year" if done mid-year. D22's archive-never-delete
  is the right shape; F4, F5 and F8 are the parts that need finishing.
- **Export/Import Settings has no Traasu equivalent and needs none** (`Export-Import-Settings.md` is about
  copying role-based security config between outlets; one tenant, one config, and store differences already ride
  the catalog delta).
- **Report Settings' per-report password is unnecessary here** (`Report-Settings.md`): every report is
  `/v1/admin/reports/*`, readable by `owner|staff` only, `user_stores` scoped, and the device plane cannot reach
  them at all (`api-routes.md` §8). The role check is strictly stronger than a shared report password.
- **Tax is copied, not recomputed, so a rate change never rewrites history** — which is exactly the risk
  GoFrugal lists as "Modified VAT% may not affect offline client until latest download happens from the server"
  (`9a24ee6967-Offline-Billing-RPOS7.pdf.md`). `bill_lines.tax_rate_bp` + `hsn` + `item_version` make it moot.

---

## 4. Not checked

- **HQ features proper.** GoFrugal's HQ/central module has no local page in `reference/gofrugal/md/` beyond the
  Multi-DB and Bulk-Repack-in-HQ mentions; I did not fetch extra pages for it, so "what an HQ does that Traasu's
  cloud does not" is out of evidence.
- **`Reports-Bulk-Repack.md`, `Reports-Material-Issue.md`, `Reports-Price-drop.md`, `Short-Excess*`** — stock and
  repack slices; read the titles only.
- **`Create Practice Database` mechanics** (SQL instance creation, "2–20 minutes", "cannot be stopped") — desktop
  chrome, not a business rule.
- **`Security-Type` / groups at the granularity of individual function keys** — I checked that Traasu's three
  roles cannot express them; I did not enumerate which specific F-key rights a grocery shop actually needs.
- **Better Auth's own migration behaviour** (its tables are in `0007`; whether a Better Auth upgrade ships its own
  schema changes is out of scope, and F7's non-atomicity applies to it identically).
- **GSTR-3B / GSTR-9 / e-invoice / e-way bill** — outside the slice; GSTR-1 table 12 and 13 were the two the
  design touches.
- **The 5-page GoFrugal fetch budget was not used.** Every GoFrugal claim above cites a page already in
  `reference/gofrugal/md/`. Statutory claims cite CBIC/CGST text and GSTN's 25-04-2025 table-12 advisory, not
  GoFrugal.
