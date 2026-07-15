import importlib
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import dashboard.main as dashboard_main
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

    def test_last_checked_str_just_now(self):
        # Frozen "now" removes real elapsed wall-clock time as a variable — the
        # RECENT/STALE constants above are always ~60s/700s old *plus* however
        # long the test session has been running, so neither can deterministically
        # land in the "just now" (age < 60) branch.
        frozen_now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with (
            patch("dashboard.main.datetime") as mock_datetime,
            patch("dashboard.main.read_results", return_value=[]),
            patch("dashboard.main.get_last_checked", return_value=frozen_now - timedelta(seconds=30)),
        ):
            mock_datetime.now.return_value = frozen_now
            text = client.get("/").text
        assert "just now" in text

    def test_last_checked_str_hours_ago(self):
        frozen_now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with (
            patch("dashboard.main.datetime") as mock_datetime,
            patch("dashboard.main.read_results", return_value=[]),
            patch("dashboard.main.get_last_checked", return_value=frozen_now - timedelta(hours=2)),
        ):
            mock_datetime.now.return_value = frozen_now
            text = client.get("/").text
        assert "2 hour(s) ago" in text

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


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

class TestAuth:
    def test_no_auth_required_by_default(self, mock_db):
        # DASHBOARD_USER/PASSWORD unset in the test environment — no credentials needed.
        assert client.get("/status").status_code == 200

    def test_correct_credentials_accepted(self, mock_db, monkeypatch):
        monkeypatch.setattr(dashboard_main, "DASHBOARD_USER", "admin")
        monkeypatch.setattr(dashboard_main, "DASHBOARD_PASSWORD", "secret")
        resp = client.get("/status", auth=("admin", "secret"))
        assert resp.status_code == 200

    def test_wrong_password_rejected(self, mock_db, monkeypatch):
        monkeypatch.setattr(dashboard_main, "DASHBOARD_USER", "admin")
        monkeypatch.setattr(dashboard_main, "DASHBOARD_PASSWORD", "secret")
        resp = client.get("/status", auth=("admin", "wrong"))
        assert resp.status_code == 401

    def test_missing_credentials_rejected(self, mock_db, monkeypatch):
        monkeypatch.setattr(dashboard_main, "DASHBOARD_USER", "admin")
        monkeypatch.setattr(dashboard_main, "DASHBOARD_PASSWORD", "secret")
        resp = client.get("/status")
        assert resp.status_code == 401

    def test_root_endpoint_also_protected(self, mock_db, monkeypatch):
        monkeypatch.setattr(dashboard_main, "DASHBOARD_USER", "admin")
        monkeypatch.setattr(dashboard_main, "DASHBOARD_PASSWORD", "secret")
        assert client.get("/").status_code == 401
        assert client.get("/", auth=("admin", "secret")).status_code == 200

    def test_mismatched_env_vars_refuse_to_start(self, monkeypatch):
        monkeypatch.setenv("SENTINEL_DASHBOARD_USER", "admin")
        monkeypatch.delenv("SENTINEL_DASHBOARD_PASSWORD", raising=False)
        try:
            with pytest.raises(RuntimeError):
                importlib.reload(dashboard_main)
        finally:
            # Restore a clean, auth-disabled module state for subsequent tests —
            # reload() mutates the shared module object in place even on failure.
            monkeypatch.undo()
            importlib.reload(dashboard_main)

    def test_docs_routes_disabled_when_auth_enabled(self, monkeypatch):
        # docs_url/redoc_url/openapi_url are baked into the FastAPI app at
        # construction time, based on DASHBOARD_USER read at import time —
        # a plain attribute monkeypatch never re-triggers app construction,
        # so this must reload the module like test_mismatched_env_vars_refuse_to_start.
        monkeypatch.setenv("SENTINEL_DASHBOARD_USER", "admin")
        monkeypatch.setenv("SENTINEL_DASHBOARD_PASSWORD", "secret")
        try:
            importlib.reload(dashboard_main)
            reloaded_client = TestClient(dashboard_main.app)
            assert reloaded_client.get("/docs").status_code == 404
            assert reloaded_client.get("/redoc").status_code == 404
            assert reloaded_client.get("/openapi.json").status_code == 404
        finally:
            monkeypatch.undo()
            importlib.reload(dashboard_main)

    def test_docs_routes_enabled_when_auth_disabled(self, monkeypatch):
        monkeypatch.delenv("SENTINEL_DASHBOARD_USER", raising=False)
        monkeypatch.delenv("SENTINEL_DASHBOARD_PASSWORD", raising=False)
        try:
            importlib.reload(dashboard_main)
            reloaded_client = TestClient(dashboard_main.app)
            assert reloaded_client.get("/docs").status_code == 200
            assert reloaded_client.get("/redoc").status_code == 200
            assert reloaded_client.get("/openapi.json").status_code == 200
        finally:
            monkeypatch.undo()
            importlib.reload(dashboard_main)


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------

class TestConfigLoading:
    def test_valid_yaml_returns_parsed_content(self, tmp_path, monkeypatch):
        # Closes _load_config's only untested path — every other test here forces a fallback.
        valid_config = tmp_path / "settings.yaml"
        valid_config.write_text("checker:\n  interval_seconds: 123\ndashboard:\n  stale_multiplier: 4\n")
        monkeypatch.setenv("SENTINEL_CONFIG_PATH", str(valid_config))
        try:
            importlib.reload(dashboard_main)
            config = dashboard_main._load_config()
            assert config == {"checker": {"interval_seconds": 123}, "dashboard": {"stale_multiplier": 4}}
        finally:
            monkeypatch.undo()
            importlib.reload(dashboard_main)

    def test_malformed_yaml_falls_back_to_defaults(self, tmp_path, monkeypatch, caplog):
        bad_config = tmp_path / "settings.yaml"
        bad_config.write_text("checker: [unterminated flow mapping {\n")
        monkeypatch.setenv("SENTINEL_CONFIG_PATH", str(bad_config))
        try:
            importlib.reload(dashboard_main)
            config = dashboard_main._load_config()
            assert config == dashboard_main._DEFAULT_CONFIG
            assert "malformed" in caplog.text.lower()
        finally:
            monkeypatch.undo()
            importlib.reload(dashboard_main)

    def test_empty_yaml_falls_back_to_defaults(self, tmp_path, monkeypatch, caplog):
        empty_config = tmp_path / "settings.yaml"
        empty_config.write_text("# just a comment, no content\n")
        monkeypatch.setenv("SENTINEL_CONFIG_PATH", str(empty_config))
        try:
            importlib.reload(dashboard_main)
            config = dashboard_main._load_config()
            assert config == dashboard_main._DEFAULT_CONFIG
            assert "expected config shape" in caplog.text.lower()
        finally:
            monkeypatch.undo()
            importlib.reload(dashboard_main)

    def test_scalar_yaml_falls_back_to_defaults(self, tmp_path, monkeypatch, caplog):
        scalar_config = tmp_path / "settings.yaml"
        scalar_config.write_text("disabled\n")
        monkeypatch.setenv("SENTINEL_CONFIG_PATH", str(scalar_config))
        try:
            importlib.reload(dashboard_main)
            config = dashboard_main._load_config()
            assert config == dashboard_main._DEFAULT_CONFIG
            assert "expected config shape" in caplog.text.lower()
        finally:
            monkeypatch.undo()
            importlib.reload(dashboard_main)

    def test_list_yaml_falls_back_to_defaults(self, tmp_path, monkeypatch, caplog):
        list_config = tmp_path / "settings.yaml"
        list_config.write_text("- checker\n- dashboard\n")
        monkeypatch.setenv("SENTINEL_CONFIG_PATH", str(list_config))
        try:
            importlib.reload(dashboard_main)
            config = dashboard_main._load_config()
            assert config == dashboard_main._DEFAULT_CONFIG
            assert "expected config shape" in caplog.text.lower()
        finally:
            monkeypatch.undo()
            importlib.reload(dashboard_main)

    def test_missing_required_key_falls_back_to_defaults(self, tmp_path, monkeypatch, caplog):
        missing_key_config = tmp_path / "settings.yaml"
        missing_key_config.write_text("checker:\n  interval_seconds: 300\n")
        monkeypatch.setenv("SENTINEL_CONFIG_PATH", str(missing_key_config))
        try:
            importlib.reload(dashboard_main)
            config = dashboard_main._load_config()
            assert config == dashboard_main._DEFAULT_CONFIG
            assert "expected config shape" in caplog.text.lower()
        finally:
            monkeypatch.undo()
            importlib.reload(dashboard_main)

    def test_wrong_value_type_falls_back_to_defaults(self, tmp_path, monkeypatch, caplog):
        wrong_type_config = tmp_path / "settings.yaml"
        wrong_type_config.write_text(
            "checker:\n  interval_seconds: \"soon\"\ndashboard:\n  stale_multiplier: 2\n"
        )
        monkeypatch.setenv("SENTINEL_CONFIG_PATH", str(wrong_type_config))
        try:
            importlib.reload(dashboard_main)
            config = dashboard_main._load_config()
            assert config == dashboard_main._DEFAULT_CONFIG
            assert "expected config shape" in caplog.text.lower()
        finally:
            monkeypatch.undo()
            importlib.reload(dashboard_main)

    def test_intermediate_key_wrong_type_falls_back_to_defaults(self, tmp_path, monkeypatch, caplog):
        # config itself is a valid dict, but "checker" is a scalar, not a nested
        # mapping — different failure mode than the top-level scalar/list cases
        # above: this exercises the isinstance-before-.get() guard on an
        # intermediate key, not just the outermost config value.
        bad_intermediate_config = tmp_path / "settings.yaml"
        bad_intermediate_config.write_text("checker: oops\ndashboard:\n  stale_multiplier: 2\n")
        monkeypatch.setenv("SENTINEL_CONFIG_PATH", str(bad_intermediate_config))
        try:
            importlib.reload(dashboard_main)
            config = dashboard_main._load_config()
            assert config == dashboard_main._DEFAULT_CONFIG
            assert "expected config shape" in caplog.text.lower()
        finally:
            monkeypatch.undo()
            importlib.reload(dashboard_main)

    def test_malformed_yaml_does_not_500_the_dashboard(self, tmp_path, monkeypatch):
        bad_config = tmp_path / "settings.yaml"
        bad_config.write_text("checker: [unterminated flow mapping {\n")
        monkeypatch.setenv("SENTINEL_CONFIG_PATH", str(bad_config))
        try:
            importlib.reload(dashboard_main)
            # Patched post-reload — reload() re-imports read_results/get_last_checked
            # from checker.db, which would silently undo a pre-reload mock.patch.
            monkeypatch.setattr(dashboard_main, "read_results", lambda path: [])
            monkeypatch.setattr(dashboard_main, "get_last_checked", lambda path: None)
            reloaded_client = TestClient(dashboard_main.app)
            assert reloaded_client.get("/status").status_code == 200
            assert reloaded_client.get("/").status_code == 200
        finally:
            monkeypatch.undo()
            importlib.reload(dashboard_main)


class TestSecurityHeaders:
    def test_headers_present_on_root(self, mock_db):
        resp = client.get("/")
        assert resp.headers["x-frame-options"] == "DENY"
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert resp.headers["content-security-policy"] == "default-src 'self'; img-src 'self' data:"
        assert resp.headers["referrer-policy"] == "no-referrer"

    def test_headers_present_on_status(self, mock_db):
        resp = client.get("/status")
        assert resp.headers["x-frame-options"] == "DENY"
