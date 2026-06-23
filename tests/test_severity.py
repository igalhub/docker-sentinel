from checker.severity import aggregate_severity, compute_severity

CONFIG = {
    "thresholds": {
        "restart": {"warning": 3, "critical": 10},
        "uptime": {"warning_seconds": 300, "critical_seconds": 60},
        "healthcheck": {"starting_warning_seconds": 300},
        "port": {"timeout_seconds": 5, "warning_ms": 2000},
        "log_silence": {"warning_hours": 2, "critical_hours": 6},
    }
}


class TestRestartSeverity:
    def test_at_warning_boundary_is_healthy(self):
        assert compute_severity("restart", 3, CONFIG) == "healthy"

    def test_just_over_warning(self):
        assert compute_severity("restart", 4, CONFIG) == "warning"

    def test_at_critical_boundary_is_warning(self):
        assert compute_severity("restart", 10, CONFIG) == "warning"

    def test_just_over_critical(self):
        assert compute_severity("restart", 11, CONFIG) == "critical"

    def test_zero(self):
        assert compute_severity("restart", 0, CONFIG) == "healthy"

    def test_none(self):
        assert compute_severity("restart", None, CONFIG) == "unknown"


class TestUptimeSeverity:
    def test_at_warning_boundary_is_healthy(self):
        assert compute_severity("uptime", 300, CONFIG) == "healthy"

    def test_just_below_warning(self):
        assert compute_severity("uptime", 299, CONFIG) == "warning"

    def test_at_critical_boundary_is_warning(self):
        assert compute_severity("uptime", 60, CONFIG) == "warning"

    def test_just_below_critical(self):
        assert compute_severity("uptime", 59, CONFIG) == "critical"

    def test_zero(self):
        assert compute_severity("uptime", 0, CONFIG) == "critical"

    def test_none(self):
        assert compute_severity("uptime", None, CONFIG) == "unknown"


class TestHealthcheckSeverity:
    def test_healthy_status(self):
        assert compute_severity("healthcheck", "healthy", CONFIG) == "healthy"

    def test_unhealthy_status(self):
        assert compute_severity("healthcheck", "unhealthy", CONFIG) == "critical"

    def test_no_healthcheck_defined(self):
        assert compute_severity("healthcheck", "no_healthcheck", CONFIG) == "warning"

    def test_none(self):
        assert compute_severity("healthcheck", None, CONFIG) == "unknown"

    def test_starting_at_threshold_is_healthy(self):
        assert compute_severity("healthcheck", 300, CONFIG) == "healthy"

    def test_starting_just_over_threshold(self):
        assert compute_severity("healthcheck", 301, CONFIG) == "warning"

    def test_starting_zero_elapsed(self):
        assert compute_severity("healthcheck", 0, CONFIG) == "healthy"


class TestPortSeverity:
    def test_refused(self):
        assert compute_severity("port", "refused", CONFIG) == "critical"

    def test_timeout(self):
        assert compute_severity("port", "timeout", CONFIG) == "critical"

    def test_at_warning_boundary_is_healthy(self):
        assert compute_severity("port", 2000, CONFIG) == "healthy"

    def test_just_over_warning(self):
        assert compute_severity("port", 2001, CONFIG) == "warning"

    def test_fast_response(self):
        assert compute_severity("port", 100, CONFIG) == "healthy"

    def test_none(self):
        assert compute_severity("port", None, CONFIG) == "unknown"


class TestLogSilenceSeverity:
    def test_at_warning_boundary_is_healthy(self):
        assert compute_severity("log_silence", 2.0, CONFIG) == "healthy"

    def test_just_over_warning(self):
        assert compute_severity("log_silence", 2.1, CONFIG) == "warning"

    def test_at_critical_boundary_is_warning(self):
        assert compute_severity("log_silence", 6.0, CONFIG) == "warning"

    def test_just_over_critical(self):
        assert compute_severity("log_silence", 6.1, CONFIG) == "critical"

    def test_no_silence(self):
        assert compute_severity("log_silence", 0.0, CONFIG) == "healthy"

    def test_none(self):
        assert compute_severity("log_silence", None, CONFIG) == "unknown"


class TestAggregateSeverity:
    def test_empty_returns_unknown(self):
        assert aggregate_severity([]) == "unknown"

    def test_all_healthy(self):
        assert aggregate_severity(["healthy", "healthy"]) == "healthy"

    def test_critical_dominates(self):
        assert aggregate_severity(["healthy", "critical", "warning"]) == "critical"

    def test_warning_over_healthy(self):
        assert aggregate_severity(["healthy", "warning"]) == "warning"

    def test_unknown_over_healthy(self):
        assert aggregate_severity(["healthy", "unknown"]) == "unknown"

    def test_critical_over_unknown(self):
        assert aggregate_severity(["unknown", "critical"]) == "critical"

    def test_single_healthy(self):
        assert aggregate_severity(["healthy"]) == "healthy"

    def test_single_critical(self):
        assert aggregate_severity(["critical"]) == "critical"


class TestThresholdsAreNotHardcoded:
    def test_custom_restart_warning_threshold(self):
        custom = {
            "thresholds": {
                "restart": {"warning": 1, "critical": 5},
                "uptime": {"warning_seconds": 300, "critical_seconds": 60},
                "healthcheck": {"starting_warning_seconds": 300},
                "port": {"timeout_seconds": 5, "warning_ms": 2000},
                "log_silence": {"warning_hours": 2, "critical_hours": 6},
            }
        }
        # RestartCount=2 is healthy under default config (threshold 3),
        # but warning under custom config (threshold 1)
        assert compute_severity("restart", 2, CONFIG) == "healthy"
        assert compute_severity("restart", 2, custom) == "warning"
