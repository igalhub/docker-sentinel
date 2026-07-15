#!/usr/bin/env bash
# Project-specific DB/seed reset.
# Resets SEED_RESET_DB_PATH (required, must be set explicitly) to a
# known-good baseline.
#
# Baseline rows:
#   seed-web    (nginx:alpine)  severity=healthy  restart check, 0 restarts
#   seed-cache  (redis:7)       severity=warning  healthcheck check, no healthcheck defined
# Together these exercise both ends of the severity scale that
# verify-data.sh's severity_values_valid check expects to see, with a
# checked_at fresh enough to pass its freshness check by default.
#
# SAFETY: refuses to run unless SEED_RESET_DB_PATH is set, and refuses to
# target the live production results.db in the repo root. This project's
# results.db is written every 5 minutes by a real systemd timer — this
# script must never be pointed at it.

set -euo pipefail

DB_PATH="${SEED_RESET_DB_PATH:?Set SEED_RESET_DB_PATH to the target SQLite file. Refuses to run without an explicit path.}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
if [[ "$(readlink -f "$DB_PATH" 2>/dev/null || echo "$DB_PATH")" == "$REPO_ROOT/results.db" ]]; then
  echo "seed-reset refuses to target the live production results.db ($REPO_ROOT/results.db)" >&2
  exit 1
fi

cd "$REPO_ROOT" || exit 1
python3 - "$DB_PATH" <<'PYEOF'
import sys
from datetime import datetime, timezone
from checker.db import init_db, write_results

path = sys.argv[1]
now = datetime.now(timezone.utc).isoformat()

init_db(path)
write_results(path, [
    {
        "container_id": "seed-healthy-1",
        "name": "seed-web",
        "image": "nginx:alpine",
        "status": "running",
        "checks": {"restart": {"check_type": "restart", "value": 0, "severity": "healthy", "detail": "restarts=0, uptime=3600s"}},
        "severity": "healthy",
        "checked_at": now,
    },
    {
        "container_id": "seed-warning-1",
        "name": "seed-cache",
        "image": "redis:7",
        "status": "running",
        "checks": {"healthcheck": {"check_type": "healthcheck", "value": "no_healthcheck", "severity": "warning", "detail": "no healthcheck defined"}},
        "severity": "warning",
        "checked_at": now,
    },
])
print(f"seed-reset complete — 2 rows written to {path}, checked_at={now}")
PYEOF
