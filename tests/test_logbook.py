"""logbook — the per-run diagnostics file (`runs/<run_id>/kiln-harness-tuner.log`).

The root logger is restored around every test by conftest's autouse
`isolate_root_logger`: pytest shares one process, so a leaked FileHandler on a
tmp_path that later disappears turns unrelated tests into logging errors.
"""

import logging

import pytest

from kiln_harness_tuner.logbook import (
    DEFAULT_LEVEL,
    LOG_FILE_NAME,
    configure_run_logging,
    log_path,
    resolve_level,
    run_log_configured,
)


def test_configure_writes_lines_into_the_run_dir(tmp_path):
    path = configure_run_logging(tmp_path / "260814-1200")
    assert path == log_path(tmp_path / "260814-1200")
    assert path.name == LOG_FILE_NAME

    logging.getLogger("kiln_harness_tuner.dispatch.claude_cli").info(
        "dispatch w1-1: spawning"
    )
    logging.getLogger("httpx").warning("connection reset")

    text = path.read_text()
    # timestamp · level · logger name · message — the file's whole structure.
    assert (
        "INFO    kiln_harness_tuner.dispatch.claude_cli dispatch w1-1: spawning" in text
    )
    assert "WARNING httpx connection reset" in text
    # A library that never heard of kiln-harness-tuner still lands in the file — the
    # point of attaching at the ROOT.
    assert text.count("\n") == 2


def test_debug_is_filtered_out_by_default_and_in_when_asked(tmp_path):
    path = configure_run_logging(tmp_path / "info")
    logging.getLogger("kiln_harness_tuner.x").debug("noisy")
    assert path.read_text() == ""

    verbose = configure_run_logging(tmp_path / "debug", level="DEBUG")
    logging.getLogger("kiln_harness_tuner.x").debug("noisy")
    assert "DEBUG   kiln_harness_tuner.x noisy" in verbose.read_text()


def test_reconfiguring_replaces_the_handler_rather_than_stacking(tmp_path):
    first = configure_run_logging(tmp_path / "one")
    second = configure_run_logging(tmp_path / "two")
    logging.getLogger("kiln_harness_tuner.x").info("only once")
    assert first.read_text() == ""
    assert second.read_text().count("only once") == 1
    root = logging.getLogger()
    assert (
        sum(
            getattr(h, "name", "") == "kiln-harness-tuner-run-log"
            for h in root.handlers
        )
        == 1
    )


def test_configure_appends_so_a_rerun_keeps_the_first_attempt(tmp_path):
    run_dir = tmp_path / "260814-1200"
    configure_run_logging(run_dir)
    logging.getLogger("kiln_harness_tuner.x").info("attempt one")
    configure_run_logging(run_dir)
    logging.getLogger("kiln_harness_tuner.x").info("attempt two")
    text = log_path(run_dir).read_text()
    assert "attempt one" in text and "attempt two" in text


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, DEFAULT_LEVEL),
        ("", DEFAULT_LEVEL),
        ("DEBUG", logging.DEBUG),
        ("debug", logging.DEBUG),
        ("WARNING", logging.WARNING),
        ("10", 10),
        # A typo must not kill a run whose whole point is to log what broke.
        ("LOUDER", DEFAULT_LEVEL),
    ],
)
def test_resolve_level(raw, expected):
    assert resolve_level(raw) == expected


def test_resolve_level_reads_the_env_var(tmp_path, monkeypatch):
    monkeypatch.setenv("KILN_HARNESS_TUNER_LOG_LEVEL", "DEBUG")
    path = configure_run_logging(tmp_path / "env")
    logging.getLogger("kiln_harness_tuner.x").debug("from the env")
    assert "from the env" in path.read_text()


def test_run_log_configured_says_whether_the_run_log_is_on_the_root_logger(tmp_path):
    assert not run_log_configured()
    configure_run_logging(tmp_path)
    assert run_log_configured()
