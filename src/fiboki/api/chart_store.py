"""Operator chart templates and price alerts (Wave 2B), one SQLite file each.

TEMPLATES
---------
A template is a named set of DISPLAY choices: which layers show, which
indicator groups are toggled, which strategies' indicators the overlays route
computes, and which trades are drawn. It holds no price, symbol or parameter:
applying one to another instrument changes only what is drawn there. Stored per
operator under ``<state>/chart/templates.sqlite`` with a version per template
for optimistic concurrency, as the drawing store does.

PRICE ALERTS
------------
An alert is "tell me when SYMBOL trades through PRICE", created from a chart
level or zone edge. The DIRECTION (cross above or below) is decided here from
the latest closed bar the store holds, never by the browser. Stored under
``<state>/alerts/price_alerts.sqlite``, append-only rows with a status.

What neither is: strategy or risk input. Nothing under ``strategy/``,
``backtest/``, ``risk/``, ``portfolio/`` or ``broker/`` reads these stores
(``tests/unit/test_drawings_never_reach_a_decision.py`` covers ``api/``
modules named here too). An alert does not place, size or close anything.

KNOWN LIMIT: no evaluator watches these alerts on this branch. Each one is
stored with status ``armed`` and the API says, on every read, that nothing is
evaluating it yet (Wave 2A owns the alert manager and its evaluator).
"""
from __future__ import annotations

import json
import math
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = [
    "LAYER_NAMES",
    "MAX_TEMPLATES",
    "ChartTemplate",
    "PriceAlert",
    "PriceAlertStore",
    "StoredTemplate",
    "TemplateConflict",
    "TemplateStore",
]

#: Every layer a template can show or hide (the chart's layer toolbar).
LAYER_NAMES: tuple[str, ...] = (
    "drawings", "trades", "stops", "events", "indicators", "signals", "fills", "regimes",
)
MAX_TEMPLATES = 50
TEMPLATE_SCHEMA = "fiboki.chart.template/1"


class ChartTemplate(BaseModel):
    """Display choices only: no symbol, no price, no parameter."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Shown / hidden per layer; a layer not named keeps the chart's default.
    layers: dict[str, bool] = Field(default_factory=dict)
    #: Explicit indicator-group toggles (``<indicator_id>|<pane>`` -> shown).
    groups: dict[str, bool] = Field(default_factory=dict, max_length=200)
    #: Strategies whose indicators the overlays route computes (None: all).
    strategy_ids: list[str] | None = Field(default=None, max_length=20)
    trade_source: Literal["paper", "backtest", "both"] = "both"

    @field_validator("layers")
    @classmethod
    def _known_layers(cls, value: dict[str, bool]) -> dict[str, bool]:
        unknown = sorted(set(value) - set(LAYER_NAMES))
        if unknown:
            raise ValueError(f"unknown layer(s) {unknown}; known: {list(LAYER_NAMES)}")
        return value

    @field_validator("groups")
    @classmethod
    def _group_ids(cls, value: dict[str, bool]) -> dict[str, bool]:
        for key in value:
            if not 1 <= len(key) <= 160 or any(ord(c) < 32 for c in key):
                raise ValueError("an indicator group id is 1 to 160 printable characters")
        return value


@dataclass(frozen=True, slots=True)
class StoredTemplate:
    owner: str
    name: str
    version: int
    updated_at: datetime
    template: ChartTemplate


class TemplateConflict(Exception):
    def __init__(self, current_version: int) -> None:
        super().__init__(f"template is at version {current_version}")
        self.current_version = current_version


_TEMPLATE_DDL = (
    """
    CREATE TABLE IF NOT EXISTS chart_template (
        owner TEXT NOT NULL,
        name TEXT NOT NULL,
        version INTEGER NOT NULL,
        updated_at TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        PRIMARY KEY (owner, name)
    )
    """,
)


@contextmanager
def _open(path: Path, ddl: tuple[str, ...], *, write: bool) -> Iterator[sqlite3.Connection | None]:
    if not write and not path.exists():
        # A read must not create the file: nothing stored yet.
        yield None
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5.0, isolation_level=None)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        for statement in ddl:
            conn.execute(statement)
        yield conn
    finally:
        conn.close()


def _payload(template: ChartTemplate) -> str:
    return json.dumps(
        {"schema": TEMPLATE_SCHEMA, "template": template.model_dump(mode="json")},
        sort_keys=True,
        separators=(",", ":"),
    )


def _parse(payload: str) -> ChartTemplate:
    raw = json.loads(payload)
    if not isinstance(raw, dict) or raw.get("schema") != TEMPLATE_SCHEMA:
        raise ValueError("stored template has an unknown schema")
    return ChartTemplate.model_validate(raw.get("template") or {})


class TemplateStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def list(self, owner: str) -> list[StoredTemplate]:
        with _open(self.path, _TEMPLATE_DDL, write=False) as conn:
            if conn is None:
                return []
            rows = conn.execute(
                "SELECT name, version, updated_at, payload_json FROM chart_template "
                "WHERE owner = ? ORDER BY name",
                (owner,),
            ).fetchall()
        return [
            StoredTemplate(owner, str(n), int(v), datetime.fromisoformat(u), _parse(p))
            for n, v, u, p in rows
        ]

    def put(
        self, owner: str, name: str, template: ChartTemplate, *, base_version: int
    ) -> StoredTemplate:
        now = datetime.now(tz=UTC)
        with _open(self.path, _TEMPLATE_DDL, write=True) as conn:
            assert conn is not None
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT version FROM chart_template WHERE owner = ? AND name = ?",
                    (owner, name),
                ).fetchone()
                current = int(row[0]) if row else 0
                if current != base_version:
                    raise TemplateConflict(current)
                if row is None:
                    count = conn.execute(
                        "SELECT count(*) FROM chart_template WHERE owner = ?", (owner,)
                    ).fetchone()[0]
                    if count >= MAX_TEMPLATES:
                        raise ValueError(f"at most {MAX_TEMPLATES} templates per operator")
                version = current + 1
                conn.execute(
                    "INSERT INTO chart_template (owner, name, version, updated_at, payload_json) "
                    "VALUES (?, ?, ?, ?, ?) ON CONFLICT (owner, name) DO UPDATE SET "
                    "version = excluded.version, updated_at = excluded.updated_at, "
                    "payload_json = excluded.payload_json",
                    (owner, name, version, now.isoformat(), _payload(template)),
                )
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        return StoredTemplate(owner, name, version, now, template)

    def delete(self, owner: str, name: str, *, base_version: int) -> None:
        with _open(self.path, _TEMPLATE_DDL, write=True) as conn:
            assert conn is not None
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT version FROM chart_template WHERE owner = ? AND name = ?",
                    (owner, name),
                ).fetchone()
                current = int(row[0]) if row else 0
                if row is None or current != base_version:
                    raise TemplateConflict(current)
                conn.execute(
                    "DELETE FROM chart_template WHERE owner = ? AND name = ?", (owner, name)
                )
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise


# ============================================================ price alerts

AlertCondition = Literal["cross_above", "cross_below"]


class PriceAlert(BaseModel):
    model_config = ConfigDict(frozen=True)

    alert_id: str
    owner: str
    symbol: str
    price: float
    condition: AlertCondition
    #: The close the direction was decided against, and that bar's time.
    reference_close: float
    reference_time: datetime
    dataset_version_id: str
    #: The drawing this alert came from (a level or a zone edge), if any.
    drawing_id: str | None
    #: ``level``, ``zone_top`` or ``zone_bottom`` when created from a drawing.
    origin: str | None
    note: str
    created_at: datetime
    status: Literal["armed"] = "armed"

    @field_validator("price", "reference_close")
    @classmethod
    def _finite(cls, value: float) -> float:
        if not math.isfinite(value) or value <= 0:
            raise ValueError("a price is finite and positive")
        return float(value)


_ALERT_DDL = (
    """
    CREATE TABLE IF NOT EXISTS price_alert (
        alert_id TEXT PRIMARY KEY,
        owner TEXT NOT NULL,
        symbol TEXT NOT NULL,
        created_at TEXT NOT NULL,
        payload_json TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS price_alert_owner ON price_alert (owner, symbol, created_at)",
    """
    CREATE TRIGGER IF NOT EXISTS price_alert_no_update
    BEFORE UPDATE ON price_alert
    BEGIN SELECT RAISE(ABORT, 'price_alert rows are append-only'); END
    """,
)


class PriceAlertStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    @staticmethod
    def new_id() -> str:
        return f"pa_{uuid.uuid4().hex[:16]}"

    def add(self, alert: PriceAlert) -> PriceAlert:
        with _open(self.path, _ALERT_DDL, write=True) as conn:
            assert conn is not None
            conn.execute(
                "INSERT INTO price_alert (alert_id, owner, symbol, created_at, payload_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    alert.alert_id,
                    alert.owner,
                    alert.symbol,
                    alert.created_at.isoformat(),
                    alert.model_dump_json(),
                ),
            )
        return alert

    def list(self, owner: str, symbol: str | None = None) -> list[PriceAlert]:
        with _open(self.path, _ALERT_DDL, write=False) as conn:
            if conn is None:
                return []
            if symbol is None:
                rows = conn.execute(
                    "SELECT payload_json FROM price_alert WHERE owner = ? "
                    "ORDER BY created_at, alert_id",
                    (owner,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT payload_json FROM price_alert WHERE owner = ? AND symbol = ? "
                    "ORDER BY created_at, alert_id",
                    (owner, symbol),
                ).fetchall()
        return [PriceAlert.model_validate_json(r[0]) for r in rows]
