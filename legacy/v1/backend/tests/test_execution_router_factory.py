"""Tests for ``build_execution_router_from_env``.

Covers the legacy_single fallback, env_global_fanout activation, default
safety (paper-only), and per-broker enable flags.
"""

from __future__ import annotations

import pytest

from fibokei.execution.router_factory import (
    build_execution_router_from_env,
    get_router_mode,
)
from fibokei.execution.targets import (
    BROKER_IG,
    BROKER_PAPER,
    BROKER_TRADOVATE,
    ROUTER_MODE_ENV_GLOBAL_FANOUT,
    ROUTER_MODE_LEGACY_SINGLE,
)

# Each test gets a fresh env using monkeypatch.delenv on every fibokei var
# we care about, then sets only what it needs.

_ALL_ENV_VARS = [
    "FIBOKEI_EXECUTION_ROUTER_MODE",
    "FIBOKEI_PAPER_ACCOUNT_ENABLED",
    "FIBOKEI_PAPER_ACCOUNT_CAPITAL",
    "FIBOKEI_PAPER_ACCOUNT_RISK_PCT",
    "FIBOKEI_IG_ACCOUNT_ENABLED",
    "FIBOKEI_IG_ACCOUNT_ENV",
    "FIBOKEI_IG_ACCOUNT_CAPITAL",
    "FIBOKEI_IG_ACCOUNT_RISK_PCT",
    "FIBOKEI_IG_LIVE_ALLOWED",
    "FIBOKEI_TRADOVATE_ACCOUNT_ENABLED",
    "FIBOKEI_TRADOVATE_ACCOUNT_ENV",
    "FIBOKEI_TRADOVATE_ACCOUNT_CAPITAL",
    "FIBOKEI_TRADOVATE_LIVE_ALLOWED",
    "FIBOKEI_LIVE_EXECUTION_ENABLED",
]


@pytest.fixture
def clean_env(monkeypatch):
    for var in _ALL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


class TestGetRouterMode:
    def test_default_legacy_single(self, clean_env):
        assert get_router_mode() == ROUTER_MODE_LEGACY_SINGLE

    def test_env_global_fanout(self, clean_env):
        clean_env.setenv("FIBOKEI_EXECUTION_ROUTER_MODE", "env_global_fanout")
        assert get_router_mode() == ROUTER_MODE_ENV_GLOBAL_FANOUT

    def test_invalid_mode_falls_back_to_legacy(self, clean_env):
        clean_env.setenv("FIBOKEI_EXECUTION_ROUTER_MODE", "garbage")
        assert get_router_mode() == ROUTER_MODE_LEGACY_SINGLE


class TestLegacySingleMode:
    def test_paper_only_when_live_disabled(self, clean_env):
        router = build_execution_router_from_env()
        assert router.mode == ROUTER_MODE_LEGACY_SINGLE
        assert len(router.targets) == 1
        assert router.targets[0].broker == BROKER_PAPER

    def test_ig_when_live_enabled(self, clean_env):
        clean_env.setenv("FIBOKEI_LIVE_EXECUTION_ENABLED", "true")
        router = build_execution_router_from_env()
        assert router.mode == ROUTER_MODE_LEGACY_SINGLE
        assert len(router.targets) == 1
        assert router.targets[0].broker == BROKER_IG


class TestEnvGlobalFanoutMode:
    def test_paper_only_by_default(self, clean_env):
        clean_env.setenv("FIBOKEI_EXECUTION_ROUTER_MODE", "env_global_fanout")
        router = build_execution_router_from_env()
        assert router.mode == ROUTER_MODE_ENV_GLOBAL_FANOUT
        # Default: only paper enabled
        brokers = [t.broker for t in router.enabled_targets]
        assert brokers == [BROKER_PAPER]

    def test_ig_opt_in(self, clean_env):
        clean_env.setenv("FIBOKEI_EXECUTION_ROUTER_MODE", "env_global_fanout")
        clean_env.setenv("FIBOKEI_IG_ACCOUNT_ENABLED", "true")
        router = build_execution_router_from_env()
        brokers = sorted(t.broker for t in router.enabled_targets)
        assert brokers == [BROKER_IG, BROKER_PAPER]

    def test_tradovate_opt_in(self, clean_env):
        clean_env.setenv("FIBOKEI_EXECUTION_ROUTER_MODE", "env_global_fanout")
        clean_env.setenv("FIBOKEI_TRADOVATE_ACCOUNT_ENABLED", "true")
        router = build_execution_router_from_env()
        brokers = sorted(t.broker for t in router.enabled_targets)
        assert brokers == [BROKER_PAPER, BROKER_TRADOVATE]

    def test_all_three_enabled(self, clean_env):
        clean_env.setenv("FIBOKEI_EXECUTION_ROUTER_MODE", "env_global_fanout")
        clean_env.setenv("FIBOKEI_IG_ACCOUNT_ENABLED", "true")
        clean_env.setenv("FIBOKEI_TRADOVATE_ACCOUNT_ENABLED", "true")
        router = build_execution_router_from_env()
        brokers = sorted(t.broker for t in router.enabled_targets)
        assert brokers == [BROKER_IG, BROKER_PAPER, BROKER_TRADOVATE]

    def test_paper_disabled_with_ig_enabled(self, clean_env):
        clean_env.setenv("FIBOKEI_EXECUTION_ROUTER_MODE", "env_global_fanout")
        clean_env.setenv("FIBOKEI_PAPER_ACCOUNT_ENABLED", "false")
        clean_env.setenv("FIBOKEI_IG_ACCOUNT_ENABLED", "true")
        router = build_execution_router_from_env()
        brokers = [t.broker for t in router.enabled_targets]
        assert brokers == [BROKER_IG]

    def test_per_target_capital_picked_up(self, clean_env):
        clean_env.setenv("FIBOKEI_EXECUTION_ROUTER_MODE", "env_global_fanout")
        clean_env.setenv("FIBOKEI_IG_ACCOUNT_ENABLED", "true")
        clean_env.setenv("FIBOKEI_IG_ACCOUNT_CAPITAL", "2500")
        clean_env.setenv("FIBOKEI_TRADOVATE_ACCOUNT_ENABLED", "true")
        clean_env.setenv("FIBOKEI_TRADOVATE_ACCOUNT_CAPITAL", "12500")
        router = build_execution_router_from_env()
        cap = {t.broker: t.allocated_capital for t in router.enabled_targets}
        assert cap[BROKER_IG] == 2500.0
        assert cap[BROKER_TRADOVATE] == 12500.0


class TestLiveSafetyGates:
    def test_ig_live_blocked_without_global_flag(self, clean_env):
        clean_env.setenv("FIBOKEI_EXECUTION_ROUTER_MODE", "env_global_fanout")
        clean_env.setenv("FIBOKEI_IG_ACCOUNT_ENABLED", "true")
        clean_env.setenv("FIBOKEI_IG_ACCOUNT_ENV", "live")
        clean_env.setenv("FIBOKEI_IG_LIVE_ALLOWED", "true")
        # FIBOKEI_LIVE_EXECUTION_ENABLED unset → still blocked
        router = build_execution_router_from_env()
        ig = next(t for t in router.targets if t.broker == BROKER_IG)
        assert ig.live_allowed is False

    def test_tradovate_live_requires_three_flags(self, clean_env):
        clean_env.setenv("FIBOKEI_EXECUTION_ROUTER_MODE", "env_global_fanout")
        clean_env.setenv("FIBOKEI_TRADOVATE_ACCOUNT_ENABLED", "true")
        clean_env.setenv("FIBOKEI_TRADOVATE_ACCOUNT_ENV", "live")
        # Missing both LIVE_ALLOWED and global LIVE_EXECUTION_ENABLED → blocked
        router1 = build_execution_router_from_env()
        tv1 = next(t for t in router1.targets if t.broker == BROKER_TRADOVATE)
        assert tv1.live_allowed is False

        # All three flags set → allowed at the target level (still blocked at
        # the client URL gate unless the URL also matches live)
        clean_env.setenv("FIBOKEI_TRADOVATE_LIVE_ALLOWED", "true")
        clean_env.setenv("FIBOKEI_LIVE_EXECUTION_ENABLED", "true")
        router2 = build_execution_router_from_env()
        tv2 = next(t for t in router2.targets if t.broker == BROKER_TRADOVATE)
        assert tv2.live_allowed is True
