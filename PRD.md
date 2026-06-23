# PRD — docker-sentinel

A lightweight, standalone container health monitoring tool that detects
the Docker containers that `docker ps` shows as "running" but are
silently broken. Surfaces results via a read-only status dashboard.
Third in a series of silent-failure detectors alongside Vault Secrets
Demo and Expiry Watcher.

---

## Problem statement

A container being "Up" in Docker's view means exactly one thing: the
process didn't exit. It doesn't mean the process is responding to
requests, doing any work, passing its own health check, or not restarting
every 30 seconds. All of these can be broken while `docker ps` shows
"Up 2 hours."

Existing tools partially cover this:
- Portainer shows healthcheck status but not log activity or port
  responsiveness
- Prometheus + cAdvisor shows restart counts but not port responsiveness
  without extra exporters
- Manual `docker inspect` covers everything but requires actively looking

docker-sentinel checks all four failure patterns in one scheduled pass
and surfaces the results in a single dashboard — without requiring a full
observability stack.

---

## Goals

- G1: Detect crash-loop restarts (RestartCount + uptime pattern)
- G2: Detect containers Docker itself has marked unhealthy, or that are
  stuck in `starting` too long
- G3: Detect containers whose exposed ports are not accepting connections
- G4: Detect containers that have gone silent (no log output for N hours)
- G5: Flag containers with no healthcheck defined (best-practice gap,
  not a failure)
- G6: Present all results via a read-only dashboard (same architecture
  and visual design as Expiry Watcher)
- G7: All thresholds configurable via `config/settings.yaml`
- G8: Zero credentials or secrets committed to git

---

## Non-goals (v1)

- **Resource monitoring (CPU/memory trends)** — requires polling over
  time to detect trends, not just a point-in-time check; deferred to v2.
  The existing SQLite schema supports it but the checker logic is
  significantly more complex.
- **Auto-remediation** — this tool detects and surfaces, never restarts
  or kills containers automatically. Auto-fixing containers carries real
  risks (lost locks, cascading failures, data corruption) and belongs in
  a separate, explicitly-scoped tool.
- **Log parsing / error detection** — "last log line contains ERROR" is
  ambiguous across arbitrary log formats; deferred to v2. v1 only checks
  whether any log output occurred (binary yes/no).
- **Network routing between containers** — can check if a port accepts a
  TCP connection, not if the container correctly routes to other services
- **Security vulnerability scanning** — different tool category entirely
  (Trivy, Snyk, etc.)
- **Application-level errors** — the tool sees the container runtime, not
  what's inside it. A web server returning 500s to every request looks
  healthy from the outside.

---

## What this tool detects (v1 scope)

### Category 1 — Lifecycle / crash-loop

| Problem | How detected |
|---|---|
| Crash-loop restart | RestartCount > threshold AND uptime < threshold |
| Frequent unplanned restarts | RestartCount > warning threshold even if currently stable |
| Short-lived container | Uptime < critical threshold consistently |

### Category 2 — Docker healthcheck

| Problem | How detected |
|---|---|
| Container marked `unhealthy` | `Health.Status == "unhealthy"` from `docker inspect` |
| Container stuck in `starting` | `Health.Status == "starting"` for > 5 minutes |
| No healthcheck defined | `Health` key absent — flagged as a gap, not a failure |

### Category 3 — Port responsiveness

| Problem | How detected |
|---|---|
| Port not accepting connections | TCP connect to exposed port fails |
| Port responding slowly | TCP connect takes > warning threshold |
| No exposed ports | Noted in output; not a failure unless expected |

### Category 4 — Log activity (binary only)

| Problem | How detected |
|---|---|
| Silent container | No log output in last N hours (configurable) |

### What this tool cannot detect

- Application-level errors (500s, business logic failures)
- Data corruption in volumes
- Network routing between containers
- Security vulnerabilities in images
- Performance degradation without resource spike
- Memory leaks (deferred to v2 resource monitoring)

---

## Severity thresholds (defaults, all configurable)

| Check | Warning | Critical |
|---|---|---|
| RestartCount | > 3 | > 10 |
| Uptime at check time | < 5 minutes | < 60 seconds |
| Healthcheck status | `starting` > 5 min | `unhealthy` |
| Port response time | > 2 seconds | connection refused / timeout |
| Log silence | > 2 hours | > 6 hours |
| No healthcheck defined | warning (best-practice gap) | — |

Severity aggregation: a container's overall severity is the worst of all
individual check results. A container that passes restart/uptime but
fails port responsiveness is `critical`.

---

## Architecture

```
systemd timer (every 5 minutes — faster than cert checks since
  containers can loop quickly)
  └── python -m checker.check
        └── docker_checker.py (uses docker-py SDK)
              ├── lists all running containers
              └── per container:
                    ├── restart_check()       — RestartCount + uptime
                    ├── healthcheck_check()   — Docker health status
                    ├── port_check()          — TCP connect to exposed ports
                    └── log_activity_check()  — any output in last N hours?
              └── writes per-container results → results.db (SQLite)

FastAPI dashboard (separate process, same pattern as Expiry Watcher)
  └── dashboard/main.py — READ-ONLY
        ├── GET /status → JSON: per-container aggregate + per-check breakdown
        └── GET /       → HTML table, color-coded by severity,
                          expandable per-check detail, staleness indicator
```

**Why two processes?** Same reasoning as Expiry Watcher: a dashboard
crash doesn't stop checks running, and a checker failure doesn't hide
last-known state. The dashboard's own staleness is visible when the
checker stops running.

**Why docker-py SDK, not docker CLI?**
`docker-py` (`docker` Python package) connects directly to the Docker
socket via the official API. No subprocess overhead, no CLI parsing, no
shell injection risk. `docker.from_env()` connects to the local Docker
daemon; all inspect/stats/logs calls are typed Python objects, not
strings to parse.

---

## Design decisions and rationale

| Decision | Reasoning |
|---|---|
| Per-check breakdown, not just aggregate severity | "Container is critical" is less useful than "container is critical because port is not responding and healthcheck is unhealthy" — the breakdown is actionable, the aggregate alone is not |
| Log activity as binary (any output?) not error detection | Log formats vary wildly across containers; reliable error detection requires per-container parser configuration. Binary "any output in last N hours" is unambiguous and cheap (`docker logs --since Nh --tail 1`) |
| Skip port check for containers with no exposed ports | Internal services (databases, workers) legitimately have no published ports — marking them as "unknown" rather than "critical" avoids false positives |
| Flag "no healthcheck defined" as warning, not error | It's a best-practice gap, not a runtime failure. But surfacing it is valuable — it's the kind of thing that's easy to miss and easy to fix |
| 5-minute check interval (vs 6 hours for Expiry Watcher) | Containers can crash-loop in seconds; a 6-hour cert check interval would miss the pattern entirely |
| Thresholds configurable via settings.yaml | Different environments have different norms — a batch job that runs once an hour looks "silent" with a 30-minute silence threshold but is fine with a 2-hour one |
| Resource monitoring deferred to v2 | Trend detection requires multiple data points over time — the architecture supports it (SQLite stores history) but the checker logic is meaningfully more complex. Better to ship a clean v1 than a half-baked v2 feature |

---

## Success criteria

- A fresh clone + setup script gets the checker running against real
  local containers in under 5 minutes
- The checker correctly identifies: a healthy container, a crash-looping
  container (proven with a real fixture that exits immediately),
  an unhealthy container (proven with a container whose HEALTHCHECK
  always fails), a container with a closed port, and a silent container
- The dashboard correctly displays all results with per-check breakdown
- All thresholds are configurable and the README documents each one
- Full test suite including live fixtures for each failure mode
- README documents exactly what's detected, what's not, and why

---

## Out of scope risks (explicitly documented)

- Single-host only — monitors containers on the local Docker daemon only;
  multi-host/Swarm/Kubernetes monitoring is out of scope
- No alerting channel (email, Slack, PagerDuty) in v1 — dashboard only
- Resource monitoring (CPU/memory trends) deferred to v2
- Log error detection deferred to v2
