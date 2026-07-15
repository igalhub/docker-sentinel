import importlib
import logging
from unittest.mock import MagicMock, patch

import docker.errors
import pytest

import checker.check as checker_check


@pytest.fixture
def reloaded_check(tmp_path, monkeypatch):
    """Points checker.check at real tmp env paths via reload; caller patches Docker/IO seams."""
    config_path = tmp_path / "settings.yaml"
    config_path.write_text("checker:\n  interval_seconds: 300\n")
    db_path = str(tmp_path / "results.db")

    monkeypatch.setenv("SENTINEL_DB_PATH", db_path)
    monkeypatch.setenv("SENTINEL_CONFIG_PATH", str(config_path))
    try:
        importlib.reload(checker_check)
        yield checker_check, db_path
    finally:
        monkeypatch.undo()
        importlib.reload(checker_check)


def test_main_happy_path_writes_results_and_logs(reloaded_check, caplog):
    caplog.set_level(logging.INFO)
    mod, db_path = reloaded_check
    fake_client = MagicMock()
    fake_results = [{"name": "web", "severity": "healthy"}]

    with (
        patch("checker.check.docker.from_env", return_value=fake_client) as mock_from_env,
        patch("checker.check.check_all", return_value=fake_results) as mock_check_all,
        patch("checker.check.init_db") as mock_init_db,
        patch("checker.check.write_results") as mock_write_results,
    ):
        mod.main()

    mock_from_env.assert_called_once()
    mock_init_db.assert_called_once_with(db_path)
    mock_check_all.assert_called_once_with(fake_client, {"checker": {"interval_seconds": 300}})
    mock_write_results.assert_called_once_with(db_path, fake_results)
    assert "Checked 1 containers" in caplog.text


def test_main_docker_exception_falls_back_to_empty_results(reloaded_check, caplog):
    caplog.set_level(logging.INFO)
    mod, db_path = reloaded_check

    with (
        patch("checker.check.docker.from_env", side_effect=docker.errors.DockerException("boom")),
        patch("checker.check.check_all") as mock_check_all,
        patch("checker.check.init_db") as mock_init_db,
        patch("checker.check.write_results") as mock_write_results,
    ):
        mod.main()

    mock_check_all.assert_not_called()
    mock_init_db.assert_called_once_with(db_path)
    mock_write_results.assert_called_once_with(db_path, [])
    assert "Docker connection failed" in caplog.text


def test_main_missing_config_exits_1_without_writing(tmp_path, monkeypatch):
    db_path = str(tmp_path / "results.db")
    missing_config = tmp_path / "nonexistent.yaml"

    monkeypatch.setenv("SENTINEL_DB_PATH", db_path)
    monkeypatch.setenv("SENTINEL_CONFIG_PATH", str(missing_config))
    try:
        importlib.reload(checker_check)
        with (
            patch("checker.check.init_db") as mock_init_db,
            patch("checker.check.write_results") as mock_write_results,
        ):
            with pytest.raises(SystemExit) as exc_info:
                checker_check.main()
        assert exc_info.value.code == 1
        mock_init_db.assert_not_called()
        mock_write_results.assert_not_called()
    finally:
        monkeypatch.undo()
        importlib.reload(checker_check)
