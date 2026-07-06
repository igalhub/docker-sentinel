import sqlite3

import pytest

import checker.db as db
from checker.db import get_last_checked, init_db, read_results, write_results

DB = ":memory:"

SAMPLE = {
    "container_id": "abc123",
    "name": "web",
    "image": "nginx:alpine",
    "status": "running",
    "checks": {
        "restart": {"check_type": "restart", "value": 0, "severity": "healthy", "detail": "restarts=0, uptime=3600s"},
        "healthcheck": {"check_type": "healthcheck", "value": "no_healthcheck", "severity": "warning", "detail": "no healthcheck defined"},
        "port": {"check_type": "port", "value": None, "severity": "unknown", "detail": "no published ports"},
        "log_silence": {"check_type": "log_silence", "value": 0.0, "severity": "healthy", "detail": "log activity within last 2h"},
    },
    "severity": "warning",
    "checked_at": "2026-06-24T00:00:00+00:00",
}

SAMPLE2 = {**SAMPLE, "container_id": "def456", "name": "db", "severity": "healthy",
           "checked_at": "2026-06-24T01:00:00+00:00"}


@pytest.fixture(autouse=True)
def fresh_db():
    init_db(DB)
    yield
    db._close_cached(DB)


class TestConnectionLifecycle:
    def test_file_based_connection_closes_after_each_call(self, tmp_path):
        path = str(tmp_path / "results.db")
        init_db(path)
        write_results(path, [SAMPLE])

        with db._connect(path) as conn:
            leaked_conn = conn
        with pytest.raises(sqlite3.ProgrammingError):
            leaked_conn.execute("SELECT 1")

    def test_memory_connection_stays_open_across_calls(self):
        init_db(DB)
        write_results(DB, [SAMPLE])
        # A second call must see the first call's data — proving the
        # cached :memory: connection was not closed in between.
        assert len(read_results(DB)) == 1


class TestInitDb:
    def test_creates_table(self):
        conn = db._get_conn(DB)
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='container_checks'"
        ).fetchone()
        assert tables is not None

    def test_idempotent(self):
        init_db(DB)  # second call — must not raise
        conn = db._get_conn(DB)
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='container_checks'"
        ).fetchall()
        assert len(tables) == 1


class TestWriteAndReadResults:
    def test_write_then_read_returns_one_row(self):
        write_results(DB, [SAMPLE])
        rows = read_results(DB)
        assert len(rows) == 1

    def test_read_preserves_all_fields(self):
        write_results(DB, [SAMPLE])
        row = read_results(DB)[0]
        assert row["container_id"] == SAMPLE["container_id"]
        assert row["name"] == SAMPLE["name"]
        assert row["image"] == SAMPLE["image"]
        assert row["status"] == SAMPLE["status"]
        assert row["severity"] == SAMPLE["severity"]
        assert row["checked_at"] == SAMPLE["checked_at"]

    def test_checks_field_is_dict_not_string(self):
        write_results(DB, [SAMPLE])
        row = read_results(DB)[0]
        assert isinstance(row["checks"], dict)
        assert "restart" in row["checks"]

    def test_write_multiple_containers(self):
        write_results(DB, [SAMPLE, SAMPLE2])
        rows = read_results(DB)
        assert len(rows) == 2

    def test_write_empty_list(self):
        write_results(DB, [])
        assert read_results(DB) == []


class TestUpsert:
    def test_upsert_updates_existing_row(self):
        write_results(DB, [SAMPLE])
        updated = {**SAMPLE, "severity": "critical", "checked_at": "2026-06-24T01:00:00+00:00"}
        write_results(DB, [updated])
        rows = read_results(DB)
        assert len(rows) == 1
        assert rows[0]["severity"] == "critical"
        assert rows[0]["checked_at"] == "2026-06-24T01:00:00+00:00"

    def test_upsert_does_not_duplicate(self):
        write_results(DB, [SAMPLE])
        write_results(DB, [SAMPLE])
        assert len(read_results(DB)) == 1


class TestGetLastChecked:
    def test_returns_none_when_empty(self):
        assert get_last_checked(DB) is None

    def test_returns_datetime_after_write(self):
        write_results(DB, [SAMPLE])
        result = get_last_checked(DB)
        assert result is not None
        assert result.year == 2026

    def test_returns_latest_timestamp(self):
        write_results(DB, [SAMPLE, SAMPLE2])
        result = get_last_checked(DB)
        # SAMPLE2 has checked_at 2026-06-24T01:00:00, SAMPLE has 00:00:00
        assert result.hour == 1
