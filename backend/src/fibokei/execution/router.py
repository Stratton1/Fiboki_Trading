"""Multi-broker execution router — Phase 1.

Translates one ``NormalisedTradePlan`` into N child ``ExecutionAttempt``
records by fanning out to every enabled :class:`ResolvedTarget`. Each
target is sized, gated, and dispatched independently. A failure on one
target never blocks siblings (the brief explicitly forbids that).

Order of validation gates per target — matches the agreed Phase 1 spec:

    1. Target enabled
    2. Global kill switch inactive
    3. Broker / environment allowed (live blocked unless explicit)
    4. Instrument supported by this broker
    5. Per-target size > 0
    6. (Phase 4) account-level risk checks
    7. Dispatch to adapter
    8. Record attempt

If any gate fails before dispatch, the attempt is recorded with status
``skipped`` (gate failure that's structural — disabled, kill switch,
unsupported instrument) or ``rejected`` (size zero / per-trade risk
violation). Adapter exceptions become ``error``-status attempts.
"""

from __future__ import annotations

import logging
import uuid
from typing import Callable

from fibokei.execution.adapter import ExecutionAdapter  # noqa: F401  (typing reference)
from fibokei.execution.broker_symbols import (
    TradovateContract,
    TradovateContractResolver,
)
from fibokei.execution.sizing import calculate_target_size
from fibokei.execution.targets import (
    ATTEMPT_ERROR,
    ATTEMPT_FILLED,
    ATTEMPT_PAPER_FILLED,
    ATTEMPT_REJECTED,
    ATTEMPT_SKIPPED,
    BROKER_IG,
    BROKER_PAPER,
    BROKER_TRADOVATE,
    ExecutionAttempt,
    NormalisedTradePlan,
    ResolvedTarget,
    UnsupportedSymbol,
)

logger = logging.getLogger(__name__)


KillSwitchCheck = Callable[[], bool]
TargetProvider = Callable[[str], list["ResolvedTarget"]]
"""Phase 2 hook: ``bot_id → list of resolved targets``. When supplied to
``ExecutionRouter``, it is consulted on every dispatch so each bot can fan
out to a different set of accounts. Phase 1 routers leave this ``None`` and
fall back to the static ``targets`` list."""


def _direction_to_broker(direction: str) -> str:
    """Map LONG/SHORT to BUY/SELL — common across IG and Tradovate."""
    return "BUY" if str(direction or "").upper() == "LONG" else "SELL"


def _resolve_symbol(target: ResolvedTarget, fiboki_symbol: str):
    """Resolve a Fiboki symbol for a target broker.

    Returns the broker-specific symbol string, or :class:`UnsupportedSymbol`.
    Paper accepts every symbol verbatim. IG and Tradovate use their own
    resolvers.
    """
    if target.broker == BROKER_PAPER:
        return fiboki_symbol

    if target.broker == BROKER_IG:
        from fibokei.execution.broker_symbols import resolve_ig_symbol

        return resolve_ig_symbol(fiboki_symbol)

    if target.broker == BROKER_TRADOVATE:
        adapter = target.adapter
        resolver: TradovateContractResolver | None = getattr(adapter, "_resolver", None)
        if resolver is None:
            return UnsupportedSymbol(
                code="NO_RESOLVER",
                detail="Tradovate adapter is missing a contract resolver",
            )
        result = resolver.resolve(fiboki_symbol)
        if isinstance(result, UnsupportedSymbol):
            return result
        if isinstance(result, TradovateContract):
            return result.contract_symbol
        return UnsupportedSymbol(code="RESOLVER_UNEXPECTED", detail=type(result).__name__)

    return UnsupportedSymbol(
        code="UNKNOWN_BROKER",
        detail=f"No symbol resolver configured for broker '{target.broker}'",
    )


class ExecutionRouter:
    """Fan-out router. One signal → N targets → N attempts."""

    def __init__(
        self,
        mode: str,
        targets: list[ResolvedTarget],
        kill_switch_check: KillSwitchCheck | None = None,
        target_provider: TargetProvider | None = None,
        account_risk_engine=None,
    ) -> None:
        self.mode = mode
        self._targets: list[ResolvedTarget] = list(targets)
        self._kill_switch_check = kill_switch_check or (lambda: False)
        self._target_provider = target_provider
        # Phase 4: optional per-account risk engine. When wired (db_targets
        # mode), it runs after sizing and before adapter dispatch and can
        # short-circuit the dispatch with a typed RiskDecision.
        self._account_risk_engine = account_risk_engine

    def _targets_for(self, bot_id: str | None) -> list[ResolvedTarget]:
        """Phase 2: per-bot target lookup with Phase 1 static fallback."""
        if self._target_provider is not None and bot_id:
            try:
                return list(self._target_provider(bot_id))
            except Exception:
                logger.exception(
                    "target_provider(bot_id=%s) raised; falling back to static targets",
                    bot_id,
                )
        return self._targets

    # ── Read-only views ─────────────────────────────────────────────

    @property
    def targets(self) -> tuple[ResolvedTarget, ...]:
        return tuple(self._targets)

    @property
    def enabled_targets(self) -> tuple[ResolvedTarget, ...]:
        return tuple(t for t in self._targets if t.is_enabled)

    @property
    def is_kill_switch_active(self) -> bool:
        try:
            return bool(self._kill_switch_check())
        except Exception:
            logger.exception("Kill switch check raised; assuming active")
            return True

    def summary(self) -> dict:
        """Operator-friendly snapshot for the System page / status endpoint."""
        return {
            "router_mode": self.mode,
            "kill_switch_active": self.is_kill_switch_active,
            "targets": [
                {
                    "target_id": t.target_id,
                    "name": t.name,
                    "broker": t.broker,
                    "environment": t.environment,
                    "is_enabled": t.is_enabled,
                    "live_allowed": t.live_allowed,
                    "allocated_capital": t.allocated_capital,
                    "risk_per_trade_pct": t.risk_per_trade_pct,
                }
                for t in self._targets
            ],
        }

    # ── Dispatch — open ─────────────────────────────────────────────

    def dispatch_open(self, plan: NormalisedTradePlan) -> list[ExecutionAttempt]:
        """Fan an opening trade out to every enabled target.

        Returns one :class:`ExecutionAttempt` per enabled target. Disabled
        targets are silently omitted from the list (their existence is
        visible via :meth:`summary`, but they don't get audited per signal).

        In Phase 2 ``db_targets`` mode the target list is resolved per
        ``plan.bot_id`` via the configured ``target_provider``; falls back
        to the static list if no provider is wired or the lookup fails.
        """
        parent_signal_id = uuid.uuid4().hex
        attempts: list[ExecutionAttempt] = []
        kill_active = self.is_kill_switch_active
        bot_targets = self._targets_for(plan.bot_id)

        for target in bot_targets:
            if not target.is_enabled:
                continue
            attempts.append(self._dispatch_one_open(parent_signal_id, target, plan, kill_active))
        return attempts

    def _dispatch_one_open(
        self,
        parent_signal_id: str,
        target: ResolvedTarget,
        plan: NormalisedTradePlan,
        kill_active: bool,
    ) -> ExecutionAttempt:
        attempt = ExecutionAttempt(
            parent_signal_id=parent_signal_id,
            target_id=target.target_id,
            target_name=target.name,
            broker=target.broker,
            environment=target.environment,
            account_capital=target.allocated_capital,
            risk_pct=target.risk_per_trade_pct,
            instrument=plan.instrument,
            direction=plan.direction,
            requested_price=plan.entry_price,
        )

        # Gate 1: kill switch
        if kill_active:
            attempt.status = ATTEMPT_SKIPPED
            attempt.rejection_reason = "kill_switch_active"
            attempt.error_code = "KILL_SWITCH"
            return attempt

        # Gate 2: env allowed
        if not target.is_environment_allowed():
            attempt.status = ATTEMPT_SKIPPED
            attempt.rejection_reason = (
                f"environment '{target.environment}' not allowed for target {target.target_id}"
            )
            attempt.error_code = "ENV_BLOCKED"
            return attempt

        # Gate 3: instrument supported by this broker
        symbol_or_err = _resolve_symbol(target, plan.instrument)
        if isinstance(symbol_or_err, UnsupportedSymbol):
            attempt.status = ATTEMPT_SKIPPED
            attempt.rejection_reason = symbol_or_err.detail or symbol_or_err.code
            attempt.error_code = symbol_or_err.code
            return attempt
        attempt.broker_symbol = symbol_or_err

        # Gate 4: per-target size
        size = calculate_target_size(target, plan)
        if size is None or size <= 0:
            attempt.status = ATTEMPT_REJECTED
            attempt.rejection_reason = (
                f"target-specific size rounds to 0 (capital={target.allocated_capital}, "
                f"risk%={target.risk_per_trade_pct})"
            )
            attempt.error_code = "SIZE_ZERO"
            return attempt
        attempt.requested_size = size
        attempt.adjusted_size = size

        # Gate 5: per-account risk (Phase 4). Sibling targets are unaffected
        # by this account's failure — the router still continues to dispatch
        # to other enabled targets after recording this rejection.
        if self._account_risk_engine is not None:
            account_id = _account_id_from_target(target)
            if account_id is not None:
                decision = self._account_risk_engine.evaluate(account_id)
                if not decision.allowed:
                    attempt.status = ATTEMPT_REJECTED
                    attempt.rejection_reason = decision.reason
                    attempt.error_code = decision.code
                    if decision.detail:
                        attempt.extra["risk_detail"] = decision.detail
                    return attempt

        # Dispatch
        order = {
            "instrument": plan.instrument,
            "direction": _direction_to_broker(plan.direction),
            "size": size,
            "requested_price": plan.entry_price,
            "stop_distance": (
                abs(plan.entry_price - plan.stop_loss) if plan.stop_loss else None
            ),
            "limit_distance": (
                abs(plan.take_profit_targets[0] - plan.entry_price)
                if plan.take_profit_targets
                else None
            ),
            "bot_id": plan.bot_id,
        }
        try:
            result = target.adapter.place_order(order)
        except Exception as exc:  # noqa: BLE001 — adapters can raise anything
            logger.exception(
                "Adapter %s raised in place_order for %s",
                type(target.adapter).__name__, plan.instrument,
            )
            attempt.status = ATTEMPT_ERROR
            attempt.rejection_reason = str(exc)
            attempt.error_code = "ADAPTER_EXCEPTION"
            return attempt

        return _populate_attempt_from_result(attempt, result, target.broker)

    # ── Dispatch — close ────────────────────────────────────────────

    def dispatch_close(
        self,
        target_deal_ids: dict[str, str],
        instrument: str,
        bot_id: str | None = None,
        parent_signal_id: str | None = None,
    ) -> list[ExecutionAttempt]:
        """Close per-target on exit.

        Only targets that hold a deal id receive a close call. Targets that
        never opened are silently skipped — they have no broker-side
        position to close.
        """
        signal_id = parent_signal_id or uuid.uuid4().hex
        attempts: list[ExecutionAttempt] = []
        # Use the per-bot target list when available so close-on-exit
        # consults the same targets the open dispatch saw.
        targets_for_close = self._targets_for(bot_id)

        for target in targets_for_close:
            deal_id = target_deal_ids.get(target.target_id)
            if not deal_id:
                continue
            attempt = ExecutionAttempt(
                parent_signal_id=signal_id,
                target_id=target.target_id,
                target_name=target.name,
                broker=target.broker,
                environment=target.environment,
                account_capital=target.allocated_capital,
                risk_pct=target.risk_per_trade_pct,
                instrument=instrument,
                direction="CLOSE",
                broker_deal_id=deal_id,
            )
            try:
                result = target.adapter.close_position(deal_id)
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "Adapter %s raised in close_position for %s",
                    type(target.adapter).__name__, deal_id,
                )
                attempt.status = ATTEMPT_ERROR
                attempt.rejection_reason = str(exc)
                attempt.error_code = "ADAPTER_EXCEPTION"
                attempts.append(attempt)
                continue
            attempts.append(_populate_close_attempt_from_result(attempt, result, target.broker))
        return attempts


def _account_id_from_target(target: ResolvedTarget) -> int | None:
    """Phase 4 helper: extract the integer account id from a target_id string.

    db_targets mode generates ``target_id="acct-{int}"``. Phase 1 env-driven
    targets use string ids (``ig-demo-main`` etc.) — those targets aren't
    backed by a DB row, so per-account risk doesn't apply.
    """
    raw = target.target_id or ""
    if raw.startswith("acct-"):
        try:
            return int(raw[5:])
        except (TypeError, ValueError):
            return None
    return None


def _populate_attempt_from_result(
    attempt: ExecutionAttempt,
    result: dict,
    broker: str,
) -> ExecutionAttempt:
    """Normalise a place_order result dict into the attempt's status fields."""
    if not isinstance(result, dict):
        attempt.status = ATTEMPT_ERROR
        attempt.rejection_reason = f"Adapter returned non-dict result: {type(result).__name__}"
        attempt.error_code = "ADAPTER_BAD_RETURN"
        return attempt

    raw_status = str(result.get("status", "")).strip()
    deal_id = result.get("deal_id") or result.get("dealId") or result.get("order_id")
    filled_price = result.get("filled_price") or result.get("level")
    filled_size = result.get("filled_size") or result.get("size")
    latency = result.get("fill_latency_ms")

    # Latency / slippage if the adapter populated them
    if isinstance(latency, (int, float)):
        attempt.latency_ms = int(latency)
    slippage = result.get("slippage_pips")
    if isinstance(slippage, (int, float)):
        attempt.slippage_pips = float(slippage)

    # Paper adapter reports lowercase ``filled``; IG reports ``ACCEPTED``;
    # Tradovate adapter reports ``ACCEPTED``. Treat any of these + a deal id
    # as a successful fill.
    accepted = raw_status.upper() in ("ACCEPTED", "FILLED")
    is_paper = broker == BROKER_PAPER

    if accepted and deal_id:
        attempt.status = ATTEMPT_PAPER_FILLED if is_paper else ATTEMPT_FILLED
        attempt.broker_deal_id = str(deal_id)
        attempt.broker_order_id = str(deal_id)
        if filled_price is not None:
            try:
                attempt.filled_price = float(filled_price)
            except (TypeError, ValueError):
                attempt.filled_price = None
        if filled_size is not None:
            try:
                attempt.filled_size = float(filled_size)
            except (TypeError, ValueError):
                attempt.filled_size = None
        return attempt

    # Anything else is a rejection.
    attempt.status = ATTEMPT_REJECTED
    attempt.rejection_reason = (
        result.get("reason")
        or result.get("error")
        or f"broker returned status='{raw_status or 'unknown'}'"
    )
    attempt.error_code = result.get("error_code") or "REJECTED"
    if "raw" in result:
        attempt.extra["raw"] = result["raw"]
    return attempt


def _populate_close_attempt_from_result(
    attempt: ExecutionAttempt,
    result: dict,
    broker: str,
) -> ExecutionAttempt:
    """Normalise a close_position result into attempt fields."""
    if not isinstance(result, dict):
        type_name = type(result).__name__
        attempt.status = ATTEMPT_ERROR
        attempt.rejection_reason = f"Adapter returned non-dict close result: {type_name}"
        attempt.error_code = "ADAPTER_BAD_RETURN"
        return attempt

    raw_status = str(result.get("status", "")).strip().upper()
    if raw_status in ("ACCEPTED", "CLOSED", "FILLED"):
        attempt.status = ATTEMPT_PAPER_FILLED if broker == BROKER_PAPER else ATTEMPT_FILLED
        # Some adapters echo the original deal id, others return a new one for the close.
        new_deal = result.get("deal_id") or result.get("dealId") or result.get("order_id")
        if new_deal:
            attempt.broker_order_id = str(new_deal)
        return attempt

    attempt.status = ATTEMPT_REJECTED
    attempt.rejection_reason = result.get("reason") or f"close status='{raw_status or 'unknown'}'"
    attempt.error_code = result.get("error_code") or "CLOSE_REJECTED"
    return attempt
