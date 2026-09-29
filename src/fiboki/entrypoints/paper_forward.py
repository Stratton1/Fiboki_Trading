"""PAPER trading forward in time, against live OANDA PRACTICE prices (audit F, P1-15).

What was missing
----------------
Every part of a forward paper trader existed and was tested: the polling bar
feed (``workers/feeds.OandaPollingBarFeed``), the loop
(``workers/live_worker.LiveWorker``) with fail-closed startup reconciliation and
periodic reconciliation, the risk-context builder, the execution service, the
venue position manager. Nothing composed them. ``fiboki worker run live``
refuses, rightly, to assemble a market-facing worker from command-line flags,
and there was no reviewed place that assembled one from anything else. So
nothing traded forward, and the operator intent -- the deterministic system
trades -- was not met.

This module is that place. :func:`compose` reads a COMMITTED, VERSIONED wiring
file (``entrypoints/wiring/paper_forward_v1.json``) and builds::

    OANDA practice candles --HttpxTransport--> OandaPollingBarFeed
         |                                          |  bar close time + price
         |                                          v
         |                          RiskContextBuilder   ForwardPaperVenue
         |   OANDA practice pricing  (freshness, market   (instant fill at the
         +--> OandaPricingSpreadSource  state, events,     last close +/- the
              (abnormal_spread is REAL)  P&L, correlation)  profile half spread)
                                                |
    compiled strategy (content-hashed) -> SignalEvaluator (sizes ONCE)
         -> RiskGateway (durable kill switch; every attempt stamped with the
            wiring's sha256) -> ExecutionService (fsynced intents)
         -> ForwardPaperVenue, managed by VenuePositionManager over the SAME
            PositionBook the backtester drives

and hands it to a :class:`LiveWorker` whose ``allowed_modes`` is ``("paper",)``.

PAPER ONLY, structurally
------------------------
* The wiring's ``mode`` and ``allowed_modes`` must be exactly ``paper``; any
  other value refuses to load. Widening them is a source change here, reviewed,
  not a wiring edit.
* The execution service is constructed in ``ExecutionMode.PAPER`` over
  :class:`~fiboki.entrypoints.paper_venue.ForwardPaperVenue`, which reaches no
  broker. There is no OANDA order adapter in this composition at all: OANDA is
  read (candles and pricing) and never written to.
* The market-data base URL is PARSED and its hostname must be
  ``api-fxpractice.oanda.com``. The live host is a hard error, and the
  :class:`~fiboki.broker.http_transport.HttpxTransport` is built with the
  practice host as its only allowed host, so even a bug upstream cannot send a
  request to ``api-fxtrade``.
* None of the live controls in ``broker/mode_guard.py`` or ``broker/oanda.py``
  is read, set or needed.

Credentials
-----------
``FIBOKI_OANDA_PRACTICE_TOKEN`` and ``FIBOKI_OANDA_PRACTICE_ACCOUNT_ID``
(declared in ``api/settings.ENV_REGISTRY``) are read ONCE, from the environment
the process was started with (``~/.fiboki/env`` via
``scripts/fiboki-service.sh``). The token goes into request headers and nowhere
else: it is not logged, not journalled and not in any ``repr``.

The pre-round-4 names ``OANDA_PRACTICE_TOKEN`` / ``OANDA_PRACTICE_ACCOUNT_ID``
are still accepted for ONE release as a documented fallback, used only when
the ``FIBOKI_*`` name is unset, and each use logs a warning naming the
variable to rename (never its value). They will be removed after that release.

What is modelled, and what is not (read before believing a number)
------------------------------------------------------------------
* THE LEDGER OF RECORD IS THE BOOK. Entries fill in the book at the NEXT bar's
  open through the fill simulator, exactly as in a backtest of the same
  document. The paper venue's instant fill at the signal bar's close is
  recorded beside it, and the gap is measured, not reconciled away.
* Spreads charged by the book and the venue are the EXECUTION PROFILE's
  (``broker_profile`` in the wiring). The LIVE quoted spread from OANDA pricing
  feeds the gateway's ``abnormal_spread`` check and is journalled per bar, but
  is not charged.
* FX: v1 supports instruments quoted in the account currency only
  (``IdentityFxSource``); a wiring that needs conversion is refused.
* No market-state engine is wired: the regime is ``unknown`` on every context.
* A restart loses the in-memory book and venue. The journal carries the
  realised balance, the peak equity (so the drawdown limit does not reset) and
  every closed trade (so the daily and weekly loss limits do not reset) across
  restarts; positions open at the last bar before the restart are recorded as
  ABANDONED in the journal and alerted, not silently dropped, and their
  unrealised P&L is not carried.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, is_dataclass, replace
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pandas as pd

from fiboki.backtest.exits import ExitPolicy, ReversalMode, exit_policy_from_document
from fiboki.backtest.position import BookConfig, PositionBook
from fiboki.broker.execution_service import ExecutionService, JsonlIntentStore
from fiboki.broker.http_transport import HttpxTransport, bearer_token_from_env
from fiboki.broker.oanda import OANDA_LIVE_HOST, OANDA_PRACTICE_HOST, RateLimiter
from fiboki.broker.oanda_pricing import OandaPricingSpreadSource
from fiboki.broker.position_manager import RetryPolicy, VenuePositionManager
from fiboki.broker.retry import ReadRetry
from fiboki.core.contracts import AccountState, Signal
from fiboki.core.durable import durable_append, read_payloads
from fiboki.core.enums import (
    ExecutionMode,
    ExitReason,
    Provenance,
    StrategyLifecycle,
    Timeframe,
)
from fiboki.core.instruments import get as get_instrument
from fiboki.core.money import IdentityFxSource
from fiboki.data.providers.oanda import OandaCandlesProvider
from fiboki.entrypoints.paper_venue import ForwardPaperVenue
from fiboki.marketstate.calendar import (
    CalendarError,
    EconomicCalendar,
    instrument_currencies,
    load_official_calendar,
)
from fiboki.obs.alerts import AlertEvent, Severity
from fiboki.obs.logging import get_logger
from fiboki.portfolio.construction import CorrelationMatrix
from fiboki.portfolio.sizing import SizingPolicy
from fiboki.risk.accounting import RealisedPnlLedger, correlation_from_frames
from fiboki.risk.gateway import ExecutionAttempt, ExitContext, RiskGateway
from fiboki.risk.killswitch import FileKillSwitchJournal, KillSwitch, RequestKind
from fiboki.risk.limits import get_limit_set
from fiboki.sim.fills import FillSimulator, IntrabarPolicy
from fiboki.sim.profiles import get_profile
from fiboki.strategy.compiler import compile_strategy
from fiboki.strategy.dsl import StrategyDocument
from fiboki.workers.base import WorkerStore
from fiboki.workers.feeds import OandaPollingBarFeed, PollingFeedConfig, TransportHttpClient
from fiboki.workers.live_worker import BarBatch, LiveWorker, LiveWorkerConfig
from fiboki.workers.runtime import (
    RiskContextBuilder,
    SignalEvaluator,
    calendar_event_source,
    event_source_horizon_minutes,
)

__all__ = [
    "DEFAULT_WIRING",
    "ENV_ACCOUNT",
    "ENV_TOKEN",
    "LEGACY_ENV_ACCOUNT",
    "LEGACY_ENV_TOKEN",
    "WIRING_SCHEMA",
    "ForwardJournal",
    "PaperForwardRuntime",
    "PaperForwardWiring",
    "PaperForwardWorker",
    "WiringError",
    "compose",
    "compose_runtime",
    "load_wiring",
]

_log = get_logger("fiboki.entrypoints.paper_forward")

#: The wiring file format this module reads. A different schema is refused.
WIRING_SCHEMA = "fiboki.paper_forward.wiring/1"
#: The committed wiring the desktop service runs.
DEFAULT_WIRING = Path(__file__).resolve().parent / "wiring" / "paper_forward_v1.json"
#: Credentials, read once from the environment. Never logged. Declared in
#: ``fiboki.api.settings.ENV_REGISTRY``.
ENV_TOKEN = "FIBOKI_OANDA_PRACTICE_TOKEN"
ENV_ACCOUNT = "FIBOKI_OANDA_PRACTICE_ACCOUNT_ID"
#: The pre-round-4 names: a fallback for ONE release, warned about when used.
LEGACY_ENV_TOKEN = "OANDA_PRACTICE_TOKEN"
LEGACY_ENV_ACCOUNT = "OANDA_PRACTICE_ACCOUNT_ID"
#: Where the seed documents live in a source checkout.
_REPO_STRATEGIES = Path(__file__).resolve().parents[3] / "research" / "strategies"


class WiringError(ValueError):
    """The wiring file is malformed, or asks for something this entrypoint refuses."""


# ===========================================================================
# The wiring file
# ===========================================================================


@dataclass(frozen=True, slots=True)
class StrategyWiring:
    strategy_id: str
    document: str
    #: sha256 of ``StrategyDocument.bind_defaults()`` -- the document AS TRADED.
    content_hash: str


@dataclass(frozen=True, slots=True)
class PaperForwardWiring:
    """A parsed, validated wiring file. ``sha256`` is of the file's exact bytes."""

    path: Path
    sha256: str
    version: str
    strategies: tuple[StrategyWiring, ...]
    instruments: tuple[str, ...]
    timeframe: Timeframe
    limits_version: str
    broker_profile: str
    market_data_base_url: str
    max_requests_per_second: float
    account_ccy: str
    initial_balance: float
    risk_fraction: float
    sizing_policy_id: str
    max_concurrent: int
    calendar_min_impact: str
    calendar_min_coverage_days: float
    correlation_min_observations: int
    correlation_default: float
    ledger_dir: str
    feed: Mapping[str, float]
    max_quote_age_s: float
    worker: Mapping[str, Any]
    allowed_modes: tuple[str, ...] = ("paper",)

    @property
    def short_hash(self) -> str:
        return self.sha256[:12]

    def stamp(self) -> dict[str, str]:
        """What goes on every attempt row and journal line."""
        return {"wiring_version": self.version, "wiring_sha256": self.sha256}


_TOP_LEVEL = frozenset(
    {
        "schema", "version", "description", "mode", "allowed_modes", "venue",
        "strategies", "instruments", "timeframe", "limits_version", "broker_profile",
        "account", "sizing", "book", "calendar", "correlation", "ledger_dir", "feed",
        "pricing", "worker",
    }
)
_FEED_KEYS = frozenset(
    {"offset_s", "late_candle_grace_s", "repoll_attempts", "repoll_interval_s",
     "max_block_s", "warmup_bars", "history_bars"}
)
_WORKER_KEYS = frozenset(
    {"lease_name", "lease_ttl_seconds", "idle_sleep_seconds",
     "reconcile_interval_seconds", "reconcile_every_cycles"}
)


def _require(body: Mapping[str, Any], key: str, where: str = "wiring") -> Any:
    if key not in body:
        raise WiringError(f"{where} is missing {key!r}")
    return body[key]


def _no_extra(body: Mapping[str, Any], allowed: frozenset[str], where: str) -> None:
    extra = sorted(set(body) - allowed)
    if extra:
        raise WiringError(
            f"{where} has unknown key(s) {extra}. A wiring file is reviewed as a whole; "
            "a key this entrypoint does not read would look like configuration and do "
            "nothing."
        )


def assert_practice_host(base_url: str) -> str:
    """The parsed hostname must be OANDA's PRACTICE host. The live host is a hard error."""
    parts = urlsplit(base_url)
    host = (parts.hostname or "").lower()
    if host == OANDA_LIVE_HOST:
        raise WiringError(
            f"{base_url!r} is the OANDA LIVE host. The paper-forward entrypoint reads "
            "the PRACTICE host only; there is no setting, token or flag that changes "
            "this."
        )
    if host != OANDA_PRACTICE_HOST or parts.scheme != "https":
        raise WiringError(
            f"{base_url!r} parses to {parts.scheme}://{host}; only "
            f"https://{OANDA_PRACTICE_HOST} is accepted. The comparison is on the "
            "parsed hostname, so a look-alike domain or prefix does not match."
        )
    return f"https://{OANDA_PRACTICE_HOST}"


def load_wiring(path: str | Path) -> PaperForwardWiring:
    """Parse and validate a wiring file. Everything it cannot vouch for is refused."""
    source = Path(path)
    raw = source.read_bytes()
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WiringError(f"{source} is not JSON: {exc}") from exc
    if not isinstance(body, dict):
        raise WiringError(f"{source} is not a JSON object")
    _no_extra(body, _TOP_LEVEL, "wiring")
    if body.get("schema") != WIRING_SCHEMA:
        raise WiringError(f"schema must be {WIRING_SCHEMA!r}, got {body.get('schema')!r}")
    mode = str(_require(body, "mode")).lower()
    allowed = tuple(str(m).lower() for m in _require(body, "allowed_modes"))
    if mode != "paper" or allowed != ("paper",):
        raise WiringError(
            f"mode={mode!r} allowed_modes={list(allowed)!r}: this entrypoint is PAPER "
            "only. Widening it is a reviewed source change to "
            "fiboki/entrypoints/paper_forward.py, never a wiring edit."
        )
    venue = dict(_require(body, "venue"))
    _no_extra(venue, frozenset({"kind", "market_data_base_url", "max_requests_per_second"}), "venue")
    if venue.get("kind") != "paper_forward":
        raise WiringError("venue.kind must be 'paper_forward' (instant-fill paper venue)")
    base_url = assert_practice_host(str(_require(venue, "market_data_base_url", "venue")))

    strategies_raw = _require(body, "strategies")
    if not isinstance(strategies_raw, list) or len(strategies_raw) != 1:
        raise WiringError(
            "v1 wiring trades exactly ONE strategy document. A paper book carries one "
            "exit policy, as a backtest run does; two documents need two runtimes."
        )
    strategies = []
    for item in strategies_raw:
        _no_extra(item, frozenset({"strategy_id", "document", "content_hash"}), "strategy")
        strategies.append(
            StrategyWiring(
                strategy_id=str(_require(item, "strategy_id", "strategy")),
                document=str(_require(item, "document", "strategy")),
                content_hash=str(_require(item, "content_hash", "strategy")),
            )
        )
    instruments = tuple(str(s).upper() for s in _require(body, "instruments"))
    if not instruments or len(set(instruments)) != len(instruments):
        raise WiringError("instruments must be a non-empty list without duplicates")
    try:
        timeframe = Timeframe(str(_require(body, "timeframe")).upper())
    except ValueError as exc:
        raise WiringError(f"unknown timeframe {body.get('timeframe')!r}") from exc

    account = dict(_require(body, "account"))
    _no_extra(account, frozenset({"currency", "initial_balance"}), "account")
    sizing = dict(_require(body, "sizing"))
    _no_extra(sizing, frozenset({"policy_id", "risk_fraction"}), "sizing")
    book = dict(_require(body, "book"))
    _no_extra(book, frozenset({"max_concurrent"}), "book")
    calendar = dict(_require(body, "calendar"))
    _no_extra(calendar, frozenset({"source", "min_impact", "min_coverage_days"}), "calendar")
    if calendar.get("source") != "official":
        raise WiringError("calendar.source must be 'official' (load_official_calendar)")
    correlation = dict(_require(body, "correlation"))
    _no_extra(correlation, frozenset({"source", "min_observations", "default"}), "correlation")
    if correlation.get("source") != "feed_history":
        raise WiringError("correlation.source must be 'feed_history'")
    feed = dict(_require(body, "feed"))
    _no_extra(feed, _FEED_KEYS, "feed")
    pricing = dict(_require(body, "pricing"))
    _no_extra(pricing, frozenset({"max_quote_age_s"}), "pricing")
    worker = dict(_require(body, "worker"))
    _no_extra(worker, _WORKER_KEYS, "worker")
    ledger_dir = str(_require(body, "ledger_dir"))
    if Path(ledger_dir).is_absolute() or ".." in Path(ledger_dir).parts:
        raise WiringError("ledger_dir must be a relative path under the state directory")

    return PaperForwardWiring(
        path=source.resolve(),
        sha256=hashlib.sha256(raw).hexdigest(),
        version=str(_require(body, "version")),
        strategies=tuple(strategies),
        instruments=instruments,
        timeframe=timeframe,
        limits_version=str(_require(body, "limits_version")),
        broker_profile=str(_require(body, "broker_profile")),
        market_data_base_url=base_url,
        max_requests_per_second=float(venue.get("max_requests_per_second", 20.0)),
        account_ccy=str(_require(account, "currency", "account")).upper(),
        initial_balance=float(_require(account, "initial_balance", "account")),
        risk_fraction=float(_require(sizing, "risk_fraction", "sizing")),
        sizing_policy_id=str(sizing.get("policy_id", "fixed_fractional_v1")),
        max_concurrent=int(_require(book, "max_concurrent", "book")),
        calendar_min_impact=str(calendar.get("min_impact", "high")),
        calendar_min_coverage_days=float(calendar.get("min_coverage_days", 14)),
        correlation_min_observations=int(correlation.get("min_observations", 30)),
        correlation_default=float(correlation.get("default", 0.30)),
        ledger_dir=ledger_dir,
        feed={k: float(v) for k, v in feed.items()},
        max_quote_age_s=float(pricing.get("max_quote_age_s", 120.0)),
        worker=worker,
        allowed_modes=allowed,
    )


# ===========================================================================
# The durable journal
# ===========================================================================


def _jsonable(value: Any) -> Any:
    if isinstance(value, pd.Timestamp | datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {k: _jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_jsonable(v) for v in value]
    if isinstance(value, float) and value != value:
        return None
    return value


@dataclass(frozen=True, slots=True)
class LedgerTrade:
    """A closed trade as the P&L ledger needs it, reloaded from ``trades.jsonl``."""

    trade_id: str
    strategy_id: str
    exit_time: pd.Timestamp
    net_pnl: float


class ForwardJournal:
    """Append-only, self-verifying journals, plus the paper session the API reads.

    ``<state_dir>/<ledger_dir>/``:

    ``journal.jsonl``   one event per line (session start, bar, pricing sample,
                        venue fill, book entry, trade closed, kill switch,
                        abandoned positions), each stamped with the wiring hash.
    ``trades.jsonl``    every closed trade, once. THE P&L LEDGER: the daily and
                        weekly loss windows are computed from it, so a restart
                        does not reset them.
    ``attempts.jsonl``  every risk-gateway attempt, allowed or blocked.
    ``intents.jsonl``   the execution service's fsynced order intents.

    ``<paper_root>/forward-<version>/`` holds ``summary.json``, ``trades.csv``
    and ``positions.csv`` in the format :mod:`fiboki.api.paper_journal` reads,
    rewritten atomically after every bar: ONE continuous account per wiring
    version, whose balance minus initial balance equals the sum of its closed
    trades.
    """

    def __init__(
        self,
        *,
        ledger_dir: Path,
        session_dir: Path,
        wiring: PaperForwardWiring,
        clock: Callable[[], pd.Timestamp],
    ) -> None:
        self.ledger_dir = ledger_dir
        self.session_dir = session_dir
        self.wiring = wiring
        self.clock = clock
        self.ledger_dir.mkdir(parents=True, exist_ok=True)
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.events_path = ledger_dir / "journal.jsonl"
        self.trades_path = ledger_dir / "trades.jsonl"
        self.attempts_path = ledger_dir / "attempts.jsonl"
        self.intents_path = ledger_dir / "intents.jsonl"
        self.process_id = f"{pd.Timestamp.now(tz='UTC'):%Y%m%dT%H%M%S%fZ}-{os.getpid()}"
        self._trade_rows: list[dict[str, Any]] = [
            json.loads(p) for p in read_payloads(self.trades_path)
        ]
        self._journaled: set[str] = {str(r["trade_id"]) for r in self._trade_rows}

    # -- appends -----------------------------------------------------------

    def event(self, kind: str, **fields: Any) -> dict[str, Any]:
        row = {
            "kind": kind,
            "at": self.clock().isoformat(),
            "process": self.process_id,
            **self.wiring.stamp(),
            **{k: _jsonable(v) for k, v in fields.items()},
        }
        durable_append(self.events_path, json.dumps(row, sort_keys=True, default=str))
        return row

    def events(self, kind: str | None = None) -> list[dict[str, Any]]:
        rows = [json.loads(p) for p in read_payloads(self.events_path)]
        return [r for r in rows if kind is None or r.get("kind") == kind]

    def attempt(self, attempt: ExecutionAttempt) -> None:
        durable_append(
            self.attempts_path, json.dumps(_jsonable(attempt.as_row()), sort_keys=True, default=str)
        )

    def record_trades(self, trades: Sequence[Any]) -> list[dict[str, Any]]:
        """Journal every closed trade not journaled before. Idempotent on trade_id."""
        new: list[dict[str, Any]] = []
        for trade in trades:
            trade_id = str(trade.trade_id)
            if trade_id in self._journaled:
                continue
            row = {**_jsonable(trade), **self.wiring.stamp(), "process": self.process_id}
            durable_append(self.trades_path, json.dumps(row, sort_keys=True, default=str))
            self._journaled.add(trade_id)
            self._trade_rows.append(row)
            new.append(row)
            self.event(
                "trade_closed",
                trade_id=trade_id,
                instrument=trade.instrument,
                exit_reason=trade.exit_reason,
                net_pnl=trade.net_pnl,
            )
        return new

    # -- reads -------------------------------------------------------------

    def ledger_trades(self) -> list[LedgerTrade]:
        """Every closed trade under this wiring, across restarts, for the P&L windows."""
        out: list[LedgerTrade] = []
        for row in self._trade_rows:
            exit_time = pd.Timestamp(row["exit_time"])
            if exit_time.tzinfo is None:
                exit_time = exit_time.tz_localize("UTC")
            out.append(
                LedgerTrade(
                    trade_id=str(row["trade_id"]),
                    strategy_id=str(row.get("strategy_id", "")),
                    exit_time=exit_time,
                    net_pnl=float(row["net_pnl"]),
                )
            )
        return out

    def trade_rows(self) -> list[dict[str, Any]]:
        return list(self._trade_rows)

    def last_account(self) -> dict[str, Any] | None:
        bars = self.events("bar")
        return bars[-1] if bars else None

    def equity_marks(self) -> list[tuple[pd.Timestamp, float]]:
        return [
            (pd.Timestamp(r["bar_time"]), float(r["equity"]))
            for r in self.events("bar")
            if r.get("bar_time") and r.get("equity") is not None
        ]

    # -- the session the API reads -----------------------------------------

    def write_session(
        self,
        *,
        initial_balance: float,
        account: AccountState,
        open_positions: Sequence[Any],
        marks: Mapping[str, float],
        last_bar: pd.Timestamp | None,
        extra: Mapping[str, Any],
    ) -> None:
        trades = self._trade_rows
        summary = {
            "provenance": Provenance.PAPER.value,
            "source": "fiboki paper forward",
            "account_ccy": self.wiring.account_ccy,
            "initial_balance": initial_balance,
            "balance": account.balance,
            "equity": account.equity,
            "strategy_id": ",".join(s.strategy_id for s in self.wiring.strategies),
            "instrument": ",".join(self.wiring.instruments),
            "timeframe": self.wiring.timeframe.value,
            "trades": len(trades),
            "open_positions": len(open_positions),
            "dataset_last_bar": None if last_bar is None else last_bar.isoformat(),
            "written_at": self.clock().isoformat(),
            **self.wiring.stamp(),
            **{k: _jsonable(v) for k, v in extra.items()},
        }
        trade_fields = (
            "trade_id", "strategy_id", "instrument", "direction", "size", "entry_price",
            "exit_price", "entry_time", "exit_time", "exit_reason", "gross_pnl",
            "spread_cost", "commission", "slippage_cost", "financing_cost", "net_pnl",
            "account_ccy", "provenance",
        )
        position_fields = (
            "position_id", "strategy_id", "instrument", "direction", "size", "entry_price",
            "mark_price", "entry_time", "stop_loss", "take_profit",
        )
        positions = []
        for managed in open_positions:
            pos = managed.position
            positions.append(
                {
                    "position_id": pos.position_id,
                    "strategy_id": pos.strategy_id,
                    "instrument": pos.instrument,
                    "direction": pos.direction.value,
                    "size": pos.size,
                    "entry_price": pos.entry_price,
                    "mark_price": marks.get(pos.instrument),
                    "entry_time": pos.entry_time.isoformat(),
                    "stop_loss": pos.stop_loss,
                    "take_profit": managed.active_target(),
                }
            )
        self._atomic(self.session_dir / "trades.csv", _csv(trade_fields, trades))
        self._atomic(self.session_dir / "positions.csv", _csv(position_fields, positions))
        # summary.json LAST: the reader keys a session on it.
        self._atomic(
            self.session_dir / "summary.json",
            json.dumps(summary, indent=2, sort_keys=True, default=str),
        )

    @staticmethod
    def _atomic(path: Path, text: str) -> None:
        fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise


def _csv(fields: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> str:
    if not rows:
        return ""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(fields), extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: row.get(k) for k in fields})
    return buffer.getvalue()


# ===========================================================================
# The attempt recorder: every row carries the wiring hash
# ===========================================================================


class StampedAttemptRecorder:
    """Records every gateway attempt with the wiring's hash, durably.

    ``RiskGateway._record`` builds the attempt's ``extra`` itself (it does not
    copy the context's), so the stamp is applied HERE, on the way into the
    journal: an attempt row that cannot say which reviewed wiring produced it
    is an attempt row nobody can audit.
    """

    def __init__(self, journal: ForwardJournal, stamp: Mapping[str, str]) -> None:
        self.journal = journal
        self.stamp = dict(stamp)
        self.attempts: list[ExecutionAttempt] = []

    def record(self, attempt: ExecutionAttempt) -> None:
        stamped = replace(attempt, extra={**attempt.extra, **self.stamp})
        self.attempts.append(stamped)
        self.journal.attempt(stamped)

    @property
    def blocked(self) -> list[ExecutionAttempt]:
        return [a for a in self.attempts if not a.allowed]


# ===========================================================================
# Strategy: a content-hashed document, evaluated on the feed's history
# ===========================================================================


def load_strategy(entry: StrategyWiring, strategies_dir: Path) -> StrategyDocument:
    """Load, bind defaults, and refuse unless the content hash is the wired one."""
    path = Path(entry.document)
    if not path.is_absolute():
        path = strategies_dir / path
    if not path.exists():
        raise WiringError(f"strategy document {path} does not exist")
    document = StrategyDocument.from_json(path.read_text(encoding="utf-8"))
    if document.strategy_id != entry.strategy_id:
        raise WiringError(
            f"{path} is strategy {document.strategy_id!r}, wired as {entry.strategy_id!r}"
        )
    bound = document.bind_defaults()
    actual = bound.content_hash()
    if actual != entry.content_hash:
        raise WiringError(
            f"{entry.strategy_id}: the document on disk hashes to {actual} (bound to its "
            f"declared defaults) but the wiring names {entry.content_hash}. The strategy "
            "that trades must be the one that was reviewed; update the wiring in a "
            "reviewed commit if the change is intended."
        )
    return bound


class ForwardSignalSource:
    """``(symbol, history, now) -> signals`` for ONE compiled document, closed bars only.

    Indicators are prepared over the feed's history as it stands, once per
    delivered bar, and cached so the SAME frame supplies the exit policy's
    series (the ATR a chandelier trails on) and the signal. Every indicator is
    causal (``tests/unit/test_indicator_causality.py``), so preparing over the
    history so far equals what the backtester computes on that row.
    """

    def __init__(self, document: StrategyDocument, timeframe: Timeframe, policy: ExitPolicy) -> None:
        self.document = document
        self.compiled = compile_strategy(document)
        self.timeframe = timeframe
        self.policy = policy
        self.universe = frozenset(s.upper() for s in document.universe)
        self._cache: dict[str, tuple[pd.Timestamp, pd.DataFrame]] = {}
        self.evaluations = 0
        self.signals_emitted = 0

    @property
    def warmup_period(self) -> int:
        return int(self.compiled.warmup_period)

    def prepared(self, symbol: str, history: pd.DataFrame) -> pd.DataFrame:
        newest = history.index[-1]
        cached = self._cache.get(symbol)
        if cached is not None and cached[0] == newest and len(cached[1]) == len(history):
            return cached[1]
        frame = self.compiled.prepare(history[["open", "high", "low", "close"]].astype(float))
        self._cache[symbol] = (newest, frame)
        return frame

    def exit_series(
        self, histories: Mapping[str, pd.DataFrame]
    ) -> dict[str, dict[str, float]] | None:
        needed = self.policy.needs_series
        if not needed:
            return None
        out: dict[str, dict[str, float]] = {}
        for symbol, history in histories.items():
            if symbol not in self.universe or history.empty:
                continue
            row = self.prepared(symbol, history).iloc[-1]
            out[symbol] = {c: float(row[c]) for c in needed if c in row.index}
        return out

    def __call__(self, symbol: str, history: pd.DataFrame, now: pd.Timestamp) -> list[Signal]:
        if symbol.upper() not in self.universe or history.empty:
            return []
        idx = len(history) - 1
        if idx < self.warmup_period:
            return []
        prepared = self.prepared(symbol, history)
        if prepared.index[idx] != now:
            raise AssertionError(
                f"{symbol}: the feed delivered {now} but its history ends at "
                f"{prepared.index[idx]}; refusing to signal on the wrong bar"
            )
        self.evaluations += 1
        signal = self.compiled.generate_signal(
            prepared, idx, instrument=symbol, timeframe=self.timeframe
        )
        if signal is None:
            return []
        self.signals_emitted += 1
        return [signal]


# ===========================================================================
# The seams the worker is given
# ===========================================================================


class ModelledAccount:
    """The BOOK's account, with a peak equity that survives restarts.

    Sizing and the gateway's drawdown and margin checks read this. The peak is
    carried from the journal so a restart cannot reset ``total_drawdown``;
    margin used is the book's open notional over each instrument's retail
    leverage, marked at the last close, as the paper broker computes it.
    """

    def __init__(self, manager: VenuePositionManager, *, carried_peak: float = 0.0) -> None:
        self.manager = manager
        self.peak = float(carried_peak)

    def __call__(self) -> AccountState:
        base = self.manager.account()
        self.peak = max(self.peak, base.peak_equity, base.equity)
        margin = 0.0
        for managed in self.manager.open_positions:
            pos = managed.position
            instrument = get_instrument(pos.instrument)
            bar = self.manager.driver.last_bars.get(pos.instrument)
            price = bar.close if bar is not None else pos.entry_price
            margin += abs(price * pos.size * instrument.contract_size * managed.entry_fx) / (
                instrument.retail_leverage or 1.0
            )
        return AccountState(
            balance=base.balance,
            equity=base.equity,
            currency=base.currency,
            margin_used=margin,
            open_positions=base.open_positions,
            realised_pnl=base.realised_pnl,
            unrealised_pnl=base.unrealised_pnl,
            peak_equity=self.peak,
        )


@dataclass(frozen=True, slots=True)
class _NotSubmitted:
    """A plan the BOOK would refuse, stopped before it reached the gateway or venue."""

    reason: str
    accepted: bool = False
    blocked_by_risk: bool = False
    ack: Any = None


class ManagedExecution:
    """What the worker calls ``execution``: the service, with the manager in front.

    ``LiveWorker._submit`` makes one call, ``execution.submit(plan, context)``.
    Here that call does what the backtester does before it queues an entry and
    what a venue path must do before it deals:

    1. schedule the reversal an opposite signal implies (``ReversalMode``),
       and stop if the policy is close-only;
    2. :meth:`VenuePositionManager.precheck_entry` -- a venue fills at once, so
       an entry the book would refuse (cooldown, concurrency) must not be dealt;
    3. :meth:`VenuePositionManager.submit` -- gateway, fsynced intent, venue,
       then the book adopts the whole ladder.

    Nothing here constructs an order or sizes anything.
    """

    def __init__(
        self,
        execution: ExecutionService,
        manager: VenuePositionManager,
        policy: ExitPolicy,
        journal: ForwardJournal,
    ) -> None:
        self.execution = execution
        self.manager = manager
        self.policy = policy
        self.journal = journal

    @property
    def mode(self) -> ExecutionMode:
        return self.execution.mode

    @property
    def adapter(self) -> Any:
        return self.execution.adapter

    def reconcile(self) -> Any:
        return self.execution.reconcile()

    def submit(self, plan: Any, context: Any) -> Any:
        book = self.manager.book
        scheduled = book.schedule_reversal(
            plan.instrument, plan.direction, index=self.manager.driver.bar_index
        )
        if scheduled:
            self.journal.event(
                "reversal_scheduled", instrument=plan.instrument, closes=len(scheduled)
            )
        if scheduled and self.policy.reversal is ReversalMode.CLOSE_ONLY:
            book.bump("reversal_close_only")
            return _NotSubmitted("reversal_close_only")
        refusal = self.manager.precheck_entry(plan.instrument)
        if refusal is not None:
            self.journal.event(
                "entry_refused_by_book", instrument=plan.instrument, plan_id=plan.plan_id,
                reason=refusal,
            )
            return _NotSubmitted(f"book_would_refuse:{refusal}")
        outcome = self.manager.submit(plan, context, strategy_id=plan.signal.strategy_id)
        ack = getattr(outcome, "ack", None)
        fill = getattr(ack, "fill", None)
        if getattr(outcome, "accepted", False) and fill is not None:
            self.journal.event(
                "venue_fill",
                plan_id=plan.plan_id,
                client_ref=ack.client_ref,
                broker_ref=ack.broker_ref,
                instrument=fill.instrument,
                direction=fill.direction,
                size=fill.filled_size,
                price=fill.filled_price,
                mid=fill.requested_price,
                half_spread=fill.spread_paid,
                stop_loss=plan.signal.stop_price,
                take_profit_prices=list(plan.signal.take_profit_prices),
            )
        elif not getattr(outcome, "accepted", False):
            self.journal.event(
                "submission_not_accepted",
                plan_id=plan.plan_id,
                instrument=plan.instrument,
                blocked_by_risk=bool(getattr(outcome, "blocked_by_risk", False)),
                reason=str(getattr(outcome, "reason", "")),
            )
        return outcome


class ForwardPositionReconciler:
    """``VenuePositionManager.reconcile`` without one false positive.

    Between the venue's instant fill (signal bar) and the book's modelled entry
    (next bar's open) the venue holds a position the book does not yet manage,
    and the manager reports it ``unmanaged_at_venue``. That window is by
    design, and a 15-minute reconciliation timer inside it would otherwise
    alert once per trade. A divergence is suppressed ONLY when the manager
    itself adopted that exact broker reference and is waiting for the book's
    entry; every other divergence, including an unmanaged position with any
    other reference, passes through untouched.
    """

    def __init__(self, manager: VenuePositionManager) -> None:
        self.manager = manager

    def reconcile(self, *, repair: bool = False) -> list[Any]:
        found = list(self.manager.reconcile(repair=repair))
        managed = {
            str(op.position.venue_ref) for op in self.manager.open_positions if op.position.venue_ref
        }
        awaiting = {ref for ref in self.manager.venue_positions if ref not in managed}
        return [
            d
            for d in found
            if not (getattr(d, "kind", "") == "unmanaged_at_venue" and d.broker_ref in awaiting)
        ]


class KillSwitchView:
    """``blocks_new_risk()`` / ``requires_flatten()`` over the DURABLE journal.

    Re-reads the journal on every question, so ``fiboki killswitch pause`` in
    another terminal, or the API, binds this process's very next decision.
    """

    def __init__(self, switch: KillSwitch) -> None:
        self.switch = switch

    def _state(self) -> Any:
        refresh = getattr(self.switch, "refresh", None)
        if callable(refresh):
            return refresh()
        # A KillSwitch without refresh(): replay the journal from scratch.
        return KillSwitch(self.switch.journal).state

    def blocks_new_risk(self) -> bool:
        return bool(self._state().blocks_new_risk)

    def requires_flatten(self) -> bool:
        return bool(self._state().requires_flatten)


class _CalendarGuard:
    """The gateway's event source, refusing to answer past the calendar's coverage.

    ``calendar_event_source`` answers "nothing scheduled" for a time the
    calendar knows nothing about, which on a forward run is indistinguishable
    from a quiet week. Past ``declared_end`` this raises instead, the context
    cannot be built, the cycle fails loudly and nothing opens until the
    calendar is refreshed.
    """

    def __init__(self, calendar: EconomicCalendar, horizon_minutes: float) -> None:
        self.calendar = calendar
        self.inner = calendar_event_source(calendar, horizon_minutes=horizon_minutes)
        cov = calendar.coverage()
        self.declared_end = cov.declared_end if cov.declared_end is not None else cov.last_event

    def __call__(self, instrument: str, now: pd.Timestamp) -> tuple[pd.Timestamp, ...]:
        if self.declared_end is not None and now > self.declared_end:
            raise CalendarError(
                f"the official economic calendar is declared only to {self.declared_end}; "
                f"it cannot vouch for {now}. No new position opens until it is refreshed."
            )
        return self.inner(instrument, now)


class ForwardFeed:
    """The worker's ``BarFeed``: poll OANDA, sample pricing, advance the book.

    The ORDER is the backtester's per-bar order. The book must see the closed
    bar first -- financing, entries queued on the previous bar filling at THIS
    bar's open, exits -- and the venue must be told, BEFORE the strategy is
    asked for signals on the same bar. Evaluating first would let an entry
    signalled on bar i be modelled at bar i's open, which is look-ahead.
    """

    def __init__(
        self,
        *,
        feed: OandaPollingBarFeed,
        spread: OandaPricingSpreadSource,
        manager: VenuePositionManager,
        builder: RiskContextBuilder,
        signals: ForwardSignalSource,
        journal: ForwardJournal,
        kill_switch: KillSwitchView,
        account: ModelledAccount,
        initial_balance: float,
        wiring: PaperForwardWiring,
        dispatcher: Any = None,
        equity_marks: Sequence[tuple[pd.Timestamp, float]] = (),
        session_extra: Mapping[str, Any] | None = None,
    ) -> None:
        self.feed = feed
        self.spread = spread
        self.manager = manager
        self.builder = builder
        self.signals = signals
        self.journal = journal
        self.kill_switch = kill_switch
        self.account = account
        self.initial_balance = initial_balance
        self.wiring = wiring
        self.dispatcher = dispatcher
        self.timeframe = feed.timeframe
        self.bar_index = -1
        self.last_bar_time: pd.Timestamp | None = None
        self.equity_marks: list[tuple[pd.Timestamp, float]] = list(equity_marks)
        self.session_extra = dict(session_extra or {})
        self.flattens = 0

    # -- BarFeed -------------------------------------------------------------

    def wait_until_due(self, should_stop: Callable[[], bool] | None = None) -> bool:
        # A FLATTEN must not wait for the next bar boundary (up to four hours).
        self._flatten_if_required(reason="kill_switch_flatten")
        return self.feed.wait_until_due(should_stop)

    def history(self, symbol: str) -> pd.DataFrame:
        return self.feed.history(symbol)

    def poll(self) -> BarBatch:
        batch = self.feed.poll()
        sample = self.spread.sample(self.wiring.instruments)
        self.journal.event("pricing", sample=sample.as_row())
        if sample.error or sample.missing:
            self._alert(
                AlertEvent.BROKER_UNHEALTHY,
                (
                    f"OANDA practice pricing sample incomplete (missing "
                    f"{list(sample.missing)}; {sample.error or 'no quote returned'}). "
                    "abnormal_spread blocks those instruments until a fresh quote arrives."
                ),
                dedupe_key="paper_forward_pricing",
            )
        if batch.frames:
            self.bar_index += 1
            stamp = max(bar.timestamp for bar in batch.frames.values())
            histories = {sym: self.feed.history(sym) for sym in batch.frames}
            cycle = self.manager.on_bar(
                batch.frames,
                bar_index=self.bar_index,
                timestamp=stamp,
                series=self.signals.exit_series(histories),
            )
            self.last_bar_time = stamp + pd.Timedelta(minutes=self.wiring.timeframe.minutes)
            self._journal_cycle(cycle, stamp)
        self._refresh_correlation()
        self._flatten_if_required(reason="kill_switch_flatten")
        self.write_session()
        return batch

    # -- pieces ------------------------------------------------------------

    def _journal_cycle(self, cycle: Any, stamp: pd.Timestamp) -> None:
        for event in getattr(cycle.advance, "entries", ()) or ():
            if getattr(event, "filled", False) and getattr(event, "managed", None) is not None:
                pos = event.managed.position
                self.journal.event(
                    "position_opened",
                    broker_ref=pos.venue_ref,
                    position_id=pos.position_id,
                    instrument=pos.instrument,
                    direction=pos.direction,
                    size=pos.size,
                    modelled_entry=pos.entry_price,
                    stop_loss=pos.stop_loss,
                    bar_time=stamp,
                )
        self._journal_instructions(cycle)
        self.journal.record_trades(self.manager.book.trades)
        account = self.account()
        self.equity_marks.append((stamp, account.equity))
        self.journal.event(
            "bar",
            bar_time=stamp,
            balance=account.balance,
            equity=account.equity,
            peak_equity=account.peak_equity,
            open_positions=[
                {
                    "position_id": op.position.position_id,
                    "broker_ref": op.position.venue_ref,
                    "instrument": op.position.instrument,
                    "direction": op.position.direction,
                    "size": op.position.size,
                    "entry_price": op.position.entry_price,
                }
                for op in self.manager.open_positions
            ],
        )

    def _refresh_correlation(self) -> None:
        histories = {}
        for sym in self.wiring.instruments:
            try:
                histories[sym] = self.feed.history(sym)
            except KeyError:
                continue
        self.builder.correlation = correlation_from_frames(
            histories,
            min_observations=self.wiring.correlation_min_observations,
            default=self.wiring.correlation_default,
        )

    def _flatten_if_required(self, *, reason: str) -> None:
        if not self.manager.open_positions or not self.kill_switch.requires_flatten():
            return
        if self.manager.now is None:
            return
        cycle = self.manager.flatten(
            dict(self.manager.driver.last_bars), reason=ExitReason.RISK_HALT
        )
        self.flattens += 1
        self.journal.event(
            "kill_switch_flatten",
            reason=reason,
            closed=[leg.position_seq for leg in getattr(cycle.advance, "legs", ())],
        )
        self._journal_instructions(cycle)
        self.journal.record_trades(self.manager.book.trades)
        self.write_session()

    def _journal_instructions(self, cycle: Any) -> None:
        """Every amend/close the manager sent the venue, with its telemetry."""
        for record in getattr(cycle, "telemetry", ()) or ():
            row = record.as_row() if hasattr(record, "as_row") else record
            self.journal.event("venue_instruction", telemetry=dict(row))

    def write_session(self) -> None:
        self.journal.write_session(
            initial_balance=self.initial_balance,
            account=self.account(),
            open_positions=self.manager.open_positions,
            marks=dict(self.builder.last_closes),
            last_bar=self.last_bar_time,
            extra={
                **self.session_extra,
                "risk_inputs_live": self.builder.risk_inputs_live(),
                "spread_source": "oanda_practice_pricing",
            },
        )

    def equity_curve(self) -> pd.Series:
        if not self.equity_marks:
            return pd.Series(dtype=float)
        return pd.Series(
            [e for _t, e in self.equity_marks],
            index=pd.DatetimeIndex([t for t, _e in self.equity_marks]),
        )

    def _alert(self, event: AlertEvent, message: str, **kwargs: Any) -> None:
        if self.dispatcher is not None:
            self.dispatcher.fire(event, message, source="paper-forward", **kwargs)


class PaperForwardWorker(LiveWorker):
    """A :class:`LiveWorker` that journals its start and closes its transport."""

    runtime: PaperForwardRuntime

    def resume(self) -> None:
        super().resume()
        runtime = self.runtime
        runtime.journal.event(
            "session_start",
            worker_id=self.worker_id,
            initial_balance=runtime.initial_balance,
            carried_balance=runtime.carried_balance,
            restarts=runtime.restarts,
            abandoned_positions=runtime.abandoned_positions,
            instruments=list(runtime.wiring.instruments),
            timeframe=runtime.wiring.timeframe.value,
            strategy_content_hashes=[s.content_hash for s in runtime.wiring.strategies],
            limits_version=runtime.wiring.limits_version,
            broker_profile=runtime.wiring.broker_profile,
        )
        runtime.forward.write_session()

    def teardown(self) -> None:
        try:
            self.runtime.forward.write_session()
            self.runtime.journal.event("session_stop", summary=self.summary())
        finally:
            close = getattr(self.runtime.transport, "close", None)
            if callable(close):
                close()


@dataclass
class PaperForwardRuntime:
    """Every assembled part, kept so the CLI and the tests can inspect it."""

    wiring: PaperForwardWiring
    worker: PaperForwardWorker
    feed: OandaPollingBarFeed
    forward: ForwardFeed
    venue: ForwardPaperVenue
    manager: VenuePositionManager
    execution: ExecutionService
    managed_execution: ManagedExecution
    gateway: RiskGateway
    recorder: StampedAttemptRecorder
    builder: RiskContextBuilder
    evaluator: SignalEvaluator
    signals: ForwardSignalSource
    spread: OandaPricingSpreadSource
    journal: ForwardJournal
    kill_switch: KillSwitch
    transport: Any
    initial_balance: float
    carried_balance: float | None = None
    restarts: int = 0
    abandoned_positions: list[dict[str, Any]] = field(default_factory=list)


# ===========================================================================
# compose
# ===========================================================================


def _state_dir(settings: Any) -> Path:
    state = getattr(settings, "state_dir", None)
    if state is None:
        raise WiringError("settings has no state_dir")
    return Path(state)


def _killswitch_path(settings: Any) -> Path:
    for attr in ("killswitch_path", "killswitch_journal"):
        value = getattr(settings, attr, None)
        if value:
            return Path(value)
    paths = getattr(settings, "paths", None)
    if paths is not None and getattr(paths, "killswitch_journal", None):
        return Path(paths.killswitch_journal)
    return _state_dir(settings) / "killswitch.jsonl"


def _health(settings: Any) -> Any:
    """``settings.health`` (THE observability thresholds), or the shared default."""
    from fiboki.obs.health import DEFAULT_HEALTH_THRESHOLDS

    return getattr(settings, "health", None) or DEFAULT_HEALTH_THRESHOLDS


def _paper_root(settings: Any) -> Path:
    root = getattr(settings, "paper_root", None)
    return Path(root) if root else _state_dir(settings) / "paper"


def _credential_env(env: Mapping[str, str]) -> dict[str, str]:
    """``env`` with the FIBOKI_* credential names filled from the legacy names.

    A legacy name is used only when its FIBOKI_* replacement is unset or empty,
    and every such use logs one warning naming both variables (never a value).
    """
    out = dict(env)
    for new, old in ((ENV_TOKEN, LEGACY_ENV_TOKEN), (ENV_ACCOUNT, LEGACY_ENV_ACCOUNT)):
        if str(out.get(new, "") or "").strip():
            continue
        if str(out.get(old, "") or "").strip():
            out[new] = str(out[old])
            _log.warning(
                "deprecated credential name in use; rename it before the next release",
                extra={"deprecated": old, "use": new},
            )
    return out


def compose_runtime(
    settings: Any,
    wiring: PaperForwardWiring | str | Path = DEFAULT_WIRING,
    *,
    store: WorkerStore,
    environ: Mapping[str, str] | None = None,
    transport: Any = None,
    dispatcher: Any = None,
    strategies_dir: str | Path | None = None,
    calendar: EconomicCalendar | None = None,
    clock: Callable[[], pd.Timestamp] | None = None,
    sleeper: Callable[[float], None] | None = None,
    monotonic: Callable[[], float] | None = None,
    worker_id: str | None = None,
    max_cycles: int = 0,
) -> PaperForwardRuntime:
    """Assemble the paper-forward runtime from ``settings`` and a wiring file.

    ``transport`` defaults to an :class:`HttpxTransport` allowed to reach the
    OANDA practice host only; tests pass a
    :class:`~fiboki.broker.oanda.RecordedTransport`. ``clock``, ``sleeper`` and
    ``monotonic`` default to the wall clock and real sleep.
    """
    import time as _time

    wired = wiring if isinstance(wiring, PaperForwardWiring) else load_wiring(wiring)
    env = _credential_env(os.environ if environ is None else environ)
    clock = clock or (lambda: pd.Timestamp.now(tz="UTC"))
    sleeper = sleeper or _time.sleep
    monotonic = monotonic or _time.monotonic

    # -- credentials, read once -------------------------------------------
    token = bearer_token_from_env(env, ENV_TOKEN)
    account_id = str(env.get(ENV_ACCOUNT, "") or "").strip()
    if not account_id:
        raise WiringError(
            f"{ENV_ACCOUNT} is not set; the pricing read needs the practice account id"
        )

    # -- the reviewed pieces ------------------------------------------------
    limits = get_limit_set(wired.limits_version)
    profile = get_profile(wired.broker_profile)
    (entry,) = wired.strategies
    document = load_strategy(entry, Path(strategies_dir) if strategies_dir else _REPO_STRATEGIES)
    policy = exit_policy_from_document(document)
    if wired.timeframe not in document.timeframes:
        raise WiringError(
            f"{document.strategy_id} permits {[t.value for t in document.timeframes]}, "
            f"not {wired.timeframe.value}"
        )
    outside = [s for s in wired.instruments if s not in {u.upper() for u in document.universe}]
    if outside:
        raise WiringError(f"{outside} are outside {document.strategy_id}'s universe")
    for symbol in wired.instruments:
        if get_instrument(symbol).quote != wired.account_ccy:
            raise WiringError(
                f"{symbol} is quoted in {get_instrument(symbol).quote}, the account in "
                f"{wired.account_ccy}. v1 wiring converts nothing (IdentityFxSource); "
                "a conversion source is a reviewed addition, not a guess."
            )
    fx = IdentityFxSource()

    calendar = calendar or load_official_calendar()
    now = clock()
    try:
        calendar.assert_populated(
            start=now,
            end=now + pd.Timedelta(days=wired.calendar_min_coverage_days),
            currencies=sorted({c for s in wired.instruments for c in instrument_currencies(s)}),
        )
    except CalendarError as exc:
        raise WiringError(
            f"the official economic calendar cannot vouch for the next "
            f"{wired.calendar_min_coverage_days:g} days: {str(exc).splitlines()[0]}"
        ) from exc

    # -- durable state -------------------------------------------------------
    state_dir = _state_dir(settings)
    journal = ForwardJournal(
        ledger_dir=state_dir / wired.ledger_dir,
        session_dir=_paper_root(settings) / f"forward-{wired.version}",
        wiring=wired,
        clock=clock,
    )
    previous = journal.last_account()
    carried_balance = None if previous is None else float(previous["balance"])
    carried_peak = 0.0 if previous is None else float(previous.get("peak_equity") or 0.0)
    abandoned = list(previous.get("open_positions") or []) if previous else []
    restarts = len(journal.events("session_start"))
    initial_balance = wired.initial_balance
    book_balance = carried_balance if carried_balance is not None else initial_balance
    if abandoned:
        journal.event(
            "positions_abandoned_at_restart",
            positions=abandoned,
            note=(
                "the book and the paper venue are in memory; these positions were open "
                "at the last journalled bar and died with the previous process. Their "
                "unrealised P&L is NOT carried."
            ),
        )
        if dispatcher is not None:
            dispatcher.fire(
                AlertEvent.RECONCILIATION_DIVERGENCE,
                f"paper forward restarted with {len(abandoned)} paper position(s) "
                "open at the last bar; they were abandoned, not recovered",
                severity=Severity.WARNING,
                source="paper-forward",
                dedupe_key="paper_forward_abandoned",
            )

    # -- the network: practice host only ------------------------------------
    if transport is None:
        transport = HttpxTransport(allowed_hosts=(OANDA_PRACTICE_HOST,))
    limiter = RateLimiter(
        wired.max_requests_per_second, clock=monotonic, sleeper=sleeper
    )

    def _retry() -> ReadRetry:
        return ReadRetry(rate_limiter=limiter, clock=monotonic, sleeper=sleeper)

    provider = OandaCandlesProvider(
        api_token=token,
        account_id=account_id,
        host=wired.market_data_base_url,
        http_client=TransportHttpClient(transport),
    )
    feed_cfg = wired.feed
    feed = OandaPollingBarFeed(
        provider,
        PollingFeedConfig(
            instruments=wired.instruments,
            timeframe=wired.timeframe,
            offset_s=feed_cfg.get("offset_s", 5.0),
            late_candle_grace_s=feed_cfg.get("late_candle_grace_s", 20.0),
            repoll_attempts=int(feed_cfg.get("repoll_attempts", 3)),
            repoll_interval_s=feed_cfg.get("repoll_interval_s", 5.0),
            max_block_s=feed_cfg.get("max_block_s", 30.0),
            warmup_bars=int(feed_cfg.get("warmup_bars", 500)),
            history_bars=int(feed_cfg.get("history_bars", 1500)),
        ),
        clock=clock,
        sleeper=sleeper,
        read_retry=_retry(),
        dispatcher=dispatcher,
        source="paper-forward-feed",
    )
    spread = OandaPricingSpreadSource(
        transport=transport,
        account_id=account_id,
        api_token=token,
        base_url=wired.market_data_base_url,
        read_retry=_retry(),
        clock=clock,
        max_quote_age_s=wired.max_quote_age_s,
    )

    # -- the book, the venue, the manager -----------------------------------
    book = PositionBook(
        sim=FillSimulator(profile=profile, intrabar_policy=IntrabarPolicy.STOP_FIRST),
        fx=fx,
        config=BookConfig(
            account_ccy=wired.account_ccy,
            max_concurrent=wired.max_concurrent,
            max_per_instrument=document.position_management.max_concurrent_positions,
            charge_financing=True,
            financing_rollover_hour_utc=21,
            strategy_id=document.strategy_id,
            provenance=Provenance.PAPER,
        ),
        policy=policy,
        blackout=calendar,
        initial_balance=book_balance,
        latency_bars=profile.latency_bars,
        financing_profile=profile.financing,
    )
    holder: dict[str, Any] = {}
    venue = ForwardPaperVenue(
        profile=profile,
        account=lambda: holder["account"](),
        clock=clock,
        strategy_id=document.strategy_id,
    )
    switch = KillSwitch(FileKillSwitchJournal(_killswitch_path(settings)))
    recorder = StampedAttemptRecorder(journal, wired.stamp())
    # mode=PAPER: the gateway refuses an in-memory journal and every attempt
    # row names the mode. PAPER composition: the explicit, recorded permission
    # for an order to pass with a missing loss/exposure input (each attempt row
    # carries ``missing_inputs`` and ``paper_allows_missing_inputs``).
    gateway = RiskGateway(
        kill_switch=switch,
        recorder=recorder,
        limits=limits,
        mode=ExecutionMode.PAPER,
        paper_allows_missing_inputs=True,
    )
    execution = ExecutionService(
        adapter=venue,
        gateway=gateway,
        store=JsonlIntentStore(journal.intents_path),
        mode=ExecutionMode.PAPER,
    )

    sizing = SizingPolicy(risk_fraction=wired.risk_fraction, policy_id=wired.sizing_policy_id)
    builder = RiskContextBuilder(
        adapter=venue,
        clock=clock,
        fx=fx,
        account_ccy=wired.account_ccy,
        limits=limits,
        mode=ExecutionMode.PAPER,
        spread_source=spread,
        event_source=_CalendarGuard(
            calendar, event_source_horizon_minutes(limits, float(wired.timeframe.minutes))
        ),
        default_lifecycle=StrategyLifecycle.PAPER,
        pnl_ledger=RealisedPnlLedger(journal.ledger_trades, account_ccy=wired.account_ccy),
        correlation=CorrelationMatrix(default=wired.correlation_default),
        # open_risk under the SAME rule the evaluator sizes new plans with.
        sizing_policy=sizing,
    )

    def _exit_context(position: Any, kind: str) -> ExitContext:
        at = clock()
        instrument = get_instrument(position.instrument)
        return ExitContext(
            instrument=position.instrument,
            strategy_id=position.strategy_id or document.strategy_id,
            size=position.size,
            now=at,
            mode=ExecutionMode.PAPER,
            position_id=position.position_id,
            limits=limits,
            market=builder.market_view(instrument, at),
            venue=builder.venue_view(),
            request_kind=(
                RequestKind.PROTECTIVE_AMEND
                if kind == "amend"
                else RequestKind.REDUCE
                if kind == "reduce"
                else RequestKind.CLOSE
            ),
            extra={"reason": kind, **wired.stamp()},
        )

    manager = VenuePositionManager(
        execution=execution,
        book=book,
        context_factory=_exit_context,
        fx=fx,
        account_ccy=wired.account_ccy,
        latency_bars=profile.latency_bars,
        retry=RetryPolicy(),
        clock=clock,
        sleeper=sleeper,
        alert=(
            (lambda key, message, detail: dispatcher.fire(
                AlertEvent.RECONCILIATION_DIVERGENCE, message, source="paper-forward",
                dedupe_key=key,
            ))
            if dispatcher is not None
            else None
        ),
    )
    tf = pd.Timedelta(minutes=wired.timeframe.minutes)
    for symbol in wired.instruments:
        manager.set_bar_interval(symbol, tf)
    account = ModelledAccount(manager, carried_peak=carried_peak)
    holder["account"] = account

    signals = ForwardSignalSource(document, wired.timeframe, policy)
    # The constructor sees the book the gateway will (builder.snapshot), the
    # lifecycle view it will (builder.strategy_view) and the regime source the
    # contexts carry (none is wired here: every candidate's regime is
    # "unknown", as the module docstring states). Without these the allocator
    # sized every signal against an empty book.
    evaluator = SignalEvaluator(
        source=signals,
        account=account,
        fx=fx,
        policy=sizing,
        history=feed.history,
        account_ccy=wired.account_ccy,
        snapshot=builder.snapshot,
        strategy=builder.strategy_view,
        regime=builder.regime_source,
    )
    feed.attach(builder)
    feed.attach(venue)

    kill_view = KillSwitchView(switch)
    forward = ForwardFeed(
        feed=feed,
        spread=spread,
        manager=manager,
        builder=builder,
        signals=signals,
        journal=journal,
        kill_switch=kill_view,
        account=account,
        initial_balance=initial_balance,
        wiring=wired,
        dispatcher=dispatcher,
        equity_marks=journal.equity_marks(),
        session_extra={"restarts": restarts, "abandoned_at_restart": len(abandoned)},
    )
    builder.equity_curve = forward.equity_curve
    managed = ManagedExecution(execution, manager, policy, journal)

    wcfg = wired.worker
    worker = PaperForwardWorker(
        execution_service=managed,
        feed=forward,
        evaluator=evaluator,
        context_builder=builder,
        store=store,
        kill_switch=kill_view,
        position_reconciler=ForwardPositionReconciler(manager),
        config=LiveWorkerConfig(
            kind="paper",
            lease_name=str(wcfg.get("lease_name", "paper-forward")),
            lease_ttl_seconds=float(wcfg.get("lease_ttl_seconds", 90.0)),
            idle_sleep_seconds=float(wcfg.get("idle_sleep_seconds", 1.0)),
            reconcile_interval_seconds=float(wcfg.get("reconcile_interval_seconds", 900.0)),
            reconcile_every_cycles=int(wcfg.get("reconcile_every_cycles", 12)),
            allowed_modes=wired.allowed_modes,
            max_cycles=int(max_cycles),
            # Settings.health: the same DATA_STALE threshold the health page
            # and the watchdog read (FIBOKI_DATA_STALE_SECONDS).
            data_stale_after_seconds=_health(settings).data_stale_after_seconds,
        ),
        dispatcher=dispatcher,
        worker=worker_id,
        monotonic=monotonic,
    )
    runtime = PaperForwardRuntime(
        wiring=wired,
        worker=worker,
        feed=feed,
        forward=forward,
        venue=venue,
        manager=manager,
        execution=execution,
        managed_execution=managed,
        gateway=gateway,
        recorder=recorder,
        builder=builder,
        evaluator=evaluator,
        signals=signals,
        spread=spread,
        journal=journal,
        kill_switch=switch,
        transport=transport,
        initial_balance=initial_balance,
        carried_balance=carried_balance,
        restarts=restarts,
        abandoned_positions=abandoned,
    )
    worker.runtime = runtime
    _log.info(
        "paper forward composed",
        extra={
            "wiring": str(wired.path),
            "wiring_sha256": wired.sha256,
            "strategy": document.strategy_id,
            "strategy_content_hash": entry.content_hash,
            "instruments": list(wired.instruments),
            "timeframe": wired.timeframe.value,
            "limits": limits.version,
            "profile": profile.name,
            "restarts": restarts,
        },
    )
    return runtime


def compose(
    settings: Any,
    wiring: PaperForwardWiring | str | Path = DEFAULT_WIRING,
    **seams: Any,
) -> LiveWorker:
    """``compose(settings, wiring) -> LiveWorker``. See :func:`compose_runtime`."""
    return compose_runtime(settings, wiring, **seams).worker


def wiring_digest(path: str | Path) -> str:
    """sha256 of a wiring file's bytes (what every attempt row is stamped with)."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
