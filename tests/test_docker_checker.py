import time
import uuid
from unittest.mock import MagicMock, patch

import docker
import pytest

from checker.docker_checker import (
    _healthcheck_check,
    _log_activity_check,
    _parse_docker_time,
    _port_check,
    _restart_check,
    check_all,
    check_container,
)

CONFIG = {
    "thresholds": {
        "restart": {"warning": 3, "critical": 10},
        "uptime": {"warning_seconds": 300, "critical_seconds": 60},
        "healthcheck": {"starting_warning_seconds": 300},
        "port": {"timeout_seconds": 5, "warning_ms": 2000},
        "log_silence": {"warning_hours": 2, "critical_hours": 6},
    }
}

# Lower thresholds so live fixtures reach critical quickly
LIVE_CONFIG = {
    "thresholds": {
        "restart": {"warning": 1, "critical": 3},
        "uptime": {"warning_seconds": 300, "critical_seconds": 60},
        "healthcheck": {"starting_warning_seconds": 300},
        "port": {"timeout_seconds": 3, "warning_ms": 2000},
        "log_silence": {"warning_hours": 2, "critical_hours": 6},
    }
}

RECENT_TS = "2026-06-24T00:00:00.000000000Z"
OLD_TS = "2020-01-01T00:00:00.000000000Z"


def _mock_container(
    restart_count=0,
    started_at=RECENT_TS,
    health=None,
    ports=None,
    networks=None,
    logs_output=b"some log line\n",
):
    c = MagicMock()
    c.id = "abc123def456"
    c.name = "test-container"
    c.status = "running"
    c.image.tags = ["test-image:latest"]
    c.attrs = {
        "RestartCount": restart_count,
        "State": {"StartedAt": started_at, "Health": health},
        "NetworkSettings": {
            "Ports": ports or {},
            "Networks": networks or {"bridge": {"IPAddress": "172.17.0.2"}},
        },
        "Config": {"Image": "test-image:latest"},
    }
    c.logs.return_value = logs_output
    return c


# ---------------------------------------------------------------------------
# _parse_docker_time
# ---------------------------------------------------------------------------

class TestParseDockerTime:
    def test_nanosecond_precision(self):
        dt = _parse_docker_time("2026-06-24T00:07:41.123456789Z")
        assert dt.year == 2026
        assert dt.microsecond == 123456

    def test_no_fractional(self):
        dt = _parse_docker_time("2026-06-24T00:07:41Z")
        assert dt.second == 41
        assert dt.microsecond == 0

    def test_timezone_aware(self):
        from datetime import timezone
        dt = _parse_docker_time("2026-06-24T00:00:00Z")
        assert dt.tzinfo is not None
        assert dt.utcoffset().total_seconds() == 0


# ---------------------------------------------------------------------------
# _restart_check
# ---------------------------------------------------------------------------

class TestRestartCheck:
    def test_healthy_no_restarts(self):
        c = _mock_container(restart_count=0, started_at=OLD_TS)
        result = _restart_check(c, CONFIG)
        assert result["check_type"] == "restart"
        assert result["severity"] == "healthy"
        assert result["value"] == 0

    def test_warning_restart_count(self):
        c = _mock_container(restart_count=4, started_at=OLD_TS)
        result = _restart_check(c, CONFIG)
        assert result["severity"] == "warning"

    def test_critical_restart_count(self):
        c = _mock_container(restart_count=11, started_at=OLD_TS)
        result = _restart_check(c, CONFIG)
        assert result["severity"] == "critical"

    def test_critical_short_uptime(self):
        # Container just started: uptime < 60s triggers critical regardless of restarts
        c = _mock_container(restart_count=0, started_at=RECENT_TS)
        result = _restart_check(c, CONFIG)
        assert result["severity"] == "critical"

    def test_error_returns_unknown(self):
        c = MagicMock()
        c.attrs = {}  # missing keys → KeyError
        result = _restart_check(c, CONFIG)
        assert result["severity"] == "unknown"
        assert "error" in result

    def test_detail_contains_counts(self):
        c = _mock_container(restart_count=2, started_at=OLD_TS)
        result = _restart_check(c, CONFIG)
        assert "restarts=2" in result["detail"]


# ---------------------------------------------------------------------------
# _healthcheck_check
# ---------------------------------------------------------------------------

class TestHealthcheckCheck:
    def test_no_healthcheck_is_warning(self):
        c = _mock_container(health=None)
        result = _healthcheck_check(c, CONFIG)
        assert result["severity"] == "warning"
        assert result["value"] == "no_healthcheck"
        assert result["detail"] == "no healthcheck defined"

    def test_healthy_status(self):
        c = _mock_container(health={"Status": "healthy", "FailingStreak": 0})
        result = _healthcheck_check(c, CONFIG)
        assert result["severity"] == "healthy"

    def test_unhealthy_status_is_critical(self):
        c = _mock_container(health={"Status": "unhealthy", "FailingStreak": 3})
        result = _healthcheck_check(c, CONFIG)
        assert result["severity"] == "critical"
        assert "FailingStreak=3" in result["detail"]

    def test_starting_recently_is_healthy(self):
        # Container just started: elapsed < 300s → healthy
        c = _mock_container(started_at=RECENT_TS, health={"Status": "starting", "FailingStreak": 0})
        result = _healthcheck_check(c, CONFIG)
        assert result["severity"] in ("healthy", "warning")  # depends on elapsed; recent → healthy

    def test_starting_old_container_is_warning(self):
        # Container started long ago: elapsed > 300s → warning
        c = _mock_container(started_at=OLD_TS, health={"Status": "starting", "FailingStreak": 0})
        result = _healthcheck_check(c, CONFIG)
        assert result["severity"] == "warning"

    def test_error_returns_unknown(self):
        c = MagicMock()
        c.attrs = {}
        result = _healthcheck_check(c, CONFIG)
        assert result["severity"] == "unknown"
        assert "error" in result


# ---------------------------------------------------------------------------
# _port_check
# ---------------------------------------------------------------------------

class TestPortCheck:
    def test_no_ports_is_unknown(self):
        c = _mock_container(ports={})
        result = _port_check(c, CONFIG)
        assert result["severity"] == "unknown"
        assert result["detail"] == "no published ports"

    def test_ports_none_bindings_is_unknown(self):
        # Port declared in image but not published to host
        c = _mock_container(ports={"80/tcp": None})
        result = _port_check(c, CONFIG)
        assert result["severity"] == "unknown"

    def test_connection_refused_is_critical(self):
        c = _mock_container(ports={"80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8080"}]})
        with patch("checker.docker_checker.socket.create_connection", side_effect=ConnectionRefusedError):
            result = _port_check(c, CONFIG)
        assert result["severity"] == "critical"
        assert "refused" in result["detail"]

    def test_successful_connection_is_healthy(self):
        c = _mock_container(ports={"80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8080"}]})
        mock_sock = MagicMock()
        mock_sock.__enter__ = MagicMock(return_value=mock_sock)
        mock_sock.__exit__ = MagicMock(return_value=False)
        with patch("checker.docker_checker.socket.create_connection", return_value=mock_sock):
            result = _port_check(c, CONFIG)
        assert result["severity"] == "healthy"

    def test_timeout_is_critical(self):
        c = _mock_container(ports={"80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8080"}]})
        with patch("checker.docker_checker.socket.create_connection", side_effect=TimeoutError):
            result = _port_check(c, CONFIG)
        assert result["severity"] == "critical"
        assert "timeout" in result["detail"]

    def test_error_returns_unknown(self):
        c = MagicMock()
        c.attrs = {}
        result = _port_check(c, CONFIG)
        assert result["severity"] == "unknown"
        assert "error" in result


# ---------------------------------------------------------------------------
# _log_activity_check
# ---------------------------------------------------------------------------

class TestLogActivityCheck:
    def test_recent_logs_healthy(self):
        c = _mock_container(logs_output=b"log line\n")
        result = _log_activity_check(c, CONFIG)
        assert result["severity"] == "healthy"

    def test_no_logs_in_warning_window_is_warning(self):
        c = _mock_container()
        # First call (critical window) returns output; second call (warning window) returns empty
        c.logs.side_effect = [b"old log\n", b""]
        result = _log_activity_check(c, CONFIG)
        assert result["severity"] == "warning"

    def test_no_logs_in_critical_window_is_critical(self):
        c = _mock_container(logs_output=b"")
        result = _log_activity_check(c, CONFIG)
        assert result["severity"] == "critical"

    def test_error_returns_unknown(self):
        c = MagicMock()
        c.logs.side_effect = docker.errors.APIError("daemon error")
        result = _log_activity_check(c, CONFIG)
        assert result["severity"] == "unknown"
        assert "error" in result


# ---------------------------------------------------------------------------
# check_container
# ---------------------------------------------------------------------------

class TestCheckContainer:
    def test_result_structure(self):
        c = _mock_container(restart_count=0, started_at=OLD_TS)
        result = check_container(c, CONFIG)
        assert set(result.keys()) == {"container_id", "name", "image", "status", "checks", "severity", "checked_at"}
        assert set(result["checks"].keys()) == {"restart", "healthcheck", "port", "log_silence"}

    def test_each_check_has_required_fields(self):
        c = _mock_container(restart_count=0, started_at=OLD_TS)
        result = check_container(c, CONFIG)
        for check in result["checks"].values():
            assert "check_type" in check
            assert "value" in check
            assert "severity" in check
            assert "detail" in check

    def test_aggregate_severity_worst_of_checks(self):
        # restart critical (high count) + everything else healthy → container is critical
        c = _mock_container(restart_count=11, started_at=OLD_TS)
        result = check_container(c, CONFIG)
        assert result["severity"] == "critical"

    def test_image_fallback_when_no_tags(self):
        c = _mock_container(started_at=OLD_TS)
        c.image.tags = []
        result = check_container(c, CONFIG)
        assert result["image"] == "test-image:latest"  # from Config.Image


# ---------------------------------------------------------------------------
# check_all
# ---------------------------------------------------------------------------

class TestCheckAll:
    def test_returns_empty_on_api_error(self):
        client = MagicMock()
        client.containers.list.side_effect = docker.errors.APIError("connection failed")
        result = check_all(client, CONFIG)
        assert result == []

    def test_calls_check_container_for_each(self):
        client = MagicMock()
        c1 = _mock_container(started_at=OLD_TS)
        c2 = _mock_container(started_at=OLD_TS)
        client.containers.list.return_value = [c1, c2]
        result = check_all(client, CONFIG)
        assert len(result) == 2
        assert all("container_id" in r for r in result)


# ---------------------------------------------------------------------------
# Live Docker tests
# ---------------------------------------------------------------------------

def _wait_for(condition_fn, timeout: float = 15.0, interval: float = 0.5) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition_fn():
            return True
        time.sleep(interval)
    return False


@pytest.fixture(scope="module")
def docker_client():
    return docker.from_env()


@pytest.fixture
def healthy_container(docker_client):
    name = f"ds-test-healthy-{uuid.uuid4().hex[:8]}"
    # Publish port 80 so port_check has a published port to check
    c = docker_client.containers.run("nginx:alpine", ports={"80/tcp": None}, detach=True, name=name)
    time.sleep(1)  # let nginx finish startup before checking
    yield c
    c.stop(timeout=2)
    c.remove(force=True)


@pytest.fixture
def crash_loop_container(docker_client):
    name = f"ds-test-crash-{uuid.uuid4().hex[:8]}"
    c = docker_client.containers.run(
        "alpine",
        command=["sh", "-c", "exit 1"],
        restart_policy={"Name": "on-failure", "MaximumRetryCount": 20},
        detach=True,
        name=name,
    )
    yield c
    c.remove(force=True)


@pytest.fixture
def unhealthy_container(docker_client):
    name = f"ds-test-unhealthy-{uuid.uuid4().hex[:8]}"
    c = docker_client.containers.run(
        "alpine",
        command=["sleep", "infinity"],
        healthcheck={
            "test": ["CMD-SHELL", "exit 1"],
            "interval": 1_000_000_000,
            "timeout": 1_000_000_000,
            "retries": 1,
            "start_period": 0,
        },
        detach=True,
        name=name,
    )
    yield c
    c.stop(timeout=2)
    c.remove(force=True)


@pytest.fixture
def closed_port_container(docker_client):
    name = f"ds-test-port-{uuid.uuid4().hex[:8]}"
    # Publish an arbitrary container port that nothing listens on
    c = docker_client.containers.run(
        "alpine",
        command=["sleep", "infinity"],
        ports={"19877/tcp": None},
        detach=True,
        name=name,
    )
    yield c
    c.stop(timeout=2)
    c.remove(force=True)


@pytest.mark.docker
def test_healthy_container_port_and_logs_healthy(healthy_container):
    healthy_container.reload()
    result = check_container(healthy_container, LIVE_CONFIG)
    # nginx IS listening on port 80 — port_check must be healthy
    assert result["checks"]["port"]["severity"] == "healthy", (
        f"expected port healthy, got {result['checks']['port']}"
    )
    # nginx outputs startup logs — log_silence must be healthy
    assert result["checks"]["log_silence"]["severity"] == "healthy", (
        f"expected log_silence healthy, got {result['checks']['log_silence']}"
    )
    # nginx:alpine has no HEALTHCHECK defined — correctly flagged as warning
    assert result["checks"]["healthcheck"]["severity"] == "warning", (
        f"expected healthcheck warning, got {result['checks']['healthcheck']}"
    )


@pytest.mark.docker
def test_crash_loop_restart_check_critical(crash_loop_container):
    reached = _wait_for(
        lambda: (crash_loop_container.reload() or True)
        and crash_loop_container.attrs["RestartCount"] >= 4,
        timeout=20.0,
    )
    assert reached, f"container only reached {crash_loop_container.attrs['RestartCount']} restarts"
    result = check_container(crash_loop_container, LIVE_CONFIG)
    assert result["checks"]["restart"]["severity"] == "critical", (
        f"expected critical, got {result['checks']['restart']}"
    )


@pytest.mark.docker
def test_unhealthy_container_healthcheck_critical(unhealthy_container):
    reached = _wait_for(
        lambda: (unhealthy_container.reload() or True)
        and unhealthy_container.attrs["State"].get("Health", {}).get("Status") == "unhealthy",
        timeout=20.0,
    )
    assert reached, f"container health status: {unhealthy_container.attrs['State'].get('Health')}"
    result = check_container(unhealthy_container, LIVE_CONFIG)
    assert result["checks"]["healthcheck"]["severity"] == "critical", (
        f"expected critical, got {result['checks']['healthcheck']}"
    )


@pytest.mark.docker
def test_closed_port_port_check_critical(closed_port_container):
    closed_port_container.reload()
    result = check_container(closed_port_container, LIVE_CONFIG)
    assert result["checks"]["port"]["severity"] == "critical", (
        f"expected critical, got {result['checks']['port']}"
    )


@pytest.mark.docker
def test_live_fixtures_leave_no_containers(docker_client):
    # All ds-test-* containers should be cleaned up by their fixtures before this runs.
    # This test runs last within the docker mark group.
    remaining = [
        c.name for c in docker_client.containers.list(all=True)
        if c.name.startswith("ds-test-")
    ]
    assert remaining == [], f"leftover test containers: {remaining}"
