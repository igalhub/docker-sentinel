_SEVERITY_ORDER = {"critical": 3, "warning": 2, "unknown": 1, "healthy": 0}


def compute_severity(check_type: str, value: float | str | None, config: dict) -> str:
    if value is None:
        return "unknown"

    t = config["thresholds"]

    if check_type == "restart":
        if value > t["restart"]["critical"]:
            return "critical"
        if value > t["restart"]["warning"]:
            return "warning"
        return "healthy"

    if check_type == "uptime":
        if value < t["uptime"]["critical_seconds"]:
            return "critical"
        if value < t["uptime"]["warning_seconds"]:
            return "warning"
        return "healthy"

    if check_type == "healthcheck":
        if value == "unhealthy":
            return "critical"
        if value == "healthy":
            return "healthy"
        if value == "no_healthcheck":
            return "warning"
        if isinstance(value, (int, float)):
            # float encodes elapsed seconds since container entered "starting" state
            if value > t["healthcheck"]["starting_warning_seconds"]:
                return "warning"
            return "healthy"
        return "unknown"

    if check_type == "port":
        if value in ("refused", "timeout"):
            return "critical"
        if isinstance(value, (int, float)):
            if value > t["port"]["warning_ms"]:
                return "warning"
            return "healthy"
        return "unknown"

    if check_type == "log_silence":
        if value > t["log_silence"]["critical_hours"]:
            return "critical"
        if value > t["log_silence"]["warning_hours"]:
            return "warning"
        return "healthy"

    return "unknown"


def aggregate_severity(severities: list[str]) -> str:
    if not severities:
        return "unknown"  # no data is ambiguous, not healthy
    return max(severities, key=lambda s: _SEVERITY_ORDER.get(s, -1))
