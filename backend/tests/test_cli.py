"""Tests for CLI commands."""

import subprocess
import sys
from pathlib import Path

import pytest

# Resolve the backend directory from the test file's own location so the
# tests work on any developer machine and in CI. The previous hardcoded
# /Users/joseph/Projects/Fiboki_Trading/backend path was the smoking-gun
# instance of local-environment drift the previous slice surfaced.
BACKEND_DIR = Path(__file__).resolve().parent.parent


class TestCLI:
    def test_list_strategies(self):
        result = subprocess.run(
            [sys.executable, "-m", "fibokei", "list-strategies"],
            capture_output=True, text=True,
            cwd=str(BACKEND_DIR),
        )
        assert result.returncode == 0
        assert "bot01_sanyaku" in result.stdout

    def test_list_indicators(self):
        result = subprocess.run(
            [sys.executable, "-m", "fibokei", "list-indicators"],
            capture_output=True, text=True,
            cwd=str(BACKEND_DIR),
        )
        assert result.returncode == 0
        assert "ichimoku_cloud" in result.stdout
        assert "atr" in result.stdout

    def test_backtest_command(self):
        result = subprocess.run(
            [
                sys.executable, "-m", "fibokei", "backtest",
                "--strategy", "bot01_sanyaku",
                "--instrument", "EURUSD",
                "--timeframe", "H1",
            ],
            capture_output=True, text=True,
            cwd=str(BACKEND_DIR),
            timeout=30,
        )
        assert result.returncode == 0
        assert "BACKTEST RESULTS" in result.stdout
        assert "Total Trades" in result.stdout
