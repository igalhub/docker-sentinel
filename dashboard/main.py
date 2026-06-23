import os
from datetime import datetime, timezone
from pathlib import Path

import yaml
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from checker.db import get_last_checked, read_results

DB_PATH = os.environ.get("SENTINEL_DB_PATH", "results.db")
CONFIG_PATH = os.environ.get("SENTINEL_CONFIG_PATH", "config/settings.yaml")

app = FastAPI()
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def _load_config() -> dict:
    try:
        with open(CONFIG_PATH) as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        return {"checker": {"interval_seconds": 300}, "dashboard": {"stale_multiplier": 2}}


def _is_stale(last_checked: datetime | None, config: dict) -> bool:
    if last_checked is None:
        return True
    interval = config["checker"]["interval_seconds"]
    multiplier = config["dashboard"]["stale_multiplier"]
    age = (datetime.now(timezone.utc) - last_checked).total_seconds()
    return age > interval * multiplier


@app.get("/status")
def status():
    config = _load_config()
    containers = read_results(DB_PATH)
    last_checked = get_last_checked(DB_PATH)
    return {
        "containers": containers,
        "last_checked": last_checked.isoformat() if last_checked else None,
        "stale": _is_stale(last_checked, config),
    }


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    config = _load_config()
    containers = read_results(DB_PATH)
    last_checked = get_last_checked(DB_PATH)
    stale = _is_stale(last_checked, config)

    if last_checked is None:
        last_checked_str = "never"
    else:
        age = (datetime.now(timezone.utc) - last_checked).total_seconds()
        if age < 60:
            last_checked_str = "just now"
        elif age < 3600:
            last_checked_str = f"{int(age // 60)} minute(s) ago"
        else:
            last_checked_str = f"{int(age // 3600)} hour(s) ago"

    counts = {
        "healthy": sum(1 for c in containers if c["severity"] == "healthy"),
        "warning": sum(1 for c in containers if c["severity"] == "warning"),
        "critical": sum(1 for c in containers if c["severity"] == "critical"),
        "unknown": sum(1 for c in containers if c["severity"] not in ("healthy", "warning", "critical")),
    }

    return templates.TemplateResponse(
        request,
        "index.html",
        context={
            "containers": containers,
            "last_checked_str": last_checked_str,
            "stale": stale,
            "counts": counts,
        },
    )
