import logging
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

import yaml
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi import status as http_status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from checker.db import get_last_checked, read_results

logger = logging.getLogger(__name__)

DB_PATH = os.environ.get("SENTINEL_DB_PATH", "results.db")
CONFIG_PATH = os.environ.get("SENTINEL_CONFIG_PATH", "config/settings.yaml")
DASHBOARD_USER = os.environ.get("SENTINEL_DASHBOARD_USER")
DASHBOARD_PASSWORD = os.environ.get("SENTINEL_DASHBOARD_PASSWORD")

if bool(DASHBOARD_USER) != bool(DASHBOARD_PASSWORD):
    raise RuntimeError(
        "SENTINEL_DASHBOARD_USER and SENTINEL_DASHBOARD_PASSWORD must both be set, or both left unset"
    )

if not DASHBOARD_USER:
    logger.warning(
        "SENTINEL_DASHBOARD_USER/SENTINEL_DASHBOARD_PASSWORD not set — "
        "dashboard is running WITHOUT authentication. Do not expose this port "
        "directly to the internet; put it behind a reverse proxy or VPN."
    )

app = FastAPI(
    docs_url="/docs" if not DASHBOARD_USER else None,
    redoc_url="/redoc" if not DASHBOARD_USER else None,
    openapi_url="/openapi.json" if not DASHBOARD_USER else None,
)
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
security = HTTPBasic(auto_error=False)


def require_auth(credentials: Annotated[HTTPBasicCredentials | None, Depends(security)] = None) -> None:
    if not DASHBOARD_USER:
        return

    if credentials is None or not (
        secrets.compare_digest(credentials.username, DASHBOARD_USER)
        and secrets.compare_digest(credentials.password, DASHBOARD_PASSWORD)
    ):
        raise HTTPException(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data:"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


_DEFAULT_CONFIG = {"checker": {"interval_seconds": 300}, "dashboard": {"stale_multiplier": 2}}


def _load_config() -> dict:
    try:
        with open(CONFIG_PATH) as f:
            config = yaml.safe_load(f)
    except FileNotFoundError:
        return _DEFAULT_CONFIG
    except yaml.YAMLError:
        logger.warning(
            "%s is malformed YAML — falling back to default config", CONFIG_PATH
        )
        return _DEFAULT_CONFIG

    if config is None:
        logger.warning(
            "%s is empty or contains no YAML content — falling back to default config",
            CONFIG_PATH,
        )
        return _DEFAULT_CONFIG
    return config


def _is_stale(last_checked: datetime | None, config: dict) -> bool:
    if last_checked is None:
        return True
    interval = config["checker"]["interval_seconds"]
    multiplier = config["dashboard"]["stale_multiplier"]
    age = (datetime.now(timezone.utc) - last_checked).total_seconds()
    return age > interval * multiplier


@app.get("/status")
def status(_auth: None = Depends(require_auth)):
    config = _load_config()
    containers = read_results(DB_PATH)
    last_checked = get_last_checked(DB_PATH)
    return {
        "containers": containers,
        "last_checked": last_checked.isoformat() if last_checked else None,
        "stale": _is_stale(last_checked, config),
    }


@app.get("/", response_class=HTMLResponse)
def index(request: Request, _auth: None = Depends(require_auth)):
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
