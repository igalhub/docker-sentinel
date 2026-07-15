# CHANGELOG — docker-sentinel

## DS-016 — Deterministic tests for dashboard's relative-timestamp branches
DS-013's hosted CI run passed the coverage gate at 96.07%, only 0.07
points above threshold. Traced to `dashboard/main.py`'s `"just now"`/
`"X hour(s) ago"` relative-timestamp branches being permanently
uncovered (not intermittently flaky — confirmed via repeated stable
local runs) since no existing fixture's `last_checked` timestamp lands
near either boundary. Added two deterministic tests
(`tests/test_dashboard.py`) freezing "now" via
`patch("dashboard.main.datetime")`. `dashboard/main.py` is now 100%
covered; `--cov-fail-under` raised `96 → 97` to match the real
re-measured total (97.05%). The hosted-run's exact 96.07% vs. local
96.39% discrepancy (3 vs. 2 missed lines) was investigated but the
precise third line was never identified — noted as unresolved, not
blocking.

## DS-013 — Add lint + coverage gate to CI
`.github/workflows/ci.yml` now runs `ruff check .` and
`pytest --cov=checker --cov=dashboard --cov-fail-under=96`. Discovery
found 1 lint violation (unused import, fixed) and 87% coverage dragged
down entirely by `checker/check.py` at 0%; added `tests/test_check.py`
(mocking `docker.from_env`/`check_all`/`init_db`/`write_results`) to
close that gap rather than excluding the entry point from the gate.
Final measured coverage: 96.39%.

## DS-015 — Commit results.db seed-reset.sh and verify-data.sh adapter scripts
`.claude/seed-reset.sh` and `.claude/verify-data.sh` seed/verify a target
SQLite file via `checker.db`'s real `init_db`/`write_results`/
`read_results`/`get_last_checked`. Both refuse to run against the real
`results.db` path (this project's actual DB is written every 5 minutes by
a live systemd timer). `verify-data.sh`'s staleness threshold reads from
`config/settings.yaml` the same way `dashboard/main.py` does, rather than
hardcoding a value that could silently drift from the dashboard's real
config.

## DS-014 — Live-DB integration test for dashboard read path
`tests/test_dashboard_live_db.py` seeds a real SQLite file via
`checker.db`'s real `init_db`/`write_results` and reads it back through
`dashboard/main.py`'s real `read_results`/`get_last_checked` — no mocks.
Covers healthy/critical severities and staleness detection in both
`GET /status` and `GET /`. Closes the gap where every prior dashboard
test mocked the DB read path outright.

## DS-011 — Fix FastAPI docs/redoc/openapi auth bypass
FastAPI's auto-registered `/docs`, `/redoc`, and `/openapi.json` routes
were wired up in the `FastAPI()` constructor itself, before any
route-level `Depends(require_auth)` existed, so they returned 200
unauthenticated even with Basic Auth configured. Now disabled outright
when `SENTINEL_DASHBOARD_USER` is set. Found via a live-server QA
security pass; regression test mutation-tested.

## Unticketed maintenance

- **CI: bump `actions/checkout` to v5 and `actions/setup-python` to v6** —
  removes the Node.js 20 deprecation warning on GitHub-hosted runners;
  both actions now ship Node-24-native runners instead of being
  force-shimmed onto a newer Node version at runtime.

## DS-010 — Security hardening pass
Optional HTTP Basic Auth for the dashboard (`SENTINEL_DASHBOARD_USER`/
`SENTINEL_DASHBOARD_PASSWORD`), security response headers, warning logs
on every checker error path instead of silent `unknown` results, and a
pinned `python:3.12-slim` base image digest. README documents the auth
env vars and warns about `docker`-group root-equivalence.

## DS-009 — Home lab deployment documentation
`docs/HOMELAB_DEPLOYMENT.md` walkthrough for Proxmox VE + Ubuntu Server
VM deployment; README platform support section updated with home lab
notes and the port 8081 deviation.

## DS-008 — README finalization + pre-publish audit
README verified against the final implementation; fresh-clone smoke
test performed; user completed the pre-publish credential/history audit.

## DS-007 — CI pipeline (GitHub Actions)
`.github/workflows/ci.yml` runs the offline test suite on push/PR to
`master`; CI badge added to README.

## DS-006 — FastAPI read-only dashboard
`dashboard/main.py` — `GET /status` (JSON) and `GET /` (HTML), staleness
detection, per-check breakdown, `results.db` mounted read-only in
`docker-compose.yml`.

## DS-005 — systemd timer + service
`systemd/docker-sentinel.service` and `.timer` — 5-minute interval,
installed via `systemd/install.sh`.

## DS-004 — db.py + check.py orchestration
`checker/db.py` (SQLite persistence, upsert by container name) and
`checker/check.py` (runnable entry point via `python -m checker.check`).

## DS-003 — docker_checker.py container inspection
`checker/docker_checker.py` — `check_container` / `check_all`, four
per-container checks (restart, healthcheck, port, log activity) via the
docker-py SDK, live-fixture-proven for both healthy and broken states.

## DS-002 — severity.py
Pure `compute_severity` / `aggregate_severity` functions, thresholds
passed as a config dict, boundary-tested for every check type.

## DS-001 — Repo scaffolding
`.gitignore`, `LICENSE`, directory skeleton, pinned requirements files,
`config/settings.yaml.example`.
