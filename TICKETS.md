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

**Status:** OPEN
**Depends on:** nothing

**Description:**
Establish the repository baseline. No application logic. No credentials.

**Acceptance criteria:**
- [ ] `.gitignore` excludes at minimum: `.idea/`, `.venv/`, `results.db`,
      `__pycache__/`, `*.pyc`, `*.pyo`, `.env`, `config/settings.yaml`
- [ ] `LICENSE` present, MIT, copyright Igal Vexler 2026
- [ ] `README.md` present (full content from PRD package — not a placeholder)
- [ ] Directory skeleton exists: `checker/`, `dashboard/`,
      `dashboard/templates/`, `config/`, `tests/`, `systemd/`, `scripts/`,
      `docs/`
- [ ] `requirements.txt` lists runtime deps with pinned versions:
      `fastapi`, `uvicorn`, `docker`, `PyYAML`
- [ ] `requirements-dev.txt` lists dev deps with pinned versions:
      `pytest`, `pytest-cov`, `httpx`
- [ ] `config/settings.yaml.example` exists with all threshold defaults
      documented and obviously fake/default values only
- [ ] `PRD.md`, `TICKETS.md`, `CLAUDE.md` all present in repo root
- [ ] `git status` after commit shows clean tree — no `.idea/`, `.venv/`,
      `results.db` untracked
- [ ] Verify with `git log --stat`

---

## DS-002 — `severity.py` — per-check and aggregate severity logic

**Status:** OPEN
**Depends on:** DS-001

**Description:**
Two pure functions: one computes severity for a single check result,
one aggregates multiple check severities into a container-level severity.
No I/O, no Docker calls, no external deps.

**Acceptance criteria:**
- [ ] `checker/severity.py` exports:
      - `compute_severity(check_type: str, value: float | str | None) -> str`
        — returns `"healthy"`, `"warning"`, `"critical"`, or `"unknown"`
        based on the check type and value, using configured thresholds
      - `aggregate_severity(severities: list[str]) -> str`
        — returns the worst severity from a list; order:
        `critical > warning > unknown > healthy`
- [ ] Thresholds read from a passed-in config dict (not hardcoded) so
      tests can override them without touching files
- [ ] Boundary tests cover every threshold transition for every check type:
      - RestartCount: 3→warning, 10→critical, exact boundary values
      - Uptime seconds: 60→critical, 300→warning, exact boundaries
      - Healthcheck: "healthy"→healthy, "unhealthy"→critical,
        "starting"→depends on elapsed time, None→unknown
      - Port ms: 2000→warning, refused/timeout→critical
      - Log silence hours: 2→warning, 6→critical
      - No healthcheck: warning regardless of value
- [ ] `aggregate_severity(["healthy", "critical", "warning"])` → `"critical"`
- [ ] `aggregate_severity([])` → `"unknown"`
- [ ] `pytest tests/test_severity.py -v` passes with 0 failures
- [ ] Mutation test performed: flip one boundary, confirm the relevant
      test fails, revert, confirm green again — evidence shown

---

## DS-003 — `docker_checker.py` — container inspection via docker-py

**Status:** OPEN
**Depends on:** DS-002

**Description:**
The core checker module. Uses docker-py SDK to list running containers
and run all four checks per container. No subprocess calls, no CLI.

**Acceptance criteria:**
- [ ] `checker/docker_checker.py` exports:
      - `check_container(container) -> dict` — runs all four checks,
        returns a result dict per check plus an aggregate
      - `check_all(client: docker.DockerClient, config: dict) -> list[dict]`
        — lists all running containers, calls `check_container` on each
- [ ] Per-container result dict contains:
      `container_id`, `name`, `image`, `status`, `checks` (dict of
      per-check results), `severity` (aggregate), `checked_at`
- [ ] Each per-check result contains: `check_type`, `value`, `severity`,
      `detail` (human-readable explanation)
- [ ] **restart_check:** reads `RestartCount` and `StartedAt` from
      `container.attrs` — no CLI call
- [ ] **healthcheck_check:** reads `Health.Status` and `Health.FailingStreak`
      from `container.attrs["State"]` — gracefully handles containers
      with no HEALTHCHECK (returns warning with `detail="no healthcheck defined"`)
- [ ] **port_check:** reads exposed ports from
      `container.attrs["NetworkSettings"]["Ports"]`, TCP-connects to each
      published port on `localhost`, times the connection, skips containers
      with no published ports (returns `"unknown"` with detail explaining why)
- [ ] **log_activity_check:** calls `container.logs(since=N_hours_ago,
      tail=1)` — any bytes returned → activity detected; empty → silence
      flag; uses `docker logs --since` equivalent, not full log retrieval
- [ ] On `docker.errors.APIError` or any Docker exception, the affected
      check returns `severity="unknown"` and `error` field populated —
      never crashes the whole checker run
- [ ] **Live proof required (marked `@pytest.mark.docker`):**
      - A healthy long-running container (e.g. `nginx:alpine`) → all
        checks healthy or unknown (no published ports → port check unknown)
      - A crash-looping container (exits immediately, restart policy
        `on-failure`) → restart_check returns `critical` after N restarts
      - A container with a failing HEALTHCHECK → healthcheck_check
        returns `critical`
      - A container whose exposed port is not actually listening →
        port_check returns `critical`
      - All fixtures created via docker-py, all cleaned up after the test
- [ ] `pytest tests/test_docker_checker.py -v -m "not docker"` passes
      (offline/mocked tests)
- [ ] `pytest tests/test_docker_checker.py -v -m docker` passes with a
      running Docker daemon — Developer runs this and shows full output

---

## DS-004 — `db.py` + `check.py` — orchestration and persistence

**Status:** OPEN
**Depends on:** DS-003

**Description:**
Wire docker_checker into a runnable script. Persist results to SQLite.
Same db.py pattern as Expiry Watcher with schema adapted for
per-container, per-check results.

**Acceptance criteria:**
- [ ] `checker/db.py` exports:
      - `init_db(path: str)` — creates schema if it doesn't exist
      - `write_results(path: str, results: list[dict])` — upserts by
        `container_id`; stores per-check breakdown as JSON in a `checks`
        column
      - `read_results(path: str) -> list[dict]`
      - `get_last_checked(path: str) -> datetime | None`
- [ ] `checker/check.py` is runnable as `python -m checker.check`:
      - connects to Docker via `docker.from_env()`
      - loads `config/settings.yaml`
      - runs `check_all()`
      - writes to `results.db` (path configurable via env var)
      - exits 0 on completion; Docker errors written to db, not raised
- [ ] After a live run, `read_results()` returns one dict per running
      container — verified by actually running it and querying the db
- [ ] `tests/test_db.py` uses in-memory SQLite (`:memory:`) — no I/O
      side effects in tests
- [ ] `pytest tests/test_db.py -v` passes with 0 failures
- [ ] `results.db` does not appear in `git status` after a run

---

## DS-005 — systemd timer + service

**Status:** OPEN
**Depends on:** DS-004

**Description:**
Install and verify the systemd units. 5-minute interval. This ticket is
not done until the timer has actually fired and the service has run.

**Acceptance criteria:**
- [ ] `systemd/docker-sentinel.service` — Type=oneshot, correct
      WorkingDirectory and ExecStart using `.venv/bin/python -m checker.check`
- [ ] `systemd/docker-sentinel.timer` — OnBootSec=2min, OnUnitActiveSec=5min,
      Persistent=true, WantedBy=timers.target
- [ ] `systemd/install.sh` — copies units to `~/.config/systemd/user/`,
      daemon-reload, enable --now
- [ ] All `vault` commands run via `docker exec` — no host CLI required
      (N/A for this project, but note: docker-py connects via socket,
      no host docker CLI install required either)
- [ ] `systemctl --user status docker-sentinel.timer` shows
      `active (waiting)` — shown with actual output
- [ ] `systemctl --user start docker-sentinel.service` triggers a manual
      run that completes successfully and writes to `results.db` —
      verified with `journalctl --user -u docker-sentinel.service` output
- [ ] `journalctl` output contains no credential strings
- [ ] Unit files committed; `results.db` not committed

---

## DS-006 — `dashboard/main.py` — FastAPI read-only dashboard

**Status:** OPEN
**Depends on:** DS-004

**Description:**
Read-only FastAPI dashboard. Same two-process architecture and visual
design as Expiry Watcher. Adds per-check breakdown display — not just
aggregate severity per container, but which specific check failed and why.

**Acceptance criteria:**
- [ ] `dashboard/main.py` is a runnable FastAPI app
- [ ] `GET /status` returns JSON:
      - `containers`: list of per-container dicts, each with `name`,
        `image`, `severity` (aggregate), `checks` (per-check breakdown),
        `checked_at`
      - `last_checked`: ISO-8601 timestamp
      - `stale`: bool (true if last_checked > 2× check interval ago)
- [ ] `GET /` returns HTML:
      - Summary cards (healthy / warning / critical counts)
      - Table with one row per container: name, image, aggregate severity
        badge, expandable per-check detail (or inline sub-rows)
      - "Last checked: X minutes ago" with stale banner if stale
      - Same color scheme and Tabler icons as Expiry Watcher dashboard
- [ ] No code path in `dashboard/` ever writes to `results.db` —
      confirmed by monkeypatching `write_results` to raise and asserting
      no dashboard endpoint triggers it
- [ ] Staleness detection: last_checked older than 2× check interval →
      `stale: true` in JSON and stale banner in HTML
- [ ] `docker-compose.yml` mounts `results.db` as `:ro` — read-only
      enforced at container level, not just application code
- [ ] Cross-process proof: run `python -m checker.check` on host, then
      start dashboard container, confirm `GET /status` `last_checked`
      timestamp matches the host checker run exactly
- [ ] `pytest tests/test_dashboard.py -v` passes 0 failures
- [ ] `docker compose up dashboard -d` → `curl http://localhost:8080/status`
      returns 200 with correct JSON — shown with actual output

---

## DS-007 — CI pipeline (GitHub Actions)

**Status:** OPEN
**Depends on:** DS-006

**Description:**
GitHub Actions workflow. Docker-dependent tests skipped in CI (no Docker
socket on standard runners without special setup). Everything else must
pass.

**Acceptance criteria:**
- [ ] `.github/workflows/ci.yml` runs on push and PR to `master`
- [ ] CI steps: checkout → Python 3.12 → install deps → pytest
      `-m "not docker" -v`
- [ ] CI passes on a clean push — verified by reading the actual Actions
      run log, not just "it went green"
- [ ] No credentials appear in the workflow file or CI logs
- [ ] CI badge added to README.md
- [ ] Proactively check for runner-specific issues before pushing
      (lesson from Vault Secrets Demo's bind-mount CI failure)

---

## DS-008 — README finalization + pre-publish audit

**Status:** OPEN
**Depends on:** DS-007

**Description:**
README is already substantially complete from DS-001 (full content from
PRD package). This ticket is a verification and finalization pass, plus
the pre-publish security audit — which belongs to the user alone.

**Acceptance criteria (Developer):**
- [ ] README accurately reflects the final implementation — no
      placeholder text, no TODO lines, no steps that don't work as written
- [ ] Fresh-clone smoke test performed: clone into a fresh directory,
      follow the README exactly, confirm each step works — directory left
      intact for user to verify (lesson from Expiry Watcher)
- [ ] Platform support table matches actual test results from CI and
      any manual macOS/Windows testing done

**Acceptance criteria (User — not delegatable):**
- [ ] `git log --all --full-history -- '*.yaml' '*.env' '*.json'`
      — confirm no credential file was ever committed
- [ ] `git log -p | grep -iE 'password|secret|token'`
      — scan full patch history for accidental credential strings
- [ ] Clean-clone smoke test from a fresh directory: clone, follow
      README, confirm checker runs and dashboard serves
- [ ] Confirm `results.db` is not present in the published repo
- [ ] Confirm CI badge is green on master

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
| DS-001 | Repo scaffolding | OPEN |
| DS-002 | severity.py | OPEN |
| DS-003 | docker_checker.py | OPEN |
| DS-004 | db.py + check.py | OPEN |
| DS-005 | systemd timer | OPEN |
| DS-006 | dashboard | OPEN |
| DS-007 | CI pipeline | OPEN |
| DS-008 | README + audit | OPEN |
| DS-stretch-01 | Resource monitoring | DEFERRED |
| DS-stretch-02 | Log error detection | DEFERRED |
| DS-stretch-03 | Multi-host | DEFERRED |
| DS-stretch-04 | Push notifications | DEFERRED |
