"""Automated test suite for agentctl status and agentctl logs CLI commands."""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
import pytest

from agentctl.cli import format_duration, parse_since, query_daemon_status
from agentctl.config import REPO_ROOT


def test_parse_since():
    assert parse_since("30s") == 30.0
    assert parse_since("10m") == 600.0
    assert parse_since("2h") == 7200.0
    assert parse_since("1d") == 86400.0

    with pytest.raises(ValueError):
        parse_since("invalid")


def test_format_duration():
    assert format_duration(15.2) == "15.2s"
    assert format_duration(65) == "1m 5s"
    assert format_duration(3665) == "1h 1m"


def test_status_when_daemon_stopped():
    """When daemon is not running, agentctl status must exit with code 3."""
    res = subprocess.run(
        [sys.executable, "-m", "agentctl.cli", "status"],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 3
    assert "UNKNOWN" in res.stdout or "not answering" in res.stdout


def test_status_json_when_daemon_stopped():
    """When daemon is not running, agentctl status --json outputs JSON with code 3."""
    res = subprocess.run(
        [sys.executable, "-m", "agentctl.cli", "status", "--json"],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 3
    data = json.loads(res.stdout)
    assert data["status"] == "stopped"
    assert data["code"] == 3


def test_logs_filtering(tmp_path, monkeypatch):
    """Verifies level, component, and turn filtering in agentctl logs."""
    monkeypatch.setenv("AGENTCTL_LOG_DIR", str(tmp_path))

    daemon_log = tmp_path / "daemon.log"
    events_jsonl = tmp_path / "events.jsonl"

    daemon_log.write_text(
        "2026-11-05 16:26:01.000 DEBUG daemon  Process start\n"
        "2026-11-05 16:26:02.000 INFO  stt     Speech detected | turn=1\n"
        "2026-11-05 16:26:03.000 WARN  llm     Retrying endpoint | turn=1\n"
        "2026-11-05 16:26:04.000 ERROR session API failure | turn=2\n"
    )

    events_jsonl.write_text(
        json.dumps({"timestamp": "2026-11-05T16:26:01.000Z", "level": "debug", "component": "daemon", "message": "Process start"}) + "\n" +
        json.dumps({"timestamp": "2026-11-05T16:26:02.000Z", "level": "info", "component": "stt", "turn_id": 1, "message": "Speech detected"}) + "\n" +
        json.dumps({"timestamp": "2026-11-05T16:26:03.000Z", "level": "warn", "component": "llm", "turn_id": 1, "message": "Retrying endpoint"}) + "\n" +
        json.dumps({"timestamp": "2026-11-05T16:26:04.000Z", "level": "error", "component": "session", "turn_id": 2, "message": "API failure"}) + "\n"
    )

    # 1. Filter by level WARN (should get WARN and ERROR)
    res = subprocess.run(
        [sys.executable, "-m", "agentctl.cli", "logs", "--level", "warn"],
        capture_output=True,
        text=True,
    )
    assert "WARN" in res.stdout
    assert "ERROR" in res.stdout
    assert "DEBUG" not in res.stdout
    assert "Speech detected" not in res.stdout

    # 2. Filter by component stt
    res = subprocess.run(
        [sys.executable, "-m", "agentctl.cli", "logs", "--component", "stt"],
        capture_output=True,
        text=True,
    )
    assert "Speech detected" in res.stdout
    assert "Retrying endpoint" not in res.stdout

    # 3. Filter by turn 2
    res = subprocess.run(
        [sys.executable, "-m", "agentctl.cli", "logs", "--turn", "2"],
        capture_output=True,
        text=True,
    )
    assert "turn=2" in res.stdout
    assert "turn=1" not in res.stdout

    # 4. JSON output mode
    res = subprocess.run(
        [sys.executable, "-m", "agentctl.cli", "logs", "--json", "--level", "error"],
        capture_output=True,
        text=True,
    )
    lines = [l for l in res.stdout.strip().splitlines() if l]
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["level"] == "error"
    assert data["component"] == "session"


def test_status_exit_code_mapping(monkeypatch):
    """Verifies agentctl status exit codes: 0 for healthy running, 1 for degraded, 3 for stopped."""
    import argparse
    import agentctl.cli as cli

    # Case 1: Healthy running daemon -> Exit code 0
    monkeypatch.setattr(
        cli,
        "query_daemon_status",
        lambda timeout_s=1.0: (0, {
            "daemon": {"status": "running", "pid": 1234, "uptime_secs": 10},
            "components": {"daemon": {"status": "up"}, "ui": {"status": "up"}, "tts": {"status": "up"}},
            "models": {"models_loaded": ["whisper"], "ram_mb": 100.0},
        }),
    )
    with pytest.raises(SystemExit) as exc:
        cli.cmd_status(argparse.Namespace(json=False))
    assert exc.value.code == 0

    # Case 2: Degraded daemon (e.g. killed TTS worker) -> Exit code 1
    monkeypatch.setattr(
        cli,
        "query_daemon_status",
        lambda timeout_s=1.0: (1, {
            "daemon": {"status": "degraded", "pid": 1234, "uptime_secs": 10},
            "components": {"daemon": {"status": "up"}, "tts": {"status": "down"}},
            "models": {"models_loaded": ["whisper"], "ram_mb": 100.0},
        }),
    )
    with pytest.raises(SystemExit) as exc:
        cli.cmd_status(argparse.Namespace(json=False))
    assert exc.value.code == 1

    # Case 3: Stopped daemon -> Exit code 3
    monkeypatch.setattr(
        cli,
        "query_daemon_status",
        lambda timeout_s=1.0: (3, None),
    )
    with pytest.raises(SystemExit) as exc:
        cli.cmd_status(argparse.Namespace(json=False))
    assert exc.value.code == 3
