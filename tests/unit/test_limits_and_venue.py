"""Versioned limit sets, and the fake venue's configurable rudeness."""
from __future__ import annotations

import pandas as pd
import pytest

from fiboki.broker.base import (
    BrokerRejected,
    BrokerUnavailable,
    DuplicateClientRef,
    OrderAck,
    OrderStatus,
)
from fiboki.broker.simulated_venue import SimulatedVenue, VenueConfig
from fiboki.core.contracts import Order
from fiboki.core.enums import Direction, ExecutionMode, OrderType
from fiboki.risk.limits import (
    CONSERVATIVE_LIMITS,
    DEFAULT_LIMITS,
    LIMIT_SETS,
    PAPER_LIMITS,
    LimitSet,
    get_limit_set,
    register_limit_set,
)

NOW = pd.Timestamp("2024-06-03 12:00", tz="UTC")


# ----------------------------------------------------------- limit sets


def test_every_shipped_limit_set_is_registered_and_retrievable() -> None:
    for limits in (DEFAULT_LIMITS, CONSERVATIVE_LIMITS, PAPER_LIMITS):
        assert get_limit_set(limits.version) is limits


def test_an_unknown_limit_set_raises_rather_than_defaulting() -> None:
    """A decision taken under unknown limits is not an auditable decision."""
    with pytest.raises(KeyError, match="will not fall back"):
        get_limit_set("limits_that_do_not_exist")


def test_a_limit_set_fingerprints_its_values() -> None:
    assert len(DEFAULT_LIMITS.fingerprint()) == 16
    assert DEFAULT_LIMITS.fingerprint() != CONSERVATIVE_LIMITS.fingerprint()


def test_the_fingerprint_ignores_the_version_and_the_notes() -> None:
    """Two names for identical numbers share a fingerprint: it is the NUMBERS
    that decided the trade."""
    twin = DEFAULT_LIMITS.derive("limits_v1_default_copy", notes="different note")
    assert twin.fingerprint() == DEFAULT_LIMITS.fingerprint()
    assert twin.version != DEFAULT_LIMITS.version


def test_changing_a_number_changes_the_fingerprint() -> None:
    tighter = DEFAULT_LIMITS.derive("limits_v2", max_per_trade_risk_pct=0.5)
    assert tighter.fingerprint() != DEFAULT_LIMITS.fingerprint()


def test_deriving_without_a_new_version_is_refused() -> None:
    with pytest.raises(ValueError, match="new version"):
        DEFAULT_LIMITS.derive(DEFAULT_LIMITS.version, max_daily_loss_pct=1.0)


def test_a_limit_set_stamps_a_decision_record() -> None:
    stamp = DEFAULT_LIMITS.stamp()
    assert stamp["limits_version"] == "limits_v1_default"
    assert stamp["limits_fingerprint"] == DEFAULT_LIMITS.fingerprint()


def test_registering_a_conflicting_version_is_refused() -> None:
    register_limit_set(DEFAULT_LIMITS)  # idempotent for identical values
    conflicting = LimitSet(version=DEFAULT_LIMITS.version, max_daily_loss_pct=0.1)
    with pytest.raises(ValueError, match="already registered"):
        register_limit_set(conflicting)


def test_a_new_version_registers_cleanly() -> None:
    new = DEFAULT_LIMITS.derive("limits_test_only", max_daily_loss_pct=2.0)
    register_limit_set(new)
    try:
        assert get_limit_set("limits_test_only") is new
    finally:
        LIMIT_SETS.pop("limits_test_only", None)


def test_an_unversioned_limit_set_is_refused() -> None:
    with pytest.raises(ValueError, match="versioned"):
        LimitSet(version="")


def test_negative_limits_are_refused() -> None:
    with pytest.raises(ValueError, match="negative"):
        LimitSet(version="bad", max_daily_loss_pct=-1.0)


def test_incoherent_limit_orderings_are_refused() -> None:
    """One trade must not be able to breach the book limit on its own."""
    with pytest.raises(ValueError, match="exceeds max_account_risk"):
        LimitSet(version="bad", max_per_trade_risk_pct=9.0, max_account_risk_pct=5.0)
    with pytest.raises(ValueError, match="weekly"):
        LimitSet(version="bad", max_daily_loss_pct=8.0, max_weekly_loss_pct=6.0)
    with pytest.raises(ValueError, match="total drawdown"):
        LimitSet(version="bad", max_weekly_loss_pct=25.0, max_total_drawdown_pct=20.0)


def test_paper_limits_relax_data_quality_but_NOT_risk() -> None:
    assert PAPER_LIMITS.max_price_age_seconds > DEFAULT_LIMITS.max_price_age_seconds
    assert PAPER_LIMITS.max_spread_multiple > DEFAULT_LIMITS.max_spread_multiple
    for field in (
        "max_per_trade_risk_pct",
        "max_account_risk_pct",
        "max_daily_loss_pct",
        "max_weekly_loss_pct",
        "max_total_drawdown_pct",
        "max_margin_utilisation_pct",
    ):
        assert getattr(PAPER_LIMITS, field) == getattr(DEFAULT_LIMITS, field), field


def test_conservative_limits_are_tighter_on_every_risk_dimension() -> None:
    for field in (
        "max_per_trade_risk_pct",
        "max_account_risk_pct",
        "max_daily_loss_pct",
        "max_weekly_loss_pct",
        "max_total_drawdown_pct",
        "max_margin_utilisation_pct",
        "max_instrument_exposure_pct",
    ):
        assert getattr(CONSERVATIVE_LIMITS, field) < getattr(DEFAULT_LIMITS, field), field


def test_a_limit_set_is_immutable() -> None:
    with pytest.raises(AttributeError):
        DEFAULT_LIMITS.max_daily_loss_pct = 99.0  # type: ignore[misc]


# -------------------------------------------------------- simulated venue


def _order(client_ref: str = "c1", size: float = 1000.0, **kwargs) -> Order:
    params = {
        "plan_id": "p1",
        "instrument": "EURUSD",
        "direction": Direction.LONG,
        "size": size,
        "order_type": OrderType.MARKET,
        "mode": ExecutionMode.PAPER,
        "client_ref": client_ref,
        "stop_loss": 1.09,
        "take_profit": 1.11,
    }
    params.update(kwargs)
    return Order(**params)


def _venue(**cfg) -> SimulatedVenue:
    cfg.setdefault("prices", {"EURUSD": 1.1000})
    return SimulatedVenue(VenueConfig(**cfg), now=NOW)


def test_a_clean_venue_fills_and_returns_a_broker_reference() -> None:
    venue = _venue()
    ack = venue.place_order(_order())
    assert ack.status is OrderStatus.FILLED
    assert ack.broker_ref.startswith("SIMDEAL-")
    assert venue.positions()[0].venue_ref == ack.broker_ref


def test_duplicate_submission_is_detected_before_anything_else() -> None:
    """Even a venue that is about to reject you must honour the idempotency key."""
    venue = _venue(reject_after=2)
    venue.place_order(_order("c1"))
    with pytest.raises(DuplicateClientRef):
        venue.place_order(_order("c1"))


def test_a_disconnected_venue_raises_unavailable_not_rejected() -> None:
    venue = _venue()
    venue.disconnect("network partition")
    with pytest.raises(BrokerUnavailable, match="network partition"):
        venue.place_order(_order())


def test_disconnect_after_n_orders() -> None:
    venue = _venue(disconnect_after=3)
    venue.place_order(_order("c1"))
    venue.place_order(_order("c2"))
    with pytest.raises(BrokerUnavailable):
        venue.place_order(_order("c3"))
    assert venue.health().connected is False


def test_configured_rejections_are_terminal() -> None:
    venue = _venue(reject_client_refs=frozenset({"bad"}))
    with pytest.raises(BrokerRejected):
        venue.place_order(_order("bad"))
    assert venue.place_order(_order("good")).status is OrderStatus.FILLED


def test_partial_fills_are_reported_as_partial() -> None:
    venue = _venue(partial_fill_fraction=0.4)
    ack = venue.place_order(_order(size=1000.0))
    assert ack.status is OrderStatus.PARTIALLY_FILLED
    assert ack.fill.filled_size == 400.0
    assert ack.fill.partial is True


def test_latency_is_accumulated_for_pacing_assertions() -> None:
    venue = _venue(latency_ms=40.0)
    venue.place_order(_order("c1"))
    venue.place_order(_order("c2"))
    assert venue.total_latency_ms == pytest.approx(80.0)


def test_ack_then_fail_commits_at_the_venue_before_failing() -> None:
    """The crash-mid-order scenario: the position is real, the response is not."""
    venue = _venue(ack_then_fail=True)
    with pytest.raises(BrokerUnavailable, match="response was lost"):
        venue.place_order(_order("c1"))
    assert len(venue.positions()) == 1
    assert len(venue.lost_responses) == 1
    # And the venue remembers our client reference, which is what makes the
    # position findable again.
    record = venue.record_for_client_ref("c1")
    assert record is not None
    assert record.broker_ref == venue.positions()[0].venue_ref


def test_ack_then_fail_can_be_limited_to_the_first_n_orders() -> None:
    venue = _venue(ack_then_fail=True, ack_then_fail_count=1)
    with pytest.raises(BrokerUnavailable):
        venue.place_order(_order("c1"))
    assert venue.place_order(_order("c2")).status is OrderStatus.FILLED


def test_orders_lists_records_whose_response_was_lost() -> None:
    venue = _venue(ack_then_fail=True)
    with pytest.raises(BrokerUnavailable):
        venue.place_order(_order("c1"))
    acks = venue.orders()
    assert len(acks) == 1
    assert acks[0].client_ref == "c1"
    assert acks[0].message == "response_lost"


def test_closing_is_keyed_on_the_broker_reference() -> None:
    venue = _venue()
    venue.place_order(_order())
    position = venue.positions()[0]
    ack = venue.close_position(position, client_ref="close-1")
    assert ack.status is OrderStatus.FILLED
    assert venue.positions() == ()


def test_closing_an_unknown_reference_is_refused() -> None:
    venue = _venue()
    venue.place_order(_order())
    position = venue.positions()[0]
    position.venue_ref = "SIMDEAL-NOPE"
    with pytest.raises(BrokerRejected, match="BROKER reference"):
        venue.close_position(position, client_ref="x")


def test_probabilistic_rejection_is_deterministic_for_a_seed() -> None:
    def run(seed: int) -> list[bool]:
        venue = SimulatedVenue(
            VenueConfig(rejection_probability=0.5, seed=seed, prices={"EURUSD": 1.1}),
            now=NOW,
        )
        outcomes = []
        for i in range(10):
            try:
                venue.place_order(_order(f"c{i}"))
                outcomes.append(True)
            except BrokerRejected:
                outcomes.append(False)
        return outcomes

    assert run(42) == run(42)
    assert any(o is False for o in run(42))


def test_a_filled_ack_without_a_broker_ref_is_refused_at_construction() -> None:
    """A fill you cannot key is a position you cannot close after a restart."""
    with pytest.raises(ValueError, match="venue's reference"):
        OrderAck(
            status=OrderStatus.FILLED,
            broker_ref=None,
            client_ref="c1",
            order_id="o1",
            submitted_at=NOW,
        )


def test_order_status_terminality_is_explicit() -> None:
    assert OrderStatus.FILLED.terminal
    assert OrderStatus.REJECTED.terminal
    assert OrderStatus.CANCELLED.terminal
    assert not OrderStatus.UNKNOWN.terminal
    assert not OrderStatus.ACCEPTED.terminal


def test_an_order_cannot_be_born_without_an_idempotency_key() -> None:
    with pytest.raises(ValueError, match="client_ref is mandatory"):
        _order(client_ref="")
