# CHANGELOG — docker-sentinel

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
