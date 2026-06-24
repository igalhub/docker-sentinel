import json
import sqlite3
from datetime import datetime

# In-memory connections must be cached; the DB is destroyed when its last
# connection closes, so file-per-call semantics don't work for :memory:.
_memory_conns: dict[str, sqlite3.Connection] = {}


def _get_conn(path: str) -> sqlite3.Connection:
    if path == ":memory:":
        if path not in _memory_conns:
            _memory_conns[path] = sqlite3.connect(path)
        return _memory_conns[path]
    return sqlite3.connect(path)


def _close_cached(path: str) -> None:
    """Close and discard a cached connection. Call in test teardown to reset state."""
    if path in _memory_conns:
        _memory_conns.pop(path).close()


def init_db(path: str) -> None:
    conn = _get_conn(path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS container_checks (
            name         TEXT PRIMARY KEY,
            container_id TEXT NOT NULL,
            image        TEXT NOT NULL,
            status       TEXT NOT NULL,
            checks       TEXT NOT NULL,
            severity     TEXT NOT NULL,
            checked_at   TEXT NOT NULL
        )
    """)
    conn.commit()


def write_results(path: str, results: list[dict]) -> None:
    conn = _get_conn(path)
    for r in results:
        conn.execute(
            """
            INSERT OR REPLACE INTO container_checks
                (name, container_id, image, status, checks, severity, checked_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                r["name"],
                r["container_id"],
                r["image"],
                r["status"],
                json.dumps(r["checks"]),
                r["severity"],
                r["checked_at"],
            ),
        )
    conn.commit()


def read_results(path: str) -> list[dict]:
    conn = _get_conn(path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM container_checks ORDER BY checked_at DESC"
    ).fetchall()
    return [
        {
            "container_id": row["container_id"],
            "name": row["name"],
            "image": row["image"],
            "status": row["status"],
            "checks": json.loads(row["checks"]),
            "severity": row["severity"],
            "checked_at": row["checked_at"],
        }
        for row in rows
    ]


def get_last_checked(path: str) -> datetime | None:
    conn = _get_conn(path)
    row = conn.execute("SELECT MAX(checked_at) FROM container_checks").fetchone()
    if row and row[0]:
        return datetime.fromisoformat(row[0])
    return None
