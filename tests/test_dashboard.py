from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from dashboard.main import app

client = TestClient(app)

CONTAINERS = [
    {
        "container_id": "abc123",
        "name": "web",
        "image": "nginx:alpine",
        "status": "running",
        "severity": "warning",
        "checked_at": "2026-06-24T00:00:00+00:00",
        "checks": {
            "restart": {"check_type": "restart", "value": 0, "severity": "healthy", "detail": "restarts=0, uptime=3600s"},
            "healthcheck": {"check_type": "healthcheck", "value": "no_healthcheck", "severity": "warning", "detail": "no healthcheck defined"},
            "port": {"check_type": "port", "value": None, "severity": "unknown", "detail": "no published ports"},
            "log_silence": {"check_type": "log_silence", "value": 0.0, "severity": "healthy", "detail": "log activity within last 2h"},
        },
    }
]

RECENT = datetime.now(timezone.utc) - timedelta(seconds=60)
STALE = datetime.now(timezone.utc) - timedelta(seconds=700)  # > 600s = 2 × 300s default interval


@pytest.fixture
def mock_db():
    with (
        patch("dashboard.main.read_results", return_value=CONTAINERS),
        patch("dashboard.main.get_last_checked", return_value=RECENT),
    ):
        yield


@pytest.fixture
def empty_db():
    with (
        patch("dashboard.main.read_results", return_value=[]),
        patch("dashboard.main.get_last_checked", return_value=None),
    ):
        yield


# ---------------------------------------------------------------------------
# GET /status
# ---------------------------------------------------------------------------

class TestStatusEndpoint:
    def test_returns_200(self, mock_db):
        assert client.get("/status").status_code == 200

    def test_has_required_keys(self, mock_db):
        data = client.get("/status").json()
        assert {"containers", "last_checked", "stale"} <= data.keys()

    def test_containers_list(self, mock_db):
        data = client.get("/status").json()
        assert len(data["containers"]) == 1
        assert data["containers"][0]["name"] == "web"

    def test_checks_breakdown_in_containers(self, mock_db):
        data = client.get("/status").json()
        checks = data["containers"][0]["checks"]
        assert "restart" in checks
        assert "healthcheck" in checks
        assert "port" in checks
        assert "log_silence" in checks

    def test_not_stale_when_recent(self, mock_db):
        assert client.get("/status").json()["stale"] is False

    def test_stale_when_old(self):
        with (
            patch("dashboard.main.read_results", return_value=[]),
            patch("dashboard.main.get_last_checked", return_value=STALE),
        ):
            assert client.get("/status").json()["stale"] is True

    def test_stale_when_no_data(self, empty_db):
        assert client.get("/status").json()["stale"] is True

    def test_last_checked_is_iso_string(self, mock_db):
        last = client.get("/status").json()["last_checked"]
        assert last is not None
        datetime.fromisoformat(last)  # raises if not valid ISO-8601

    def test_last_checked_none_when_empty_db(self, empty_db):
        assert client.get("/status").json()["last_checked"] is None


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------

class TestRootEndpoint:
    def test_returns_200(self, mock_db):
        assert client.get("/").status_code == 200

    def test_returns_html(self, mock_db):
        assert "text/html" in client.get("/").headers["content-type"]

    def test_contains_container_name(self, mock_db):
        assert "web" in client.get("/").text

    def test_contains_image_name(self, mock_db):
        assert "nginx:alpine" in client.get("/").text

    def test_contains_severity_badge(self, mock_db):
        assert "warning" in client.get("/").text

    def test_stale_banner_present_when_stale(self):
        with (
            patch("dashboard.main.read_results", return_value=[]),
            patch("dashboard.main.get_last_checked", return_value=STALE),
        ):
            text = client.get("/").text
            assert "stale" in text.lower()

    def test_no_stale_banner_when_fresh(self, mock_db):
        text = client.get("/").text
        assert "STALE" not in text

    def test_empty_db_shows_no_containers_message(self, empty_db):
        assert "No containers found" in client.get("/").text


# ---------------------------------------------------------------------------
# Read-only guarantee
# ---------------------------------------------------------------------------

class TestReadOnlyGuarantee:
    def test_status_never_calls_write_results(self, mock_db):
        def _must_not_be_called(*args, **kwargs):
            raise AssertionError("write_results called from /status")

        with patch("checker.db.write_results", side_effect=_must_not_be_called):
            resp = client.get("/status")
            assert resp.status_code == 200

    def test_root_never_calls_write_results(self, mock_db):
        def _must_not_be_called(*args, **kwargs):
            raise AssertionError("write_results called from /")

        with patch("checker.db.write_results", side_effect=_must_not_be_called):
            resp = client.get("/")
            assert resp.status_code == 200
