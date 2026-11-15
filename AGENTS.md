# AGENTS.md — Traasu

Offline-first grocery billing POS for an Indian multi-store chain, replacing GoFrugal. This repo is the
design: decisions, DDL, API contract, OpenSpec specs and Python checks. Implementation code arrives story by
story through OpenSpec changes.

## Setup (mise)

All tools and tasks come from `mise.toml`; do not install Python, Go, Node or the OpenSpec CLI another way.

1. Install mise ([getting started](https://mise.jdx.dev/getting-started.html)):
   - macOS / Linux: `curl -fsSL https://mise.run | sh` (or `brew install mise`)
   - Windows (PowerShell): `irm https://mise.run/powershell | iex`
2. Optional, for interactive shells: activate it so the project's tools are on `PATH`.
   - zsh: `echo 'eval "$(~/.local/bin/mise activate zsh)"' >> "${ZDOTDIR:-$HOME}/.zshrc"`
   - bash: `echo 'eval "$(~/.local/bin/mise activate bash)"' >> ~/.bashrc`
   - PowerShell: `Add-Content $PROFILE '(&mise activate pwsh) | Out-String | Invoke-Expression'`
   - With a package-manager install, write `mise` instead of `~/.local/bin/mise`.
3. In the repo: read `mise.toml`, then `mise install` (pinned exactly by `mise.lock`).
4. `mise tasks ls` lists the tasks. Agents and CI use `mise run …` / `mise exec -- …`; no activation needed.

| Task | Runs |
|---|---|
| `mise run check` | `docs/check-schema.py`, `check-invariants.py`, `explain-queries.py`: required after any DDL change, before every commit |
| `mise run spec:validate` | `openspec validate --all --strict` |
| `mise run spec:list` | capabilities and in-flight changes |
| `mise run check:capacity` | bytes per bill on the real schema (~4 min, optional) |
| `mise run gofrugal:fetch` | refresh the local GoFrugal help copy (see below) |

## Where things are

| Path | What |
|---|---|
| `openspec/specs/<capability>/spec.md` | The agreed v1 behavior, one folder per capability. Read the spec before code. |
| `openspec/roadmap.md` | Epics → stories, status, build order. Pick work here. |
| `openspec/changes/<story>/` | One change per story: proposal, design, tasks (and a delta if behavior changes). |
| `openspec/config.yaml` | Project context and artifact rules the OpenSpec skills follow. |
| `docs/DECISIONS.md` | Why: D1–D30, each with its cost. A later "Revised" paragraph supersedes earlier text. |
| `docs/d1-schema.sql`, `docs/migrations/` | DDL: 0001 + 0002–0008. Same DDL on D1 and the register's SQLite. |
| `docs/api-routes.md`, `docs/query-catalogue.md` | Route contract and the queries the schema must serve. |
| `docs/check-invariants.py` | Executable rules; its case labels are the scenarios specs cite. |
| `docs/gofrugal-parity.md` | Feature-by-feature comparison with GoFrugal and the open findings. |
| `reference/gofrugal/` | Local Markdown copy of GoFrugal help, git-ignored on purpose (copyright, robots.txt). May be absent. |

## Workflow (OpenSpec)

1. Take the next `Ready` story from `openspec/roadmap.md`.
2. `/openspec-propose <story-id>`: the change names the story id and the requirements it implements.
3. Review, then `/openspec-apply-change`; implement only those requirements. A spec gap is reported, not invented.
4. `mise run check` (if DDL changed) and `mise run spec:validate`, then `/openspec-archive-change` and set the story's status.

## Rules that never bend

- Money is integer paise; quantities are integer base units (g, ml, pcs). No floats anywhere.
- Bills and bill lines are immutable. Corrections are voids (`bill_voids`), never updates or deletes.
- The register's local catalog decides a sale's price; the server flags (`stale_price`, `price_mismatch`) but never reprices.
- Stock is tracked per batch and may go negative; it never blocks a sale.
- Every write the register sends is replay-safe (client ids, `ON CONFLICT DO NOTHING`, triggers that fire once).
- A rule that must hold on both D1 and the register lives in the DDL (triggers), not only in the Worker.

## Conventions

- DDL: nothing is applied to any tenant yet, so `0001`, `0007`, `0008` are edited in place; each migration sets
  `schema_version`. Once a tenant exists, migrations are append-only.
- Cite evidence: a design claim names its D#, file or check case; anything not run is marked unverified.
- Branch per piece of work, PR to `main`; the owner reviews and merges.
- Never commit `package.json`, `package-lock.json`, `node_modules/`, `HANDOFF.md` or `reference/`.
- Cloudflare: account is on Workers Free. Ask before creating any Worker, D1 or R2 resource.
