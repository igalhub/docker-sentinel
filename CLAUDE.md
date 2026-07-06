# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Commands

**Setup:**
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config/settings.yaml.example config/settings.yaml
```

**Run the checker (must be invoked from project root as a module, not as a script):**
```bash
python -m checker.check
```

**Run the dashboard:**
```bash
# Via Docker Compose (recommended — mounts results.db as read-only):
docker compose up dashboard

# Direct:
uvicorn dashboard.main:app --host 0.0.0.0 --port 8080
```

**Tests:**
```bash
# Offline suite — no Docker daemon required (what CI runs):
pytest -m "not docker" -v

# Live Docker suite — requires Docker daemon and permission to create containers:
pytest -m docker -v

# Single test file (replace with the file you want to run):
pytest tests/test_severity.py -v
```

**Install systemd timer (Linux only):**
```bash
bash systemd/install.sh
```

---

## Architecture

Two-process design. The checker and dashboard are deliberately separate processes sharing a single SQLite file (`results.db`):

```
systemd timer (every 5 min)
  └── python -m checker.check
        └── checker/docker_checker.py  (docker-py SDK, no subprocess/CLI)
              ├── restart_check()       — RestartCount + StartedAt from container.attrs
              ├── healthcheck_check()   — Health.Status from container.attrs["State"]
              ├── port_check()          — TCP connect to published ports on localhost
              └── log_activity_check()  — container.logs(since=N_hours_ago, tail=1)
        └── checker/db.py → results.db (SQLite, upsert by container name)

FastAPI dashboard (separate process)
  └── dashboard/main.py — READ-ONLY against results.db
        ├── GET /status  → JSON with per-container + per-check breakdown
        └── GET /        → HTML table, severity badges, staleness indicator
```

**Key files:**
- `checker/severity.py` — pure functions, no I/O: `compute_severity(check_type, value)` and `aggregate_severity(severities)`. Thresholds passed as a config dict, never hardcoded.
- `checker/docker_checker.py` — `check_container(container) -> dict` and `check_all(client, config) -> list[dict]`. All Docker access via docker-py; Docker errors set `severity="unknown"` on the affected check, never crash the run, and log a `logger.warning` so a real bug (e.g. a Docker API field rename) doesn't hide as a silent "unknown" blip.
- `checker/db.py` — `init_db`, `write_results`, `read_results`, `get_last_checked`. Per-check breakdown stored as JSON in a `checks` column. File-based connections close after each call (`_connect` context manager); the cached `:memory:` connection stays open across calls and is test-only (thread-safety risk if ever pointed at the live dashboard). Tests use `:memory:`.
- `checker/check.py` — entry point: connects via `docker.from_env()`, loads `config/settings.yaml`, runs `check_all`, writes to `results.db`.
- `dashboard/main.py` — FastAPI app; no write path to `results.db` anywhere in `dashboard/`. Optional HTTP Basic Auth via `SENTINEL_DASHBOARD_USER`/`SENTINEL_DASHBOARD_PASSWORD` (both or neither must be set); logs a startup warning if left unset. When auth is enabled, FastAPI's auto-registered `/docs`, `/redoc`, `/openapi.json` routes are disabled outright (they bypass per-route auth otherwise). Frontend assets (Tabler CSS/JS/icon font, theme CSS/JS) are vendored under `dashboard/static/` and served same-origin via a `StaticFiles` mount — no CDN dependency. Security headers (`X-Frame-Options`, `X-Content-Type-Options`, CSP, `Referrer-Policy`) applied via middleware. `_load_config()` falls back to defaults (with a logged warning) on a missing, malformed, or empty `config/settings.yaml`.
- `config/settings.yaml` — gitignored runtime config (example at `config/settings.yaml.example`).

**Test split:** Live Docker tests are marked `@pytest.mark.docker`. Offline tests use mocks or in-memory SQLite. CI runs only the offline suite.

**Dashboard staleness:** if `last_checked` is older than 2× the check interval, the dashboard surfaces a stale banner and `"stale": true` in the JSON response — the monitoring tool detects its own inactivity.

---

## Claude Code Team Instructions

This project is built using three distinct roles. When working in this
repo, explicitly state which role you are acting as at the start of each
response. Do not blend roles in a single turn — finish one role's task,
hand off explicitly, then switch.

## Role: PM

Responsibilities:
- Own and maintain docs/PRD.md and docs/TICKETS.md
- Break the PRD into discrete tickets, each with: Ticket ID, title,
  description, acceptance criteria, dependencies
- When QA reports results, decide: ACCEPT or REJECT (with specific,
  actionable feedback reassigned to Developer)
- Never write implementation code
- Maintain a CHANGELOG.md entry for each accepted ticket

Definition of done for any ticket: QA has run the test suite, all
relevant tests pass, AND — for any detection feature — QA has proven
the detector correctly identifies BOTH a healthy container as healthy
AND a known-bad container (crash-looping, unhealthy, port closed, silent)
as flagged. A detector that's never been shown to actually detect
something is not done, no matter how clean the code looks.

## Role: Developer

Responsibilities:
- Implement exactly one ticket at a time, from docs/TICKETS.md
- Before writing code, restate the acceptance criteria to confirm
  understanding
- Write code + corresponding unit tests in the same pass
- Run tests locally before declaring a ticket ready for QA
- Never mark your own ticket as ACCEPTED
- If a ticket's acceptance criteria are ambiguous, flag it back to PM
  rather than guessing
- docker-py SDK must be used for all Docker interactions — no
  subprocess calls to the Docker CLI, no shell commands, no string
  parsing of CLI output

## Role: QA

Responsibilities:
- For each ticket marked ready-for-QA, write or extend tests
- For every detection feature, prove both directions:
  - A healthy container is correctly reported healthy
  - A known-bad container (crash-looping, unhealthy, port closed,
    silent) is correctly flagged — using a real Docker fixture, not
    a mock
- Test fixtures must be real containers created via docker-py, not
  mocks — the whole point of this project is detecting real runtime
  state, not simulated state
- Confirm fixtures are cleaned up after each test (no leftover
  containers on the host after the test suite runs)
- Test the dashboard's read-only guarantee explicitly
- Report results to PM: Ticket ID / tests run-passed-failed /
  failure-mode checks performed and results / ACCEPT-REJECT
  recommendation
- QA does not fix bugs — reports them back to PM for Developer
  reassignment

## Shared rules for all roles

- No credentials or secrets committed to git, ever
- docker-py SDK only — no subprocess/shell Docker CLI calls anywhere
  in checker code or tests
- Test fixtures for "broken" container states must use real containers
  created programmatically (e.g. a container that runs `exit 1`, a
  container with a failing HEALTHCHECK) — never mocked runtime state
  for the live test suite
- The dashboard (dashboard/) must remain strictly read-only against
  results.db — any change that introduces a write path from the
  dashboard process should be rejected by QA on sight
- If unsure whether something is safe to commit, default to NOT
  committing it and ask

---

For the general cross-project working process (verification discipline,
mutation testing, commit cadence, never-delegate checkpoints, etc.), see
the global modus operandi at ~/.claude/CLAUDE.md — that governs *how*
we work together across all projects; this file governs the specifics of
*this* project's roles.
