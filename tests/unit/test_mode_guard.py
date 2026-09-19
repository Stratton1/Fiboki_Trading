"""Reaching LIVE requires five independent controls, and no single one suffices.

V1 could reach a live broker API from a single environment variable, via a
string-equality check on the base URL that a trailing slash defeated, with
``FIBOKEI_LIVE_EXECUTION_ENABLED: "true"`` committed as a literal in deploy
config. Every test below is a direct descendant of that incident.
"""
from __future__ import annotations

import itertools
import json

import pandas as pd
import pytest

from fiboki.broker.mode_guard import (
    LIVE_ARM_TOKEN,
    LIVE_EXECUTION_COMPILED_IN,
    LiveAuthorisation,
    LiveAuthorisationStore,
    ModeGuard,
    ModeGuardError,
    parse_host,
)
from fiboki.core.enums import ExecutionMode

NOW = pd.Timestamp("2024-06-03 12:00", tz="UTC")
LIVE_URL = "https://api-fxtrade.oanda.com"
DEMO_URL = "https://api-fxpractice.oanda.com"
STRATEGY = "ichimoku_a"


def _auth(**kwargs) -> LiveAuthorisation:
    params = {
        "operator": "joe",
        "granted_at": NOW - pd.Timedelta(hours=1),
        "expires_at": NOW + pd.Timedelta(hours=8),
        "strategies": (STRATEGY,),
        "venue_host": "api-fxtrade.oanda.com",
        "reason": "monitored live window",
    }
    params.update(kwargs)
    return LiveAuthorisation(**params)


class _Store(LiveAuthorisationStore):
    def __init__(self, auth: LiveAuthorisation | None) -> None:
        super().__init__(None)
        self._auth = auth

    def load(self) -> LiveAuthorisation | None:
        return self._auth


def _guard(*, compiled: bool, armed: bool, auth: LiveAuthorisation | None) -> ModeGuard:
    env = {"FIBOKI_LIVE_RUNTIME_ARMED": LIVE_ARM_TOKEN} if armed else {}
    return ModeGuard(authorisation_store=_Store(auth), env=env, compiled_in=compiled)


# --------------------------------------------------- the repository state


def test_live_is_not_compiled_in_in_this_repository() -> None:
    """If this ever fails, someone has armed live execution in source."""
    assert LIVE_EXECUTION_COMPILED_IN is False


def test_the_default_guard_blocks_live() -> None:
    guard = ModeGuard(env={})
    decision = guard.check(ExecutionMode.LIVE, strategy_id=STRATEGY, venue_url=LIVE_URL,
                           now=NOW)
    assert not decision.allowed
    assert "build_time_constant" in decision.failed_controls


# ------------------------------------------- each control ALONE is not enough


CONTROL_NAMES = ("compiled", "armed", "auth", "host")


def _check(compiled, armed, auth, url):
    return _guard(compiled=compiled, armed=armed, auth=auth).check(
        ExecutionMode.LIVE, strategy_id=STRATEGY, venue_url=url, now=NOW
    )


@pytest.mark.parametrize("only", CONTROL_NAMES)
def test_setting_exactly_one_control_still_blocks_live(only: str) -> None:
    decision = _check(
        compiled=(only == "compiled"),
        armed=(only == "armed"),
        auth=_auth() if only == "auth" else None,
        url=LIVE_URL if only == "host" else DEMO_URL,
    )
    assert not decision.allowed, f"setting only {only!r} was enough to reach LIVE"
    assert len(decision.failed_controls) >= 1


@pytest.mark.parametrize("pair", list(itertools.combinations(CONTROL_NAMES, 2)))
def test_setting_any_two_controls_still_blocks_live(pair) -> None:
    decision = _check(
        compiled=("compiled" in pair),
        armed=("armed" in pair),
        auth=_auth() if "auth" in pair else None,
        url=LIVE_URL if "host" in pair else DEMO_URL,
    )
    assert not decision.allowed, f"setting {pair} was enough to reach LIVE"


@pytest.mark.parametrize("triple", list(itertools.combinations(CONTROL_NAMES, 3)))
def test_setting_any_three_controls_still_blocks_live(triple) -> None:
    decision = _check(
        compiled=("compiled" in triple),
        armed=("armed" in triple),
        auth=_auth() if "auth" in triple else None,
        url=LIVE_URL if "host" in triple else DEMO_URL,
    )
    assert not decision.allowed, f"setting {triple} was enough to reach LIVE"


def test_all_controls_together_do_authorise_live() -> None:
    """The control must be a control, not a prohibition: it has to be openable."""
    decision = _check(compiled=True, armed=True, auth=_auth(), url=LIVE_URL)
    assert decision.allowed, decision.reasons
    assert decision.failed_controls == ()


# ------------------------------------------------ control-specific defeats


def test_the_runtime_flag_must_be_the_exact_token_not_true() -> None:
    for value in ("true", "True", "1", "yes", "TRUE", ""):
        guard = ModeGuard(
            authorisation_store=_Store(_auth()),
            env={"FIBOKI_LIVE_RUNTIME_ARMED": value},
            compiled_in=True,
        )
        decision = guard.check(ExecutionMode.LIVE, strategy_id=STRATEGY,
                               venue_url=LIVE_URL, now=NOW)
        assert not decision.allowed, f"{value!r} armed live execution"
        assert "runtime_env_flag" in decision.failed_controls


def test_a_config_file_alone_cannot_enable_live(tmp_path) -> None:
    """The V1 failure exactly: a committed config literal reaching a live API."""
    config = tmp_path / "deploy.json"
    config.write_text(
        json.dumps(
            {
                "FIBOKEI_LIVE_EXECUTION_ENABLED": "true",
                "FIBOKI_LIVE_RUNTIME_ARMED": "true",
                "execution_mode": "live",
                "venue_url": LIVE_URL,
            }
        )
    )
    # Whatever a config file says, the guard reads the build constant and the
    # process environment. A file cannot set either.
    settings = json.loads(config.read_text())
    guard = ModeGuard(authorisation_store=_Store(None), env=dict(settings))
    decision = guard.check(
        ExecutionMode.LIVE, strategy_id=STRATEGY, venue_url=settings["venue_url"], now=NOW
    )
    assert not decision.allowed
    assert {"build_time_constant", "operator_authorisation", "strategy_allow_list"} <= set(
        decision.failed_controls
    )


def test_an_expired_authorisation_does_not_count() -> None:
    expired = _auth(
        granted_at=NOW - pd.Timedelta(days=3), expires_at=NOW - pd.Timedelta(days=2)
    )
    decision = _check(compiled=True, armed=True, auth=expired, url=LIVE_URL)
    assert not decision.allowed
    assert "operator_authorisation" in decision.failed_controls


def test_an_authorisation_granted_in_the_future_does_not_count() -> None:
    future = _auth(
        granted_at=NOW + pd.Timedelta(hours=1), expires_at=NOW + pd.Timedelta(hours=9)
    )
    decision = _check(compiled=True, armed=True, auth=future, url=LIVE_URL)
    assert not decision.allowed


def test_a_strategy_not_on_the_allow_list_is_blocked() -> None:
    decision = _guard(compiled=True, armed=True, auth=_auth()).check(
        ExecutionMode.LIVE, strategy_id="some_other_strategy", venue_url=LIVE_URL, now=NOW
    )
    assert not decision.allowed
    assert "strategy_allow_list" in decision.failed_controls


def test_an_authorisation_cannot_carry_a_wildcard() -> None:
    with pytest.raises(ValueError, match="wildcard"):
        _auth(strategies=("*",))


def test_an_authorisation_with_no_strategies_is_refused() -> None:
    with pytest.raises(ValueError):
        _auth(strategies=())


def test_an_authorisation_must_expire() -> None:
    with pytest.raises(ValueError):
        _auth(expires_at=NOW - pd.Timedelta(days=1))


def test_an_authorisation_must_name_an_operator() -> None:
    with pytest.raises(ValueError):
        _auth(operator="")


# ---------------------------------------------- the trailing-slash defeat


@pytest.mark.parametrize(
    "url",
    [
        "https://api-fxtrade.oanda.com",
        "https://api-fxtrade.oanda.com/",
        "https://api-fxtrade.oanda.com/v3/accounts",
        "https://api-fxtrade.oanda.com:443/",
        "https://user:pass@api-fxtrade.oanda.com/",
        "HTTPS://API-FXTRADE.OANDA.COM/",
    ],
)
def test_the_hostname_control_is_not_defeated_by_url_cosmetics(url: str) -> None:
    """V1's live gate was a string comparison. These all parse to one host."""
    assert parse_host(url) == "api-fxtrade.oanda.com"
    decision = _check(compiled=True, armed=True, auth=_auth(), url=url)
    assert decision.allowed, decision.reasons


@pytest.mark.parametrize(
    "url",
    [
        "https://api-fxtrade.oanda.com.attacker.example/",
        "https://not-api-fxtrade.oanda.com/",
        "https://evil.example/api-fxtrade.oanda.com",
        "https://oanda.com/",
        "",
    ],
)
def test_look_alike_hosts_are_not_approved_live_hosts(url: str) -> None:
    decision = _check(compiled=True, armed=True, auth=_auth(), url=url)
    assert not decision.allowed
    assert "venue_hostname" in decision.failed_controls


def test_the_authorisation_host_must_match_the_request_host() -> None:
    auth = _auth(venue_host="api.ig.com")
    decision = _check(compiled=True, armed=True, auth=auth, url=LIVE_URL)
    assert not decision.allowed
    assert any("authorisation names venue host" in r for r in decision.reasons)


# -------------------------------------------------- the safe direction


def test_a_demo_run_pointed_at_a_live_host_is_refused() -> None:
    guard = ModeGuard(env={})
    decision = guard.check(ExecutionMode.DEMO, strategy_id=STRATEGY,
                           venue_url=LIVE_URL, now=NOW)
    assert not decision.allowed
    assert "not_pointed_at_live_host" in decision.failed_controls


def test_a_demo_run_against_the_practice_host_is_fine() -> None:
    guard = ModeGuard(env={})
    assert guard.check(ExecutionMode.DEMO, strategy_id=STRATEGY,
                       venue_url=DEMO_URL, now=NOW).allowed


def test_a_broker_touching_mode_against_an_unknown_host_is_refused() -> None:
    guard = ModeGuard(env={})
    decision = guard.check(ExecutionMode.DEMO, strategy_id=STRATEGY,
                           venue_url="https://whatever.example/", now=NOW)
    assert not decision.allowed
    assert "known_venue_host" in decision.failed_controls


@pytest.mark.parametrize("mode", [ExecutionMode.BACKTEST, ExecutionMode.PAPER])
def test_non_broker_modes_need_no_venue(mode) -> None:
    assert ModeGuard(env={}).check(mode, strategy_id=STRATEGY, venue_url="", now=NOW).allowed


# ------------------------------------------------------------- require


def test_require_raises_and_names_the_failed_controls() -> None:
    guard = ModeGuard(env={})
    with pytest.raises(ModeGuardError) as excinfo:
        guard.require(ExecutionMode.LIVE, strategy_id=STRATEGY, venue_url=LIVE_URL, now=NOW)
    message = str(excinfo.value)
    assert "build_time_constant" in message
    assert "operator_authorisation" in message


def test_require_returns_the_decision_when_allowed() -> None:
    guard = _guard(compiled=True, armed=True, auth=_auth())
    decision = guard.require(ExecutionMode.LIVE, strategy_id=STRATEGY,
                             venue_url=LIVE_URL, now=NOW)
    assert decision.allowed


# --------------------------------------------------- authorisation store


def test_a_missing_authorisation_file_means_no_authorisation(tmp_path) -> None:
    assert LiveAuthorisationStore(tmp_path / "absent.json").load() is None


def test_an_unparseable_authorisation_file_means_no_authorisation(tmp_path) -> None:
    path = tmp_path / "auth.json"
    path.write_text("{not json")
    assert LiveAuthorisationStore(path).load() is None


def test_an_authorisation_round_trips_through_disk(tmp_path) -> None:
    path = tmp_path / "auth.json"
    store = LiveAuthorisationStore(path)
    store.save(_auth())
    loaded = store.load()
    assert loaded is not None
    assert loaded.operator == "joe"
    assert loaded.strategies == (STRATEGY,)
    assert loaded.valid_at(NOW)
