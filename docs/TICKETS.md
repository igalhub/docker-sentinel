# TICKETS — docker-sentinel

Thresholds (confirmed defaults, all configurable via config/settings.yaml):
- RestartCount: warning > 3, critical > 10
- Uptime: warning < 5 minutes, critical < 60 seconds
- Healthcheck stuck in starting: warning after 5 minutes
- Healthcheck unhealthy: critical immediately
- Port response time: warning > 2 seconds, critical = refused/timeout
- Log silence: warning > 2 hours, critical > 6 hours
- No HEALTHCHECK defined: warning (best-practice gap)

Scheduler: systemd timer, 5-minute interval.
Docker interactions: docker-py SDK only — no subprocess/CLI calls.
Live test fixtures: real containers created via docker-py, cleaned up after each test.

---

## DS-001 — Repo scaffolding, .gitignore, LICENSE, directory skeleton

**Status:** DONE
**Depends on:** nothing

**Description:**
Establish the repository baseline. No application logic. No credentials.

**Acceptance criteria:**
- [x] `.gitignore` excludes at minimum: `.idea/`, `.venv/`, `results.db`,
      `__pycache__/`, `*.pyc`, `*.pyo`, `.env`, `config/settings.yaml`
- [x] `LICENSE` present, MIT, copyright Igal Vexler 2026
- [x] `README.md` present (full content from PRD package — not a placeholder)
- [x] Directory skeleton exists: `checker/`, `dashboard/`,
      `dashboard/templates/`, `config/`, `tests/`, `systemd/`, `scripts/`,
      `docs/`
- [x] `requirements.txt` lists runtime deps with pinned versions:
      `fastapi`, `uvicorn`, `docker`, `PyYAML`
- [x] `requirements-dev.txt` lists dev deps with pinned versions:
      `pytest`, `pytest-cov`, `httpx`
- [x] `config/settings.yaml.example` exists with all threshold defaults
      documented and obviously fake/default values only
- [x] `PRD.md`, `TICKETS.md`, `CLAUDE.md` all present in repo root
      (`PRD.md`/`TICKETS.md` later reorganized into `docs/`; `CLAUDE.md`
      and `README.md` remain at repo root)
- [x] `git status` after commit shows clean tree — no `.idea/`, `.venv/`,
      `results.db` untracked
- [x] Verify with `git log --stat`

---

## DS-002 — `severity.py` — per-check and aggregate severity logic

**Status:** DONE
**Depends on:** DS-001

**Description:**
Two pure functions: one computes severity for a single check result,
one aggregates multiple check severities into a container-level severity.
No I/O, no Docker calls, no external deps.

**Acceptance criteria:**
- [x] `checker/severity.py` exports:
      - `compute_severity(check_type: str, value: float | str | None) -> str`
        — returns `"healthy"`, `"warning"`, `"critical"`, or `"unknown"`
        based on the check type and value, using configured thresholds
      - `aggregate_severity(severities: list[str]) -> str`
        — returns the worst severity from a list; order:
        `critical > warning > unknown > healthy`
- [x] Thresholds read from a passed-in config dict (not hardcoded) so
      tests can override them without touching files
- [x] Boundary tests cover every threshold transition for every check type:
      - RestartCount: 3→warning, 10→critical, exact boundary values
      - Uptime seconds: 60→critical, 300→warning, exact boundaries
      - Healthcheck: "healthy"→healthy, "unhealthy"→critical,
        "starting"→depends on elapsed time, None→unknown
      - Port ms: 2000→warning, refused/timeout→critical
      - Log silence hours: 2→warning, 6→critical
      - No healthcheck: warning regardless of value
- [x] `aggregate_severity(["healthy", "critical", "warning"])` → `"critical"`
- [x] `aggregate_severity([])` → `"unknown"`
- [x] `pytest tests/test_severity.py -v` passes with 0 failures
- [x] Mutation test performed: flip one boundary, confirm the relevant
      test fails, revert, confirm green again — evidence shown

---

## DS-003 — `docker_checker.py` — container inspection via docker-py

**Status:** DONE
**Depends on:** DS-002

**Description:**
The core checker module. Uses docker-py SDK to list running containers
and run all four checks per container. No subprocess calls, no CLI.

**Acceptance criteria:**
- [x] `checker/docker_checker.py` exports:
      - `check_container(container) -> dict` — runs all four checks,
        returns a result dict per check plus an aggregate
      - `check_all(client: docker.DockerClient, config: dict) -> list[dict]`
        — lists all running containers, calls `check_container` on each
- [x] Per-container result dict contains:
      `container_id`, `name`, `image`, `status`, `checks` (dict of
      per-check results), `severity` (aggregate), `checked_at`
- [x] Each per-check result contains: `check_type`, `value`, `severity`,
      `detail` (human-readable explanation)
- [x] **restart_check:** reads `RestartCount` and `StartedAt` from
      `container.attrs` — no CLI call
- [x] **healthcheck_check:** reads `Health.Status` and `Health.FailingStreak`
      from `container.attrs["State"]` — gracefully handles containers
      with no HEALTHCHECK (returns warning with `detail="no healthcheck defined"`)
- [x] **port_check:** reads exposed ports from
      `container.attrs["NetworkSettings"]["Ports"]`, TCP-connects to each
      published port on `localhost`, times the connection, skips containers
      with no published ports (returns `"unknown"` with detail explaining why)
- [x] **log_activity_check:** calls `container.logs(since=N_hours_ago,
      tail=1)` — any bytes returned → activity detected; empty → silence
      flag; uses `docker logs --since` equivalent, not full log retrieval
- [x] On `docker.errors.APIError` or any Docker exception, the affected
      check returns `severity="unknown"` and `error` field populated —
      never crashes the whole checker run
- [x] **Live proof required (marked `@pytest.mark.docker`):**
      - A healthy long-running container (e.g. `nginx:alpine`) → all
        checks healthy or unknown (no published ports → port check unknown)
      - A crash-looping container (exits immediately, restart policy
        `on-failure`) → restart_check returns `critical` after N restarts
      - A container with a failing HEALTHCHECK → healthcheck_check
        returns `critical`
      - A container whose exposed port is not actually listening →
        port_check returns `critical`
      - All fixtures created via docker-py, all cleaned up after the test
- [x] `pytest tests/test_docker_checker.py -v -m "not docker"` passes
      (offline/mocked tests)
- [x] `pytest tests/test_docker_checker.py -v -m docker` passes with a
      running Docker daemon — Developer runs this and shows full output

---

## DS-004 — `db.py` + `check.py` — orchestration and persistence

**Status:** DONE
**Depends on:** DS-003

**Description:**
Wire docker_checker into a runnable script. Persist results to SQLite.
Same db.py pattern as Expiry Watcher with schema adapted for
per-container, per-check results.

**Acceptance criteria:**
- [x] `checker/db.py` exports:
      - `init_db(path: str)` — creates schema if it doesn't exist
      - `write_results(path: str, results: list[dict])` — upserts by
        container name; stores per-check breakdown as JSON in a `checks`
        column
      - `read_results(path: str) -> list[dict]`
      - `get_last_checked(path: str) -> datetime | None`
- [x] `checker/check.py` is runnable as `python -m checker.check`:
      - connects to Docker via `docker.from_env()`
      - loads `config/settings.yaml`
      - runs `check_all()`
      - writes to `results.db` (path configurable via env var)
      - exits 0 on completion; Docker errors written to db, not raised
- [x] After a live run, `read_results()` returns one dict per running
      container — verified by actually running it and querying the db
- [x] `tests/test_db.py` uses in-memory SQLite (`:memory:`) — no I/O
      side effects in tests
- [x] `pytest tests/test_db.py -v` passes with 0 failures
- [x] `results.db` does not appear in `git status` after a run

---

## DS-005 — systemd timer + service

**Status:** DONE
**Depends on:** DS-004

**Description:**
Install and verify the systemd units. 5-minute interval. This ticket is
not done until the timer has actually fired and the service has run.

**Acceptance criteria:**
- [x] `systemd/docker-sentinel.service` — Type=oneshot, correct
      WorkingDirectory and ExecStart using `.venv/bin/python -m checker.check`
- [x] `systemd/docker-sentinel.timer` — OnBootSec=2min, OnUnitActiveSec=5min,
      Persistent=true, WantedBy=timers.target
- [x] `systemd/install.sh` — copies units to `~/.config/systemd/user/`,
      daemon-reload, enable --now
- [x] All `vault` commands run via `docker exec` — no host CLI required
      (N/A for this project, but note: docker-py connects via socket,
      no host docker CLI install required either)
- [x] `systemctl --user status docker-sentinel.timer` shows
      `active (waiting)` — shown with actual output
- [x] `systemctl --user start docker-sentinel.service` triggers a manual
      run that completes successfully and writes to `results.db` —
      verified with `journalctl --user -u docker-sentinel.service` output
- [x] `journalctl` output contains no credential strings
- [x] Unit files committed; `results.db` not committed

---

## DS-006 — `dashboard/main.py` — FastAPI read-only dashboard

**Status:** DONE
**Depends on:** DS-004

**Description:**
Read-only FastAPI dashboard. Same two-process architecture and visual
design as Expiry Watcher. Adds per-check breakdown display — not just
aggregate severity per container, but which specific check failed and why.

**Acceptance criteria:**
- [x] `dashboard/main.py` is a runnable FastAPI app
- [x] `GET /status` returns JSON:
      - `containers`: list of per-container dicts, each with `name`,
        `image`, `severity` (aggregate), `checks` (per-check breakdown),
        `checked_at`
      - `last_checked`: ISO-8601 timestamp
      - `stale`: bool (true if last_checked > 2× check interval ago)
- [x] `GET /` returns HTML:
      - Summary cards (healthy / warning / critical counts)
      - Table with one row per container: name, image, aggregate severity
        badge, expandable per-check detail (or inline sub-rows)
      - "Last checked: X minutes ago" with stale banner if stale
      - Same color scheme and Tabler icons as Expiry Watcher dashboard
- [x] No code path in `dashboard/` ever writes to `results.db` —
      confirmed by monkeypatching `write_results` to raise and asserting
      no dashboard endpoint triggers it
- [x] Staleness detection: last_checked older than 2× check interval →
      `stale: true` in JSON and stale banner in HTML
- [x] `docker-compose.yml` mounts `results.db` as `:ro` — read-only
      enforced at container level, not just application code
- [x] Cross-process proof: run `python -m checker.check` on host, then
      start dashboard container, confirm `GET /status` `last_checked`
      timestamp matches the host checker run exactly
- [x] `pytest tests/test_dashboard.py -v` passes 0 failures
- [x] `docker compose up dashboard -d` → `curl http://localhost:8081/status`
      returns 200 with correct JSON — shown with actual output

---

## DS-007 — CI pipeline (GitHub Actions)

**Status:** DONE
**Depends on:** DS-006

**Description:**
GitHub Actions workflow. Docker-dependent tests skipped in CI (no Docker
socket on standard runners without special setup). Everything else must
pass.

**Acceptance criteria:**
- [x] `.github/workflows/ci.yml` runs on push and PR to `master`
- [x] CI steps: checkout → Python 3.12 → install deps → pytest
      `-m "not docker" -v`
- [x] CI passes on a clean push — verified by reading the actual Actions
      run log, not just "it went green"
- [x] No credentials appear in the workflow file or CI logs
- [x] CI badge added to README.md
- [x] Proactively check for runner-specific issues before pushing
      (lesson from Vault Secrets Demo's bind-mount CI failure)

---

## DS-008 — README finalization + pre-publish audit

**Status:** DONE
**Depends on:** DS-007

**Description:**
README is already substantially complete from DS-001 (full content from
PRD package). This ticket is a verification and finalization pass, plus
the pre-publish security audit — which belongs to the user alone.

**Acceptance criteria (Developer):**
- [x] README accurately reflects the final implementation — no
      placeholder text, no TODO lines, no steps that don't work as written
- [x] Fresh-clone smoke test performed: clone into a fresh directory,
      follow the README exactly, confirm each step works — directory left
      intact for user to verify (lesson from Expiry Watcher)
- [x] Platform support table matches actual test results from CI and
      any manual macOS/Windows testing done

**Acceptance criteria (User — not delegatable):**
- [x] `git log --all --full-history -- '*.yaml' '*.env' '*.json'`
      — confirm no credential file was ever committed
- [x] `git log -p | grep -iE 'password|secret|token'`
      — scan full patch history for accidental credential strings
- [x] Clean-clone smoke test from a fresh directory: clone, follow
      README, confirm checker runs and dashboard serves
- [x] Confirm `results.db` is not present in the published repo
- [x] Confirm CI badge is green on master

---

## DS-009 — Home lab deployment documentation

**Goal:** Document deployment on a Proxmox home lab environment and
multi-project coexistence.

**Deliverables:**
- `docs/HOMELAB_DEPLOYMENT.md` — full deployment walkthrough for
  Proxmox VE + Ubuntu Server VM environment
- README platform support section updated with home lab notes
- Documented: docker-sentinel auto-discovers all running containers
  including other portfolio projects
- Documented: port 8081 used to avoid conflict with expiry-watcher

**Tested on:**
- Proxmox VE 9.2.3, Beelink SER mini PC
- Ubuntu Server 24.04.3 LTS VM
- Docker 29.6.0, Python 3.12

**Dependencies:** DS-008

**Status: DONE**

---

## DS-010 — Security hardening pass (dashboard auth, headers, logging, image pinning)

**Status:** DONE
**Depends on:** DS-006

**Description:**
Backfilled ticket for a security-focused code review pass (external
review via Claude Desktop, implemented and merged via PR #2 without a
ticket at the time). Addresses dashboard exposure, error visibility,
and supply-chain reproducibility.

**Acceptance criteria:**
- [x] Dashboard supports optional HTTP Basic Auth via
      `SENTINEL_DASHBOARD_USER`/`SENTINEL_DASHBOARD_PASSWORD`; refuses
      to start if only one is set; logs a startup warning if both are
      left unset
- [x] Security headers (`X-Frame-Options`, `X-Content-Type-Options`,
      `Content-Security-Policy`, `Referrer-Policy`) applied via
      middleware to all dashboard responses
- [x] README documents the auth env vars and recommends a reverse
      proxy/VPN instead of direct exposure; warns that `docker`-group
      membership is root-equivalent on the host
- [x] `dashboard/Dockerfile` pins `python:3.12-slim` to a resolved
      digest instead of a floating tag
- [x] Every `except Exception` block in `checker/docker_checker.py`
      logs a `logger.warning` before returning `severity="unknown"`
- [x] Tests added: auth accepted/rejected/missing, mismatched env vars
      refuse to start, security headers present, warning logged on each
      check's error path
- [x] `pytest -m "not docker" -v` passes with 0 failures (114 passed)

**Merged:** PR #2, commit `3f7dbc8`

---

## DS-011 — Fix FastAPI docs/redoc/openapi auth bypass

**Status:** DONE
**Depends on:** DS-010

**Description:**
QA's live-server security pass (2026-07-06) found that FastAPI's
auto-registered `/docs`, `/redoc`, `/openapi.json` routes bypass
`require_auth` because they're wired up in the `FastAPI()` constructor,
before any route-level dependency exists — they returned 200
unauthenticated even with Basic Auth configured. Fixed by disabling
those routes outright when `SENTINEL_DASHBOARD_USER` is set.

**Acceptance criteria:**
- [x] `/docs`, `/redoc`, `/openapi.json` return 404 when
      `SENTINEL_DASHBOARD_USER`/`PASSWORD` are set
- [x] Same routes return 200 when auth is disabled (no regression)
- [x] Regression test added, mutation-tested (reverting the fix flips
      the test from pass to fail)
- [x] `pytest -m "not docker" -v` passes with 0 failures (116 passed)

**Merged:** PR #7, commit `c7390d1`

---

## DS-012 — Validate config/settings.yaml shape at load time

**Status:** DEFERRED
**Depends on:** none

**Description:**
`dashboard/main.py`'s `_load_config()` currently special-cases individual
malformed-config shapes one at a time (`FileNotFoundError`, `yaml.YAMLError`,
`None` from an empty file) — found during a code review pass and fixed
piecemeal. A top-level YAML scalar or list (e.g. a config file containing
just `disabled` with no mapping structure) is valid YAML, parses without
raising, and isn't `None`, so it still crashes `_is_stale()` with a
`TypeError` the same way the already-fixed cases did. Continuing to patch
`_load_config` case by case doesn't converge — the right fix is validating
the full expected shape once at load time (required top-level keys, or a
lightweight Pydantic model for `config/settings.yaml`), not another
isinstance check.

**Acceptance criteria:**
- [ ] `_load_config()` validates the loaded config against the expected
      shape (required keys: `checker.interval_seconds`,
      `dashboard.stale_multiplier`) once, rather than checking for
      individual malformed shapes ad hoc
- [ ] Any invalid shape (wrong type, missing required key) falls back to
      `_DEFAULT_CONFIG` with a single logged warning, same as the existing
      `FileNotFoundError`/`YAMLError`/`None` cases
- [ ] Tests cover: non-dict top-level YAML (scalar, list), missing required
      keys, wrong value types
- [ ] `pytest -m "not docker" -v` passes with 0 failures

---

## DS-013 — Add lint + coverage gate to CI

**Status:** DONE
**Depends on:** none

**Description:**
`.github/workflows/ci.yml` runs the offline test suite only — no lint
step, no coverage gate. Flagged as optional/low-priority in the same
full code review pass that produced DS-011/DS-012.

Scope is open-ended until someone actually runs `ruff check .` and
`pytest --cov=checker --cov=dashboard` once to see current state — how
many lint violations exist today (and whether they're real issues or
noise from generated/vendored code like `dashboard/static/vendor/`)
and what today's actual coverage percentage is. Don't guess at a lint
ruleset or a coverage threshold before that discovery step; picking
either upfront risks a threshold that's either trivially passing
(useless) or immediately failing on unrelated pre-existing gaps
(blocks unrelated PRs).

Discovery found exactly 1 lint violation (`conftest.py`'s unused `pytest`
import, auto-fixed) and 87% coverage, with `checker/check.py` — the
`python -m checker.check` entry point — at 0% (the sole reason the
overall number was dragged down; every other file was already 94-100%).
Decided to add a real thin test for `checker/check.py` (mocking
`docker.from_env`/`check_all`/`init_db`/`write_results`, matching the
mocking pattern already used elsewhere in this suite) rather than
excluding it from the gate — it's a small, cleanly-mockable orchestration
function, not a genuinely untestable script, and it's the actual
production entry point the systemd timer runs every 5 minutes. Final
measured coverage after that test: 96.39%.

**Acceptance criteria:**
- [x] Discovery: run `ruff check .` and `pytest --cov=checker
      --cov=dashboard` locally once; record current violation count
      and coverage percentage
- [x] Based on discovery, decide: fix existing lint violations before
      adding the CI gate, or configure `ruff` to ignore/exclude what's
      pre-existing (e.g. vendored assets) and gate only new violations
- [x] Add a `ruff check .` step to `.github/workflows/ci.yml`
- [x] Add a `pytest --cov=checker --cov=dashboard
      --cov-fail-under=<threshold>` step, with `<threshold>` set from
      the discovery step's actual measured coverage (not guessed)
- [x] CI passes green with both new steps

---

## DS-014 — Live-DB integration test for dashboard read path

**Status:** DONE
**Depends on:** none

**Description:**
Every existing dashboard test (`tests/test_dashboard.py`) mocks
`read_results`/`get_last_checked` outright — the real SQLite read path
between `checker/db.py`'s `init_db`/`write_results` and `dashboard/main.py`'s
`read_results`/`get_last_checked` has never been exercised end-to-end by
the test suite. This ticket adds that missing coverage using a real
SQLite file, not `:memory:` and not mocks.

Test-only change. No edits to `checker/` or `dashboard/main.py` or any
other production code path — docker-sentinel is live in production. If
implementing this test surfaces a real bug in the read path, stop and
report it rather than fixing it inline; that would be a scope change
requiring its own ticket.

**Acceptance criteria:**
- [x] New test file `tests/test_dashboard_live_db.py` seeds a real SQLite
      file via `checker.db.init_db` + `checker.db.write_results` (not
      `:memory:`, not mocks) with rows covering: one healthy container,
      one critical container, and one row old enough to trip the
      dashboard's staleness threshold
- [x] `dashboard/main.py` is pointed at that real file via its existing
      construction-time env var (`SENTINEL_DB_PATH`) with a module
      reload, matching the pattern already used in `test_dashboard.py`
      for construction-time env vars
- [x] `GET /status` (JSON) reflects the seeded severities, container
      names, and the correct `stale` boolean
- [x] `GET /` (HTML) reflects the same — severity badges and the
      staleness banner render correctly against the real seeded data
- [x] No changes to any file outside `tests/`
- [x] `pytest -m "not docker" -v` passes with 0 failures, including
      every pre-existing test (no regressions)
- [x] QA report explicitly confirms this exercises the real read path
      end-to-end, not a re-skin of the existing mocked tests

---

## DS-015 — Commit results.db seed-reset.sh and verify-data.sh adapter scripts

**Status:** DONE
**Depends on:** none

**Description:**
`.claude/seed-reset.sh` and `.claude/verify-data.sh` were written and
proven working during a deliberate exercise of `/seed-reset` and
`/verify-data` — neither had ever been run against a real docker-sentinel
database before. `seed-reset.sh` seeds/resets a target SQLite file via
`checker.db`'s real `init_db`/`write_results` upsert pattern.
`verify-data.sh` checks severity-value validity and freshness against the
dashboard's staleness logic. Both scripts are safety-guarded to refuse to
run against the real `results.db` path — this project's actual database
is written every 5 minutes by a live systemd timer, and neither script
may ever touch it, including after this ticket's edits.

Two design decisions made explicitly as part of this ticket, not left
ambiguous:

1. **Staleness threshold:** `verify-data.sh` reads `interval_seconds` /
   `stale_multiplier` from `config/settings.yaml`, the same way
   `dashboard/main.py`'s `_load_config` does, with the same
   fallback-to-defaults behavior and a logged warning on fallback. A
   hardcoded value meant to mirror the dashboard's real config is exactly
   the silent-drift risk DS-012 exists to prevent in a different file —
   not reintroducing it here.
2. **Baseline documentation:** the known-good seed baseline (container
   names, severities, what each row is meant to exercise) is documented
   via a comment block at the top of `seed-reset.sh` — no separate docs
   file, appropriate for a project this size.

**Acceptance criteria:**
- [x] `.claude/seed-reset.sh` and `.claude/verify-data.sh` committed, both
      retaining the hard rejection of the real `results.db` path — QA
      re-confirms this guard still works after any edits by explicitly
      passing the real path and confirming refusal
- [x] `verify-data.sh`'s staleness threshold reads from
      `config/settings.yaml` (`checker.interval_seconds` *
      `dashboard.stale_multiplier`), tested against both the default
      config and a deliberately different `stale_multiplier` to confirm
      it is not hardcoded
- [x] Baseline seed rows documented via a comment block in
      `seed-reset.sh`, per decision above
- [x] QA re-proves both detection directions after any changes: healthy
      baseline → both checks pass; injected bad severity value →
      flagged; injected all-stale state → flagged; exit codes correct in
      all cases
- [x] No changes to `checker/`, `dashboard/`, or any existing test — this
      is `.claude/` tooling only
- [x] `pytest -m "not docker" -v` passes with 0 failures (no regressions)

---

## DS-016 — Deterministic tests for dashboard's relative-timestamp branches

**Status:** DONE
**Depends on:** DS-013

**Description:**
DS-013's hosted CI run passed the coverage gate at 96.07%, only 0.07
points above the 96% threshold — thinner margin than intended. Traced to
`dashboard/main.py` lines 129 and 133 (the `"just now"` and
`"X hour(s) ago"` branches of the relative-timestamp formatter):
confirmed via 5 stable local coverage runs that these two lines are
**permanently** uncovered by the existing test suite, not intermittently
flaky. `RECENT`/`RECENT_TS` in `tests/test_dashboard.py` and
`tests/test_dashboard_live_db.py` are computed once at collection time as
`now - 60s`; by request time, real elapsed time always pushes `age`
slightly past 60s, so `"just now"` (`age < 60`) can never fire. Neither
file has a fixture near the 3600s boundary either, so `"X hour(s) ago"`
is never exercised.

Residual, deliberately unresolved: the hosted run's exact discrepancy
(96.07% vs. a local 96.39% on the same commit — 3 vs. 2 lines missed in
`dashboard/main.py`) was investigated (checked for randomization plugins,
other environment-sensitive lines) but the exact third missing line
could not be identified, since the CI workflow doesn't capture
`--cov-report=term-missing` detail. Not chased further — noted here for
the record, not treated as blocking.

**Acceptance criteria:**
- [x] Two new deterministic tests added (`tests/test_dashboard.py`, since
      that's where the existing relative-timestamp assertions live):
      one freezing "now" via `patch("dashboard.main.datetime")` with
      `last_checked` 30s before frozen-now (asserts `"just now"`), one
      with `last_checked` 2 hours before frozen-now (asserts
      `"X hour(s) ago"`) — confirmed safe since `dashboard/main.py` only
      ever calls `datetime.now(timezone.utc)`, nothing else from the
      `datetime` class
- [x] Coverage re-measured for real after the new tests land (not
      guessed); `--cov-fail-under` in `.github/workflows/ci.yml` updated
      to reflect the actual new measured percentage (97%, actual 97.05%),
      not left at 96 with stale slack
- [x] Cross-file state-leakage check re-run (`pytest -k "test_check or
      test_dashboard or test_dashboard_live_db"`, both forward and
      reverse file order) since this touches `tests/test_dashboard.py`
      again
- [x] `pytest -m "not docker" -v` passes with 0 failures (no regressions)
- [x] No changes to `dashboard/main.py` or any other production code —
      test-only

---

## DS-017 — Close `_load_config` success-path gap; fix coverage.py rounding footgun

**Status:** DONE
**Depends on:** DS-016

**Description:**
Two real, previously-hidden bugs surfaced while verifying DS-016's hosted
CI run, both invisible all session because local coverage measurements
were taken from a contaminated dev environment (a real, gitignored
`config/settings.yaml` sitting in the dev venv that a fresh CI checkout
never has):

1. **`dashboard/main.py` line 93 (`return config`, `_load_config`'s
   success path) has never been covered by any test, in any CI run,
   ever.** No test explicitly points `SENTINEL_CONFIG_PATH` at a valid
   config file — every existing test either doesn't override it (falling
   through to the nonexistent default path in a fresh checkout) or
   deliberately points it at a malformed/empty file to test the fallback
   paths. This predates DS-013 entirely; DS-016 didn't cause or miss it,
   it was structurally impossible to see locally.
2. **`coverage.py`'s `--cov-fail-under` compares against the *rounded*
   percentage, not the raw one** (`coverage/results.py`:
   `round(total, precision) < fail_under`, default `precision=0`).
   DS-016's chosen threshold (97, matching the real fresh-environment
   coverage of 96.72% which *rounds* to 97) silently passed
   (`round(96.72, 0) == 97`, `97 < 97` is `False`) while `pytest-cov`'s
   own message-printing logic separately printed
   `FAIL Required test coverage of 97% not reached` using the raw value —
   a real, reproducible false-pass, confirmed via a fresh clone/venv and
   a 3-point threshold test matrix (98 correctly fails, 97 silently
   passes, 96 correctly passes). The gate mechanism itself isn't broken
   — a threshold with real margin, or fewer rounding-boundary
   coincidences, fixes it.

**Acceptance criteria:**
- [x] New test added exercising `_load_config`'s success path: writes a
      valid config file to a tmp path, points `SENTINEL_CONFIG_PATH` at
      it, reloads `dashboard.main`, asserts `_load_config()` returns the
      real parsed content (not `_DEFAULT_CONFIG`)
- [x] Coverage re-measured from a genuinely fresh `git clone` into a
      genuinely fresh venv — not the contaminated dev environment used
      all session — confirmed no `config/settings.yaml` or other
      gitignored state present before measuring. Honest baseline:
      **97.05%** (305 stmts, 9 missed), `dashboard/main.py` 100%.
      Independently reproduced by QA in a separate fresh clone.
- [x] `--cov-fail-under` in `.github/workflows/ci.yml` set to 96 — real
      margin clear of any rounding boundary given the 97.05% baseline
- [x] `.coveragerc`'s `[report] precision` set to 2, with a comment at
      the threshold explaining `round(X, 0)` can equal an integer
      threshold and make `<` false even when the true value is below
      intent
- [x] Gate proven for real: `--cov-fail-under=100` pushed to scratch
      branch `scratch/ds-017-prove-gate-fails`, PR #18 opened (never
      merged) to trigger hosted CI, run watched to completion,
      `conclusion:"failure"` confirmed via `gh run view --json
      conclusion`. PR closed, branch deleted both locally and on origin.
- [x] `pytest -m "not docker" -v` passes with 0 failures (no regressions)

---

## DS-stretch-01 — Resource monitoring (CPU/memory trends)

**Status:** DEFERRED
**Depends on:** DS-004 (schema already supports history)

Resource trend detection requires multiple data points over time.
The SQLite schema stores all check history from day one. Implement when
the value of trend detection outweighs the added checker complexity.

---

## DS-stretch-02 — Log error detection

**Status:** DEFERRED

Per-container log parser configuration needed to reliably detect errors
across arbitrary log formats. Implement when there's a clear use case
that justifies the configuration complexity.

---

## DS-stretch-03 — Multi-host / Swarm / Kubernetes

**Status:** DEFERRED

Monitors the local Docker daemon only. Kubernetes has its own health
check ecosystem that would need a dedicated integration.

---

## DS-stretch-04 — Push notifications

**Status:** DEFERRED

Dashboard-only in v1. The `/status` JSON endpoint is machine-readable
and can be polled by any external alerting system in the meantime.

---

## Ticket status

| Ticket | Title | Status |
|---|---|---|
| DS-001 | Repo scaffolding | DONE |
| DS-002 | severity.py | DONE |
| DS-003 | docker_checker.py | DONE |
| DS-004 | db.py + check.py | DONE |
| DS-005 | systemd timer | DONE |
| DS-006 | dashboard | DONE |
| DS-007 | CI pipeline | DONE |
| DS-008 | README + audit | DONE |
| DS-009 | Home lab deployment documentation | DONE |
| DS-010 | Security hardening pass | DONE |
| DS-011 | Fix FastAPI docs/redoc/openapi auth bypass | DONE |
| DS-012 | Validate config/settings.yaml shape at load time | DEFERRED |
| DS-013 | Add lint + coverage gate to CI | DONE |
| DS-014 | Live-DB integration test for dashboard read path | DONE |
| DS-015 | Commit results.db seed-reset.sh and verify-data.sh adapter scripts | DONE |
| DS-016 | Deterministic tests for dashboard's relative-timestamp branches | DONE |
| DS-017 | Close _load_config success-path gap; fix coverage.py rounding footgun | DONE |
| DS-stretch-01 | Resource monitoring | DEFERRED |
| DS-stretch-02 | Log error detection | DEFERRED |
| DS-stretch-03 | Multi-host | DEFERRED |
| DS-stretch-04 | Push notifications | DEFERRED |
