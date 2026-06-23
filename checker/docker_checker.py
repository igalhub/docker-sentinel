import socket
import time
from datetime import datetime, timezone
from typing import Any

import docker
import docker.errors

from checker.severity import aggregate_severity, compute_severity


def _parse_docker_time(ts: str) -> datetime:
    # Docker timestamps use nanosecond precision; truncate to microseconds
    ts = ts.rstrip("Z")
    if "." in ts:
        base, frac = ts.split(".", 1)
        ts = f"{base}.{frac[:6].ljust(6, '0')}+00:00"
    else:
        ts = f"{ts}+00:00"
    return datetime.fromisoformat(ts)


def _restart_check(container: Any, config: dict) -> dict:
    try:
        attrs = container.attrs
        restart_count = attrs["RestartCount"]
        started_at = _parse_docker_time(attrs["State"]["StartedAt"])
        uptime_s = max(0.0, (datetime.now(timezone.utc) - started_at).total_seconds())

        severity = aggregate_severity([
            compute_severity("restart", restart_count, config),
            compute_severity("uptime", uptime_s, config),
        ])
        return {
            "check_type": "restart",
            "value": restart_count,
            "severity": severity,
            "detail": f"restarts={restart_count}, uptime={uptime_s:.0f}s",
        }
    except Exception as exc:
        return {
            "check_type": "restart",
            "value": None,
            "severity": "unknown",
            "error": str(exc),
            "detail": "could not read restart/uptime data",
        }


def _healthcheck_check(container: Any, config: dict) -> dict:
    try:
        state = container.attrs["State"]
        health = state.get("Health")

        if health is None:
            return {
                "check_type": "healthcheck",
                "value": "no_healthcheck",
                "severity": "warning",
                "detail": "no healthcheck defined",
            }

        status = health["Status"]
        failing_streak = health.get("FailingStreak", 0)

        if status == "starting":
            started_at = _parse_docker_time(state["StartedAt"])
            elapsed = max(0.0, (datetime.now(timezone.utc) - started_at).total_seconds())
            severity = compute_severity("healthcheck", elapsed, config)
            return {
                "check_type": "healthcheck",
                "value": status,
                "severity": severity,
                "detail": f"stuck in starting for {elapsed:.0f}s",
            }

        if status == "unhealthy":
            return {
                "check_type": "healthcheck",
                "value": status,
                "severity": "critical",
                "detail": f"unhealthy, FailingStreak={failing_streak}",
            }

        if status == "healthy":
            return {
                "check_type": "healthcheck",
                "value": status,
                "severity": "healthy",
                "detail": "healthy",
            }

        return {
            "check_type": "healthcheck",
            "value": status,
            "severity": "unknown",
            "detail": f"unrecognised health status: {status}",
        }

    except Exception as exc:
        return {
            "check_type": "healthcheck",
            "value": None,
            "severity": "unknown",
            "error": str(exc),
            "detail": "could not read healthcheck data",
        }


def _port_check(container: Any, config: dict) -> dict:
    try:
        ports = container.attrs["NetworkSettings"]["Ports"]
        # Use container ports (keys) where the port is published (bindings not None).
        # We connect to the container IP directly to bypass Docker's userland proxy,
        # which otherwise accepts all TCP connections before forwarding — making
        # socket.create_connection succeed even when nothing listens inside.
        published_ports = [
            int(port_proto.split("/")[0])
            for port_proto, bindings in ports.items()
            if bindings
        ]

        if not published_ports:
            return {
                "check_type": "port",
                "value": None,
                "severity": "unknown",
                "detail": "no published ports",
            }

        networks = container.attrs["NetworkSettings"]["Networks"]
        container_ip = next(
            (n["IPAddress"] for n in networks.values() if n.get("IPAddress")),
            None,
        )
        if not container_ip:
            return {
                "check_type": "port",
                "value": published_ports,
                "severity": "unknown",
                "detail": "could not determine container IP",
            }

        timeout_s = config["thresholds"]["port"]["timeout_seconds"]
        severities, details = [], []

        for port in published_ports:
            t0 = time.monotonic()
            try:
                with socket.create_connection((container_ip, port), timeout=timeout_s):
                    ms = (time.monotonic() - t0) * 1000
                    severities.append(compute_severity("port", ms, config))
                    details.append(f":{port} {ms:.0f}ms")
            except ConnectionRefusedError:
                severities.append("critical")
                details.append(f":{port} refused")
            except (TimeoutError, socket.timeout):
                severities.append("critical")
                details.append(f":{port} timeout")
            except OSError as exc:
                severities.append("critical")
                details.append(f":{port} error ({exc})")

        return {
            "check_type": "port",
            "value": published_ports,
            "severity": aggregate_severity(severities),
            "detail": ", ".join(details),
        }

    except Exception as exc:
        return {
            "check_type": "port",
            "value": None,
            "severity": "unknown",
            "error": str(exc),
            "detail": "could not read port data",
        }


def _log_activity_check(container: Any, config: dict) -> dict:
    try:
        warning_h = config["thresholds"]["log_silence"]["warning_hours"]
        critical_h = config["thresholds"]["log_silence"]["critical_hours"]
        now = int(datetime.now(timezone.utc).timestamp())

        if not container.logs(since=now - int(critical_h * 3600), tail=1):
            return {
                "check_type": "log_silence",
                "value": critical_h,
                "severity": "critical",
                "detail": f"no log output in last {critical_h}h",
            }

        if not container.logs(since=now - int(warning_h * 3600), tail=1):
            return {
                "check_type": "log_silence",
                "value": warning_h,
                "severity": "warning",
                "detail": f"no log output in last {warning_h}h",
            }

        return {
            "check_type": "log_silence",
            "value": 0.0,
            "severity": "healthy",
            "detail": f"log activity within last {warning_h}h",
        }

    except Exception as exc:
        return {
            "check_type": "log_silence",
            "value": None,
            "severity": "unknown",
            "error": str(exc),
            "detail": "could not read log data",
        }


def check_container(container: Any, config: dict) -> dict:
    checks = {
        "restart": _restart_check(container, config),
        "healthcheck": _healthcheck_check(container, config),
        "port": _port_check(container, config),
        "log_silence": _log_activity_check(container, config),
    }
    image = (
        container.image.tags[0]
        if container.image and container.image.tags
        else container.attrs["Config"]["Image"]
    )
    return {
        "container_id": container.id,
        "name": container.name,
        "image": image,
        "status": container.status,
        "checks": checks,
        "severity": aggregate_severity([c["severity"] for c in checks.values()]),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def check_all(client: docker.DockerClient, config: dict) -> list[dict]:
    try:
        containers = client.containers.list()
    except docker.errors.APIError:
        return []
    return [check_container(c, config) for c in containers]
