# SPEC — docker-sentinel

Technical spec of the current implementation. `docs/PRD.md` covers the
problem/goals/non-goals; this covers module interfaces, data shapes, and
the exact rules each check applies — the "how it actually works" layer
between the PRD and the source.

---

## Module map

```
checker/severity.py       pure functions, no I/O
checker/docker_checker.py per-container checks via docker-py
checker/db.py              SQLite persistence
checker/check.py           entry point, wires the above together
dashboard/main.py          FastAPI read-only dashboard
```

## `checker/severity.py`

```python
compute_severity(check_type: str, value: float | str | None, config: dict) -> str
aggregate_severity(severities: list[str]) -> str
```

Returns one of `"healthy"`, `"warning"`, `"critical"`, `"unknown"`.
`value=None` always returns `"unknown"`. No Docker/network access, no
hardcoded thresholds — every numeric boundary comes from the `config`
dict passed in (`config["thresholds"][...]`), which is why tests can
override thresholds without touching files.

Per-check rules (boundaries are exclusive `>`/`<`, i.e. exactly-at-boundary
is still healthy):

| `check_type` | `value` | Rule |
|---|---|---|
| `restart` | RestartCount (int) | `> critical` → critical, `> warning` → warning, else healthy |
| `uptime` | seconds (float) | `< critical_seconds` → critical, `< warning_seconds` → warning, else healthy |
| `healthcheck` | `"unhealthy"` / `"healthy"` / `"no_healthcheck"` / elapsed-seconds (float, only while `status="starting"`) | `"unhealthy"`→critical, `"healthy"`→healthy, `"no_healthcheck"`→warning, elapsed `> starting_warning_seconds`→warning else healthy |
| `port` | `"refused"` / `"timeout"` / connect-ms (float) | refused/timeout→critical, ms `> warning_ms`→warning, else healthy |
| `log_silence` | hours-since-last-output (float) | `> critical_hours`→critical, `> warning_hours`→warning, else healthy |

`aggregate_severity([])` → `"unknown"` (no data is ambiguous, not
healthy). Otherwise returns the worst entry by
`critical(3) > warning(2) > unknown(1) > healthy(0)`.

## `checker/docker_checker.py`

```python
check_container(container, config: dict) -> dict
check_all(client: docker.DockerClient, config: dict) -> list[dict]
```

`check_container` runs all four checks and returns:

```python
{
    "container_id": str,
    "name": str,
    "image": str,          # container.image.tags[0], falls back to
                            # attrs["Config"]["Image"] if untagged
    "status": str,          # container.status
    "checks": {
        "restart":      {"check_type", "value", "severity", "detail"},
        "healthcheck":  {"check_type", "value", "severity", "detail"},
        "port":         {"check_type", "value", "severity", "detail"},
        "log_silence":  {"check_type", "value", "severity", "detail"},
    },
    "severity": str,        # aggregate_severity() of the four checks
    "checked_at": str,      # ISO-8601 UTC
}
```

Each check function (`_restart_check`, `_healthcheck_check`,
`_port_check`, `_log_activity_check`) wraps its Docker API access in
`try/except Exception`; on failure it returns
`{"severity": "unknown", "error": str(exc), ...}` and logs
`logger.warning(...)` — a check never raises out of `check_container`,
and a Docker API surprise (e.g. a field rename) is visible in logs
instead of silently looking like a transient blip.

**Port check specifics:** connects to the container's own IP
(`NetworkSettings.Networks[...].IPAddress`), not `localhost` — this
bypasses Docker's userland proxy, which otherwise accepts the TCP
connection before forwarding, making `socket.create_connection` succeed
even when nothing is listening inside the container. Containers with no
published ports return `"unknown"` (not a failure — internal
services like databases legitimately have none).

**Log activity check:** binary only — `container.logs(since=N_hours_ago,
tail=1)` empty vs non-empty, not error-string parsing. Checked twice per
call (critical window, then warning window) so a container silent past
the critical threshold is distinguished from one silent past only the
warning threshold.

`check_all` calls `client.containers.list()` (running containers only)
and returns `[]` on `docker.errors.APIError` rather than raising — a
transient Docker daemon hiccup never crashes the checker run.

## `checker/db.py`

Single table:

```sql
CREATE TABLE IF NOT EXISTS container_checks (
    name         TEXT PRIMARY KEY,   -- upsert key: container NAME, not ID
    container_id TEXT NOT NULL,
    image        TEXT NOT NULL,
    status       TEXT NOT NULL,
    checks       TEXT NOT NULL,      -- JSON-encoded per-check dict
    severity     TEXT NOT NULL,
    checked_at   TEXT NOT NULL
)
```

Upsert key is the container **name**, not `container_id` — a container
recreated with `docker compose up` gets a new ID but keeps its name, and
the row should update in place rather than accumulate stale rows for
IDs that no longer exist.

`:memory:` paths are cached in a module-level dict
(`_get_conn`/`_close_cached`) because SQLite destroys an in-memory DB
the moment its last connection closes — file-per-call semantics don't
work there, so tests share one cached connection per `:memory:` path
and call `_close_cached` in teardown to reset state between tests.

`get_last_checked` returns `MAX(checked_at)` across all rows, parsed
back to a `datetime`, or `None` if the table is empty.

## `checker/check.py` (entry point)

```
python -m checker.check
```

Must be invoked as a module (`python -m checker.check`), not a script
(`python checker/check.py`) — it does an absolute `from checker.db
import ...` style import that only resolves when the project root is on
`sys.path`, which `-m` guarantees and direct script invocation doesn't.

Sequence: load `config/settings.yaml` (exit 1 if missing) →
`init_db(DB_PATH)` → `docker.from_env()` → `check_all()` → write results
→ log a one-line summary. `docker.errors.DockerException` on connect is
caught and logged; the run still writes an empty result set rather than
crashing, so the dashboard can at least report "0 containers checked"
instead of going stale silently.

Env vars: `SENTINEL_DB_PATH` (default `results.db`),
`SENTINEL_CONFIG_PATH` (default `config/settings.yaml`).

## `dashboard/main.py`

`GET /status` → JSON (`containers`, `last_checked` ISO-8601 or `null`,
`stale` bool). `GET /` → the same data rendered as HTML via Jinja2.
Both routes are read-only against `results.db` — no import of
`write_results` anywhere in `dashboard/`.

**Staleness:** `age = now - last_checked; stale = age > interval_seconds
* stale_multiplier` (both read from `config/settings.yaml`, defaults
300s / 2×). `last_checked=None` (empty DB) is always stale.

**Auth (optional):** `SENTINEL_DASHBOARD_USER` /
`SENTINEL_DASHBOARD_PASSWORD` — both or neither must be set (module
raises `RuntimeError` at import time if only one is present). When set,
`require_auth` (an `HTTPBasic` dependency, `auto_error=False`) gates
both routes, comparing credentials with `secrets.compare_digest`
(timing-safe). When set, FastAPI's own auto-registered `/docs`,
`/redoc`, and `/openapi.json` routes are also disabled at construction
time (`docs_url`/`redoc_url`/`openapi_url=None`) — they're registered
outside the app's route handlers, so `require_auth` can't gate them
directly. When unset, a `logger.warning` fires once at import time
and the dashboard serves unauthenticated — this is the default because
the dashboard is meant to sit behind a reverse proxy/VPN, not be exposed
directly (see README).

**Security headers:** a `@app.middleware("http")` sets
`X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`,
`Content-Security-Policy: default-src 'self'; img-src 'self' data:`,
`Referrer-Policy: no-referrer` on every response. All frontend assets
(Tabler CSS/JS/icon font, theme CSS, theme-toggle JS) are vendored
under `dashboard/static/` and served same-origin via a `StaticFiles`
mount — no CDN dependency, no `'unsafe-inline'` needed. The `img-src
data:` allowance covers inline SVG data-URI backgrounds in the
vendored Tabler CSS (form-control icons, checkboxes) — not user input.

## Config schema (`config/settings.yaml`)

```yaml
checker:
  interval_seconds: 300
thresholds:
  restart:      {warning: 3, critical: 10}
  uptime:       {warning_seconds: 300, critical_seconds: 60}
  healthcheck:  {starting_warning_seconds: 300}
  port:         {timeout_seconds: 5, warning_ms: 2000}
  log_silence:  {warning_hours: 2, critical_hours: 6}
dashboard:
  port: 8080              # internal container port; host mapping is
                           # docker-compose.yml's concern (8081 on this
                           # machine — see docs/HOMELAB_DEPLOYMENT.md)
  stale_multiplier: 2
```

## Test split

`@pytest.mark.docker` marks tests needing a live Docker daemon (real
fixture containers: crash-loop, failing healthcheck, closed port).
Everything else uses mocks (`unittest.mock.MagicMock` for
`docker.DockerClient`/container objects) or `:memory:` SQLite. CI runs
only `pytest -m "not docker"`.
