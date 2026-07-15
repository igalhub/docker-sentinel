#!/usr/bin/env bash
# Project-specific data integrity checks.
# Each check prints: CHECK_NAME | STATUS | detail
# STATUS: OK or FAIL
#
# Checks VERIFY_DATA_DB_PATH (required, must be set explicitly) — never
# defaults to the repo's live results.db, since this project's real DB is
# written every 5 minutes by a systemd timer and must never be targeted
# by an ad-hoc verification run.
#
# Staleness threshold mirrors dashboard/main.py's _load_config exactly
# (same file, same fallback-to-defaults behavior on missing/malformed/empty
# YAML) so this check never silently drifts from what the live dashboard
# actually uses. Override via VERIFY_DATA_CONFIG_PATH for testing.

set -uo pipefail

DB_PATH="${VERIFY_DATA_DB_PATH:?Set VERIFY_DATA_DB_PATH to the target SQLite file. Refuses to run without an explicit path.}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "$(readlink -f "$DB_PATH" 2>/dev/null || echo "$DB_PATH")" == "$REPO_ROOT/results.db" ]]; then
  echo "verify-data refuses to target the live production results.db ($REPO_ROOT/results.db)" >&2
  exit 1
fi

CONFIG_PATH="${VERIFY_DATA_CONFIG_PATH:-$REPO_ROOT/config/settings.yaml}"

cd "$REPO_ROOT" || exit 1
python3 - "$DB_PATH" "$CONFIG_PATH" <<'PYEOF'
import sys
from datetime import datetime, timezone

import yaml

from checker.db import read_results, get_last_checked

db_path, config_path = sys.argv[1], sys.argv[2]
VALID_SEVERITIES = {"healthy", "warning", "critical", "unknown"}
_DEFAULT_CONFIG = {"checker": {"interval_seconds": 300}, "dashboard": {"stale_multiplier": 2}}


def _load_config(path):
    # Mirrors dashboard/main.py's _load_config exactly, so this threshold
    # never drifts from what the live dashboard actually enforces.
    try:
        with open(path) as f:
            config = yaml.safe_load(f)
    except FileNotFoundError:
        return _DEFAULT_CONFIG
    except yaml.YAMLError:
        print(f"config_load | WARN | {path} is malformed YAML — falling back to default config", file=sys.stderr)
        return _DEFAULT_CONFIG
    if config is None:
        print(f"config_load | WARN | {path} is empty or contains no YAML content — falling back to default config", file=sys.stderr)
        return _DEFAULT_CONFIG
    return config


fail = False
config = _load_config(config_path)
stale_threshold_seconds = config["checker"]["interval_seconds"] * config["dashboard"]["stale_multiplier"]

rows = read_results(db_path)

bad_severities = [r["name"] for r in rows if r["severity"] not in VALID_SEVERITIES]
if bad_severities:
    print(f"severity_values_valid | FAIL | invalid severity on: {', '.join(bad_severities)}")
    fail = True
else:
    print(f"severity_values_valid | OK | {len(rows)} rows, all severities valid")

last_checked = get_last_checked(db_path)
if last_checked is None:
    print("freshness | FAIL | no rows / no checked_at value")
    fail = True
else:
    age = (datetime.now(timezone.utc) - last_checked).total_seconds()
    if age > stale_threshold_seconds:
        print(f"freshness | FAIL | last_checked is {age:.0f}s old (> {stale_threshold_seconds:.0f}s threshold)")
        fail = True
    else:
        print(f"freshness | OK | last_checked is {age:.0f}s old (threshold {stale_threshold_seconds:.0f}s)")

sys.exit(1 if fail else 0)
PYEOF
