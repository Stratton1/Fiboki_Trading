"""Read-only reader for persisted PAPER sessions.

WHAT THIS READS
---------------
A *paper root* (``FIBOKI_PAPER_ROOT``, default ``<FIBOKI_STATE_DIR>/paper``)
holding one directory per session, in the format
``scripts/run_paper_session.py`` writes:

``summary.json``
    the session's account (``initial_balance``, ``balance``, ``equity``,
    ``account_ccy``), its ``provenance``, and the replayed data window
    (``dataset_last_bar``).
``trades.csv``
    every closed trade, one per row, timestamps as integer nanoseconds since
    the epoch (or ISO 8601), costs broken out per component.
``positions.csv``
    positions still open when the session ended. Either flat columns or the
    venue's book record with the position itself JSON-encoded in a
    ``position`` column. An empty file means no open positions.

``exit_legs.csv`` and ``telemetry.jsonl`` may sit alongside and are not read
here. Any other file in the root (a README, the ``session.sqlite`` worker
store) is ignored; only a directory containing ``summary.json`` or
``trades.csv`` is a session.

WHAT THIS DOES NOT DO
---------------------
It never writes, never repairs and never assumes a provenance. A session whose
summary does not state its provenance, or whose rows disagree with it, is
refused and reported as an error rather than labelled PAPER on a guess. A
session that cannot be parsed is reported, not skipped silently. The reader
has no dependency on the broker, risk or worker packages: it reads what the
paper runtime persisted and nothing else.
"""
from __future__ import annotations

import csv
import json
import math
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fiboki.core.enums import Direction, ExitReason, Provenance

__all__ = [
    "JournalPosition",
    "JournalTrade",
    "PaperAccount",
    "PaperJournal",
    "PaperJournalReader",
    "PaperSession",
]

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_COST_FIELDS = ("spread_cost", "commission", "slippage_cost", "financing_cost")


class JournalFormatError(ValueError):
    """A session directory exists but does not parse. Reported, never hidden."""


# ------------------------------------------------------------------ rows


@dataclass(frozen=True, slots=True)
class JournalTrade:
    """One closed paper trade, with the attributes the trading routes read.

    The first fifteen fields mirror :class:`fiboki.api.seed.TradeRow` so the
    routes need no second code path; the rest say where the row came from.
    """

    trade_id: str
    strategy_id: str
    instrument: str
    direction: Direction
    size: float
    entry_price: float
    exit_price: float
    entry_time: datetime
    exit_time: datetime
    exit_reason: ExitReason | str
    net_pnl: float
    gross_pnl: float
    costs: float
    #: ``None``: the persisted trade does not carry the risk taken, so an R
    #: multiple cannot be computed honestly. Never zero.
    r_multiple: float | None
    provenance: Provenance
    account_ccy: str
    session_id: str
    #: ``units`` of the instrument (contract size 1), not FX lots.
    size_unit: str = "units"
    as_of: datetime | None = None
    cost_breakdown: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class JournalPosition:
    position_id: str
    strategy_id: str
    instrument: str
    direction: Direction
    size: float
    entry_price: float
    #: ``None``: the session did not persist a mark. Unknown, not the entry.
    mark_price: float | None
    entry_time: datetime
    stop_loss: float | None
    take_profit: float | None
    #: The venue's own mark-to-market (equity minus balance) when the session
    #: has exactly one open position; otherwise ``None``.
    unrealised_pnl: float | None
    provenance: Provenance
    account_ccy: str
    session_id: str
    size_unit: str = "units"
    as_of: datetime | None = None


@dataclass(frozen=True, slots=True)
class PaperSession:
    session_id: str
    path: Path
    provenance: Provenance
    strategy_id: str
    instrument: str
    timeframe: str
    account_ccy: str
    initial_balance: float | None
    balance: float | None
    equity: float | None
    #: The last bar the session saw: the market time its state is true at.
    as_of: datetime | None
    #: When the summary was written (file mtime, UTC).
    written_at: datetime
    trades: tuple[JournalTrade, ...]
    positions: tuple[JournalPosition, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PaperAccount:
    """The account figures across every loaded session.

    Each session is an independent account. The totals are sums of those
    accounts, which is only meaningful in one currency: with mixed currencies
    every money field is ``None`` and a warning says why.
    """

    sessions: int
    account_ccy: str | None
    initial_balance: float | None
    balance: float | None
    equity: float | None
    realised_pnl: float | None
    unrealised_pnl: float | None
    as_of: datetime | None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PaperJournal:
    root: Path
    sessions: tuple[PaperSession, ...]
    #: ``(session directory name, error)`` for every session that failed.
    errors: tuple[tuple[str, str], ...] = ()

    @property
    def trades(self) -> list[JournalTrade]:
        return [t for s in self.sessions for t in s.trades]

    @property
    def positions(self) -> list[JournalPosition]:
        return [p for s in self.sessions for p in s.positions]

    @property
    def as_of(self) -> datetime | None:
        stamps = [s.as_of for s in self.sessions if s.as_of is not None]
        return max(stamps) if stamps else None

    def account(self) -> PaperAccount:
        warnings: list[str] = []
        sessions = self.sessions
        currencies = {s.account_ccy for s in sessions}
        if len(sessions) > 1:
            warnings.append(
                f"Aggregated across {len(sessions)} independent paper sessions, each "
                "with its own account. Their risk limits were not shared, so the "
                "total is a sum of separate books, not one portfolio."
            )
        for s in sessions:
            warnings.extend(f"{s.session_id}: {w}" for w in s.warnings)
            if s.positions and s.equity is not None and s.balance is not None:
                oldest = min(p.entry_time for p in s.positions)
                warnings.append(
                    f"{s.session_id}: equity includes {s.equity - s.balance:,.2f} "
                    f"{s.account_ccy} unrealised on {len(s.positions)} position(s) still "
                    f"open when the session ended, the oldest opened "
                    f"{oldest.date().isoformat()}. It is a mark-to-market, not a "
                    "realised result."
                )
        if len(currencies) != 1:
            warnings.append(
                "Sessions are denominated in different currencies ("
                + ", ".join(sorted(currencies))
                + "); no conversion is applied, so no account total is reported."
            )
            return PaperAccount(
                sessions=len(sessions),
                account_ccy=None,
                initial_balance=None,
                balance=None,
                equity=None,
                realised_pnl=None,
                unrealised_pnl=None,
                as_of=self.as_of,
                warnings=tuple(warnings),
            )

        def total(values: Iterable[float | None]) -> float | None:
            items = list(values)
            if not items or any(v is None for v in items):
                return None
            return round(sum(v for v in items if v is not None), 2)

        initial = total(s.initial_balance for s in sessions)
        balance = total(s.balance for s in sessions)
        equity = total(s.equity for s in sessions)
        realised = round(sum(t.net_pnl for t in self.trades), 2)
        unrealised = (
            round(equity - balance, 2) if equity is not None and balance is not None else None
        )
        return PaperAccount(
            sessions=len(sessions),
            account_ccy=next(iter(currencies)),
            initial_balance=initial,
            balance=balance,
            equity=equity,
            realised_pnl=realised,
            unrealised_pnl=unrealised,
            as_of=self.as_of,
            warnings=tuple(warnings),
        )


# ---------------------------------------------------------------- parsing


def _float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if text == "" or text.lower() in {"none", "null", "nan"}:
        return None
    number = float(text)
    return None if math.isnan(number) else number


def _required_float(row: dict[str, Any], key: str) -> float:
    value = _float(row.get(key))
    if value is None:
        raise JournalFormatError(f"missing numeric field {key!r}")
    return value


def _timestamp(value: Any) -> datetime:
    """Integer nanoseconds since the epoch, or ISO 8601. Naive means UTC."""
    if value is None or str(value).strip() == "":
        raise JournalFormatError("missing timestamp")
    text = str(value).strip()
    if text.lstrip("-").isdigit():
        ns = int(text)
        return _EPOCH + timedelta(microseconds=ns // 1000)
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError as exc:
        raise JournalFormatError(f"unparseable timestamp {text!r}") from exc
    return stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp.astimezone(UTC)


def _direction(value: Any) -> Direction:
    try:
        return Direction(str(value).strip().lower())
    except ValueError as exc:
        raise JournalFormatError(f"unknown direction {value!r}") from exc


def _exit_reason(value: Any) -> ExitReason | str:
    """The ExitReason set is open: an unknown value is kept, not rejected."""
    text = str(value or "").strip()
    try:
        return ExitReason(text)
    except ValueError:
        return text or "unknown"


def _provenance(value: Any, where: str) -> Provenance:
    try:
        return Provenance(str(value).strip().lower())
    except ValueError as exc:
        raise JournalFormatError(f"{where}: unknown provenance {value!r}") from exc


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _first_target(raw: Any) -> float | None:
    """First take-profit from a list of prices or of ``{"price": ...}`` dicts."""
    if raw is None or raw == "":
        return None
    items = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(items, list) or not items:
        return None
    first = items[0]
    if isinstance(first, dict):
        return _float(first.get("price"))
    return _float(first)


def _mtime(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)


# ---------------------------------------------------------------- reader


class PaperJournalReader:
    """Loads every session under a paper root. Read-only; never raises on a
    malformed session, but records it in :attr:`PaperJournal.errors`."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def exists(self) -> bool:
        return self.root.is_dir()

    def session_dirs(self) -> list[Path]:
        if not self.exists():
            return []
        return sorted(
            p
            for p in self.root.iterdir()
            if p.is_dir()
            and not p.name.startswith(".")
            and ((p / "summary.json").exists() or (p / "trades.csv").exists())
        )

    def signature(self) -> tuple[tuple[str, int, int], ...]:
        """Cheap change detector: name, mtime and size of each read file."""
        out: list[tuple[str, int, int]] = []
        for directory in self.session_dirs():
            for name in ("summary.json", "trades.csv", "positions.csv"):
                path = directory / name
                try:
                    stat = path.stat()
                except OSError:
                    continue
                out.append((str(path), stat.st_mtime_ns, stat.st_size))
        return tuple(out)

    def load(self) -> PaperJournal | None:
        """``None`` when no journal exists at all (no root, or no session dirs)."""
        directories = self.session_dirs()
        if not directories:
            return None
        sessions: list[PaperSession] = []
        errors: list[tuple[str, str]] = []
        for directory in directories:
            try:
                sessions.append(self._load_session(directory))
            except (JournalFormatError, OSError, ValueError, KeyError, TypeError) as exc:
                errors.append((directory.name, f"{type(exc).__name__}: {exc}"))
        return PaperJournal(root=self.root, sessions=tuple(sessions), errors=tuple(errors))

    # -- one session -----------------------------------------------------

    def _load_session(self, directory: Path) -> PaperSession:
        session_id = directory.name
        summary_path = directory / "summary.json"
        if not summary_path.exists():
            raise JournalFormatError(
                "no summary.json, so the session's provenance and account are unknown"
            )
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        if not isinstance(summary, dict):
            raise JournalFormatError("summary.json is not an object")
        if not summary.get("provenance"):
            raise JournalFormatError(
                "summary.json does not state a provenance; refusing to assume PAPER"
            )
        provenance = _provenance(summary["provenance"], "summary.json")
        account_ccy = str(summary.get("account_ccy") or "").upper()
        if not account_ccy:
            raise JournalFormatError("summary.json does not state account_ccy")

        as_of: datetime | None = None
        if summary.get("dataset_last_bar"):
            as_of = _timestamp(summary["dataset_last_bar"])

        trades = self._trades(directory, session_id, provenance, account_ccy, summary)
        if as_of is None and trades:
            as_of = max(t.exit_time for t in trades)
        if trades and as_of is not None:
            trades = tuple(_with_as_of(t, as_of) for t in trades)

        balance = _float(summary.get("balance"))
        equity = _float(summary.get("equity"))
        positions = self._positions(
            directory, session_id, provenance, account_ccy, summary, balance, equity, as_of
        )

        warnings: list[str] = []
        initial = _float(summary.get("initial_balance"))
        if initial is not None and balance is not None and trades:
            realised = sum(t.net_pnl for t in trades)
            gap = (balance - initial) - realised
            if abs(gap) > 0.01 + 1e-9 * abs(balance):
                warnings.append(
                    f"closing balance minus initial balance differs from the sum of "
                    f"closed-trade net P&L by {gap:,.2f} {account_ccy}"
                )
        declared = summary.get("trades")
        if isinstance(declared, int) and declared != len(trades):
            warnings.append(
                f"summary.json declares {declared} trades but trades.csv holds {len(trades)}"
            )

        return PaperSession(
            session_id=session_id,
            path=directory,
            provenance=provenance,
            strategy_id=str(summary.get("strategy_id") or ""),
            instrument=str(summary.get("instrument") or ""),
            timeframe=str(summary.get("timeframe") or ""),
            account_ccy=account_ccy,
            initial_balance=initial,
            balance=balance,
            equity=equity,
            as_of=as_of,
            written_at=_mtime(summary_path),
            trades=trades,
            positions=positions,
            warnings=tuple(warnings),
        )

    def _trades(
        self,
        directory: Path,
        session_id: str,
        provenance: Provenance,
        account_ccy: str,
        summary: dict[str, Any],
    ) -> tuple[JournalTrade, ...]:
        out: list[JournalTrade] = []
        for index, row in enumerate(_read_csv(directory / "trades.csv")):
            where = f"trades.csv row {index + 1}"
            if row.get("provenance"):
                row_provenance = _provenance(row["provenance"], where)
                if row_provenance is not provenance:
                    raise JournalFormatError(
                        f"{where} is labelled {row_provenance.value} inside a "
                        f"{provenance.value} session"
                    )
            row_ccy = str(row.get("account_ccy") or account_ccy).upper()
            if row_ccy != account_ccy:
                raise JournalFormatError(
                    f"{where} is in {row_ccy} inside a {account_ccy} session"
                )
            breakdown = {
                name: value
                for name in _COST_FIELDS
                if (value := _float(row.get(name))) is not None
            }
            costs = _float(row.get("costs"))
            if costs is None:
                costs = sum(breakdown.values())
            try:
                out.append(
                    JournalTrade(
                        trade_id=str(row.get("trade_id") or f"{session_id}:{index:05d}"),
                        strategy_id=str(
                            row.get("strategy_id") or summary.get("strategy_id") or ""
                        ),
                        instrument=str(row.get("instrument") or summary.get("instrument")),
                        direction=_direction(row.get("direction")),
                        size=_required_float(row, "size"),
                        entry_price=_required_float(row, "entry_price"),
                        exit_price=_required_float(row, "exit_price"),
                        entry_time=_timestamp(row.get("entry_time")),
                        exit_time=_timestamp(row.get("exit_time")),
                        exit_reason=_exit_reason(row.get("exit_reason")),
                        net_pnl=_required_float(row, "net_pnl"),
                        gross_pnl=_required_float(row, "gross_pnl"),
                        costs=float(costs),
                        r_multiple=_float(row.get("r_multiple")),
                        provenance=provenance,
                        account_ccy=account_ccy,
                        session_id=session_id,
                        cost_breakdown=breakdown,
                    )
                )
            except JournalFormatError as exc:
                raise JournalFormatError(f"{where}: {exc}") from exc
        return tuple(out)

    def _positions(
        self,
        directory: Path,
        session_id: str,
        provenance: Provenance,
        account_ccy: str,
        summary: dict[str, Any],
        balance: float | None,
        equity: float | None,
        as_of: datetime | None,
    ) -> tuple[JournalPosition, ...]:
        rows = _read_csv(directory / "positions.csv")
        # Equity minus balance is the venue's own mark-to-market of the open
        # book. It can be attributed to a position only when there is one.
        single_unrealised = (
            round(equity - balance, 2)
            if len(rows) == 1 and equity is not None and balance is not None
            else None
        )
        out: list[JournalPosition] = []
        for index, row in enumerate(rows):
            where = f"positions.csv row {index + 1}"
            record: dict[str, Any] = dict(row)
            if row.get("position"):
                nested = json.loads(row["position"])
                if not isinstance(nested, dict):
                    raise JournalFormatError(f"{where}: position is not an object")
                record = {**nested, "take_profit": row.get("take_profit") or None}
            take_profit = _float(record.get("take_profit"))
            if take_profit is None and record.get("take_profit_targets") is not None:
                take_profit = _first_target(record.get("take_profit_targets"))
            try:
                out.append(
                    JournalPosition(
                        position_id=str(
                            record.get("position_id") or f"{session_id}:pos{index:03d}"
                        ),
                        strategy_id=str(
                            record.get("strategy_id") or summary.get("strategy_id") or ""
                        ),
                        instrument=str(record.get("instrument") or summary.get("instrument")),
                        direction=_direction(record.get("direction")),
                        size=_required_float(record, "size"),
                        entry_price=_required_float(record, "entry_price"),
                        mark_price=_float(record.get("mark_price")),
                        entry_time=_timestamp(record.get("entry_time")),
                        stop_loss=_float(record.get("stop_loss")),
                        take_profit=take_profit,
                        unrealised_pnl=_float(record.get("unrealised_pnl"))
                        if record.get("unrealised_pnl") not in (None, "")
                        else single_unrealised,
                        provenance=provenance,
                        account_ccy=account_ccy,
                        session_id=session_id,
                        as_of=as_of,
                    )
                )
            except JournalFormatError as exc:
                raise JournalFormatError(f"{where}: {exc}") from exc
        return tuple(out)


def _with_as_of(trade: JournalTrade, as_of: datetime) -> JournalTrade:
    return replace(trade, as_of=as_of)
