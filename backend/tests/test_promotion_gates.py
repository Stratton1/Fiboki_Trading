"""Tests for promotion gates — Paper→Demo and Demo→Live."""

from datetime import datetime, timedelta, timezone

from fibokei.risk.promotion import (
    GateResult,
    check_demo_to_live_gate,
    check_paper_to_demo_gate,
)


class TestPaperToDemoGate:
    """Paper → Demo promotion gate tests."""

    def _passing_kwargs(self):
        return {
            "bot_created_at": datetime.now(timezone.utc) - timedelta(days=45),
            "total_trades": 120,
            "critical_errors": 0,
            "composite_score": 0.72,
        }

    def test_all_checks_pass(self):
        result = check_paper_to_demo_gate(**self._passing_kwargs())
        assert result.passed is True
        assert result.gate_name == "paper_to_demo"
        assert len(result.failed_checks) == 0

    def test_insufficient_runtime(self):
        kwargs = self._passing_kwargs()
        kwargs["bot_created_at"] = datetime.now(timezone.utc) - timedelta(days=10)
        result = check_paper_to_demo_gate(**kwargs)
        assert result.passed is False
        assert any(c["name"] == "min_runtime" for c in result.failed_checks)

    def test_insufficient_trades(self):
        kwargs = self._passing_kwargs()
        kwargs["total_trades"] = 30
        result = check_paper_to_demo_gate(**kwargs)
        assert result.passed is False
        assert any(c["name"] == "min_trades" for c in result.failed_checks)

    def test_critical_errors_fail(self):
        kwargs = self._passing_kwargs()
        kwargs["critical_errors"] = 3
        result = check_paper_to_demo_gate(**kwargs)
        assert result.passed is False
        assert any(c["name"] == "no_critical_errors" for c in result.failed_checks)

    def test_low_composite_score(self):
        kwargs = self._passing_kwargs()
        kwargs["composite_score"] = 0.40
        result = check_paper_to_demo_gate(**kwargs)
        assert result.passed is False
        assert any(c["name"] == "composite_score" for c in result.failed_checks)

    def test_custom_thresholds(self):
        result = check_paper_to_demo_gate(
            bot_created_at=datetime.now(timezone.utc) - timedelta(days=10),
            total_trades=20,
            critical_errors=0,
            composite_score=0.50,
            min_days=7,
            min_trades=10,
            min_composite_score=0.45,
        )
        assert result.passed is True

    def test_multiple_failures(self):
        result = check_paper_to_demo_gate(
            bot_created_at=datetime.now(timezone.utc) - timedelta(days=5),
            total_trades=10,
            critical_errors=2,
            composite_score=0.30,
        )
        assert result.passed is False
        assert len(result.failed_checks) == 4

    def test_summary_passing(self):
        result = check_paper_to_demo_gate(**self._passing_kwargs())
        assert "all checks passed" in result.summary

    def test_summary_failing(self):
        result = check_paper_to_demo_gate(
            bot_created_at=datetime.now(timezone.utc) - timedelta(days=5),
            total_trades=10,
            critical_errors=0,
            composite_score=0.72,
        )
        assert "failed" in result.summary

    def test_naive_datetime_handled(self):
        """Bot timestamps without tzinfo should still work."""
        result = check_paper_to_demo_gate(
            bot_created_at=datetime.now() - timedelta(days=45),
            total_trades=120,
            critical_errors=0,
            composite_score=0.72,
        )
        assert result.passed is True


class TestDemoToLiveGate:
    """Demo → Live promotion gate tests."""

    def _passing_kwargs(self):
        return {
            "demo_started_at": datetime.now(timezone.utc) - timedelta(days=20),
            "total_trades": 60,
            "reconciliation_rate": 0.998,
            "avg_slippage_pips": 0.8,
            "manual_signoff": True,
        }

    def test_all_checks_pass(self):
        result = check_demo_to_live_gate(**self._passing_kwargs())
        assert result.passed is True
        assert result.gate_name == "demo_to_live"

    def test_insufficient_runtime(self):
        kwargs = self._passing_kwargs()
        kwargs["demo_started_at"] = datetime.now(timezone.utc) - timedelta(days=5)
        result = check_demo_to_live_gate(**kwargs)
        assert result.passed is False
        assert any(c["name"] == "min_runtime" for c in result.failed_checks)

    def test_low_reconciliation(self):
        kwargs = self._passing_kwargs()
        kwargs["reconciliation_rate"] = 0.90
        result = check_demo_to_live_gate(**kwargs)
        assert result.passed is False
        assert any(c["name"] == "reconciliation_rate" for c in result.failed_checks)

    def test_high_slippage(self):
        kwargs = self._passing_kwargs()
        kwargs["avg_slippage_pips"] = 5.0
        result = check_demo_to_live_gate(**kwargs)
        assert result.passed is False
        assert any(c["name"] == "slippage_tolerance" for c in result.failed_checks)

    def test_no_manual_signoff(self):
        kwargs = self._passing_kwargs()
        kwargs["manual_signoff"] = False
        result = check_demo_to_live_gate(**kwargs)
        assert result.passed is False
        assert any(c["name"] == "manual_signoff" for c in result.failed_checks)

    def test_custom_thresholds(self):
        result = check_demo_to_live_gate(
            demo_started_at=datetime.now(timezone.utc) - timedelta(days=5),
            total_trades=15,
            reconciliation_rate=0.95,
            avg_slippage_pips=3.0,
            manual_signoff=True,
            min_days=3,
            min_trades=10,
            min_reconciliation_rate=0.90,
            max_avg_slippage_pips=5.0,
        )
        assert result.passed is True


class TestGateResult:
    """GateResult dataclass tests."""

    def test_failed_checks_property(self):
        result = GateResult(
            passed=False,
            gate_name="test",
            checks=[
                {"name": "a", "passed": True},
                {"name": "b", "passed": False},
                {"name": "c", "passed": False},
            ],
        )
        assert len(result.failed_checks) == 2
        assert result.failed_checks[0]["name"] == "b"

    def test_empty_checks(self):
        result = GateResult(passed=True, gate_name="test")
        assert result.failed_checks == []
        assert "all checks passed" in result.summary
