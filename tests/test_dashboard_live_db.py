import importlib
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import dashboard.main as dashboard_main
from checker.db import init_db, write_results

NOW = datetime.now(timezone.utc)
RECENT_TS = (NOW - timedelta(seconds=60)).isoformat()
STALE_TS = (NOW - timedelta(seconds=700)).isoformat()  # > 600s = 2 x 300s default interval

HEALTHY_ROW = {
    "container_id": "abc123",
    "name": "web",
    "image": "nginx:alpine",
    "status": "running",
    "checks": {
        "restart": {"check_type": "restart", "value": 0, "severity": "healthy", "detail": "restarts=0, uptime=3600s"},
    },
    "severity": "healthy",
    "checked_at": RECENT_TS,
}

CRITICAL_ROW = {
    "container_id": "def456",
    "name": "db",
    "image": "postgres:16",
    "status": "running",
    "checks": {
        "port": {"check_type": "port", "value": None, "severity": "critical", "detail": "connection refused"},
    },
    "severity": "critical",
    "checked_at": RECENT_TS,
}


@pytest.fixture
def live_client(tmp_path, monkeypatch):
    """Points dashboard.main at a real, caller-seeded SQLite file via env + reload."""
    db_path = str(tmp_path / "results.db")
    init_db(db_path)

    monkeypatch.setenv("SENTINEL_DB_PATH", db_path)
    monkeypatch.setenv("SENTINEL_CONFIG_PATH", str(tmp_path / "nonexistent-settings.yaml"))
    monkeypatch.delenv("SENTINEL_DASHBOARD_USER", raising=False)
    monkeypatch.delenv("SENTINEL_DASHBOARD_PASSWORD", raising=False)
    try:
        importlib.reload(dashboard_main)
        yield TestClient(dashboard_main.app), db_path
    finally:
        monkeypatch.undo()
        importlib.reload(dashboard_main)


class TestLiveDbStatusEndpoint:
    def test_status_json_reflects_seeded_healthy_and_critical_rows(self, live_client):
        client, db_path = live_client
        write_results(db_path, [HEALTHY_ROW, CRITICAL_ROW])

        data = client.get("/status").json()

        names_to_severity = {c["name"]: c["severity"] for c in data["containers"]}
        assert names_to_severity == {"web": "healthy", "db": "critical"}
        assert data["last_checked"] is not None
        assert data["stale"] is False

    def test_status_json_reports_stale_when_last_checked_past_threshold(self, live_client):
        client, db_path = live_client
        stale_row = {**HEALTHY_ROW, "checked_at": STALE_TS}
        write_results(db_path, [stale_row])

        data = client.get("/status").json()

        assert data["stale"] is True

    def test_status_json_empty_db(self, live_client):
        client, _db_path = live_client

        data = client.get("/status").json()

        assert data["containers"] == []
        assert data["last_checked"] is None
        assert data["stale"] is True


class TestLiveDbRootEndpoint:
    def test_index_html_reflects_seeded_container_names_and_severity_badges(self, live_client):
        client, db_path = live_client
        write_results(db_path, [HEALTHY_ROW, CRITICAL_ROW])

        resp = client.get("/")

        assert resp.headers["content-type"].startswith("text/html")
        assert "web" in resp.text
        assert "db" in resp.text
        assert "healthy" in resp.text
        assert "critical" in resp.text

    def test_index_html_shows_stale_banner_when_stale(self, live_client):
        client, db_path = live_client
        stale_row = {**HEALTHY_ROW, "checked_at": STALE_TS}
        write_results(db_path, [stale_row])

        resp = client.get("/")

        assert "STALE" in resp.text
        assert "Data is stale" in resp.text

    def test_index_html_no_stale_banner_when_fresh(self, live_client):
        client, db_path = live_client
        write_results(db_path, [HEALTHY_ROW])

        resp = client.get("/")

        assert "STALE" not in resp.text

    def test_index_html_empty_db_shows_no_containers_message(self, live_client):
        client, _db_path = live_client

        resp = client.get("/")

        assert "No containers found" in resp.text
