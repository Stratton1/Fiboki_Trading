"""The workspace layout contract: a typed panel registry and a tiling tree.

A layout is a tree. A leaf is one panel; a split divides its box between two or
more children, side by side (``horizontal``) or stacked (``vertical``), with a
size per child in percent. Every panel has a KIND from :data:`PANEL_KINDS` (the
workstation renders each kind with one component, so an unknown kind is
refused here rather than drawn as an empty box) and an optional colour LINK:
panels in the same link group follow each other's symbol, Interactive Brokers
TWS style. A panel's link ROLE says whether it publishes its selection
(``source``), follows the group (``destination``) or both.

The presets are code, reviewed in commits: "FX monitoring", "Research" and
"Incident". An operator saves a preset under a name of their own and edits
that copy; a preset itself never changes under anyone's feet.

Nothing here computes a market number. A layout is display state.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "LAYOUT_SCHEMA",
    "LINK_GROUPS",
    "MAX_DEPTH",
    "MAX_PANELS",
    "PANEL_KINDS",
    "PANEL_REGISTRY",
    "PRESETS",
    "LayoutDoc",
    "LayoutPreset",
    "PanelConfig",
    "PanelKindSpec",
    "PanelLeaf",
    "SplitNode",
]

LAYOUT_SCHEMA = "fiboki.workspace.layout/1"
MAX_PANELS = 16
MAX_DEPTH = 5
MAX_NOTE = 4000

PanelKind = Literal[
    "chart",
    "watchlist",
    "news",
    "calendar",
    "fleet",
    "risk",
    "exposure",
    "alerts",
    "journal",
    "research_runs",
    "market_pulse",
    "system_board",
    "notes",
]
PANEL_KINDS: tuple[str, ...] = PanelKind.__args__  # type: ignore[attr-defined]

#: Link groups. Deliberately not red or green: those mean P&L on this terminal.
LinkGroup = Literal["none", "amber", "cyan", "violet", "white"]
LINK_GROUPS: tuple[str, ...] = LinkGroup.__args__  # type: ignore[attr-defined]
LinkRole = Literal["source", "destination", "both"]

SettingValue = str | bool | int


class PanelKindSpec(BaseModel):
    """What the registry says about one kind (served by GET /api/workspace/panels)."""

    model_config = ConfigDict(frozen=True)

    kind: PanelKind
    label: str
    code: str
    description: str
    #: The panel shows one instrument at a time (can be a link destination).
    follows_symbol: bool
    #: Selecting a row in the panel names an instrument (can be a link source).
    publishes_symbol: bool
    #: The REST paths the panel reads (provenance for the operator).
    reads: tuple[str, ...]


PANEL_REGISTRY: tuple[PanelKindSpec, ...] = (
    PanelKindSpec(kind="chart", label="Chart", code="GP", follows_symbol=True, publishes_symbol=False,
                  description="Closed-candle price chart for the linked symbol.",
                  reads=("/api/markets/bars/{symbol}",)),
    PanelKindSpec(kind="watchlist", label="Watchlist", code="WL", follows_symbol=False, publishes_symbol=True,
                  description="Recorded quotes for a saved list: sparkline, session and data age.",
                  reads=("/api/markets/watchlists", "/api/markets/quotes")),
    PanelKindSpec(kind="news", label="News", code="NWS", follows_symbol=True, publishes_symbol=True,
                  description="Recorded headlines, filtered by the linked instrument's currencies.",
                  reads=("/api/markets/news",)),
    PanelKindSpec(kind="calendar", label="Calendar", code="ECO", follows_symbol=True, publishes_symbol=True,
                  description="Official economic calendar (newest version), impact filter and countdowns.",
                  reads=("/api/markets/calendar",)),
    PanelKindSpec(kind="fleet", label="Fleet", code="FLT", follows_symbol=False, publishes_symbol=True,
                  description="Every paper bot: state, last decision, position and day P&L.",
                  reads=("/api/trading/fleet",)),
    PanelKindSpec(kind="risk", label="Risk", code="RSK", follows_symbol=False, publishes_symbol=False,
                  description="The limit board: utilisation and headroom, decided by the API.",
                  reads=("/api/trading/limits",)),
    PanelKindSpec(kind="exposure", label="Exposure", code="EXPO", follows_symbol=False, publishes_symbol=False,
                  description="Exposure by instrument, currency and strategy.",
                  reads=("/api/trading/limits",)),
    PanelKindSpec(kind="alerts", label="Alerts", code="NTF", follows_symbol=False, publishes_symbol=True,
                  description="Price and level alert notifications, with the read marker.",
                  reads=("/api/alerts/notifications",)),
    PanelKindSpec(kind="journal", label="Journal", code="JRN", follows_symbol=True, publishes_symbol=True,
                  description="Closed trades, filtered by the linked instrument.",
                  reads=("/api/trading/trades",)),
    PanelKindSpec(kind="research_runs", label="Research runs", code="RUNS", follows_symbol=False,
                  publishes_symbol=False,
                  description="Stored backtest runs, newest first.",
                  reads=("/api/research/runs",)),
    PanelKindSpec(kind="market_pulse", label="Market Pulse", code="PULSE", follows_symbol=False,
                  publishes_symbol=True,
                  description="Movers, currency strength, volatility and sessions from recorded quotes.",
                  reads=("/api/markets/pulse",)),
    PanelKindSpec(kind="system_board", label="System board", code="SYS", follows_symbol=False,
                  publishes_symbol=False,
                  description="Every service, worker and check on one clock.",
                  reads=("/api/system/board",)),
    PanelKindSpec(kind="notes", label="Notes", code="NOTE", follows_symbol=False, publishes_symbol=False,
                  description="Free text kept with the layout (an operator note, never strategy input).",
                  reads=()),
)
assert tuple(s.kind for s in PANEL_REGISTRY) == PANEL_KINDS, "registry and kinds disagree"


class PanelLink(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    group: LinkGroup = "none"
    role: LinkRole = "both"


class PanelConfig(BaseModel):
    """One panel: its kind, link, symbol (when it shows one) and small settings."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,32}$")
    kind: PanelKind
    title: str = Field(default="", max_length=48)
    link: PanelLink = PanelLink()
    #: The instrument a symbol-following panel shows when nothing linked has
    #: retargeted it. Upper case; validated against the registry by the route.
    symbol: str | None = Field(default=None, pattern=r"^[A-Z0-9_]{2,16}$")
    #: Panel-specific display settings (timeframe, impact filter, watchlist
    #: name, note text). Small and flat by construction.
    settings: dict[str, SettingValue] = Field(default_factory=dict, max_length=16)

    @model_validator(mode="after")
    def _settings(self) -> PanelConfig:
        for key, value in self.settings.items():
            if not key.replace("_", "").isalnum() or len(key) > 32:
                raise ValueError(f"setting key {key!r} is not a short identifier")
            if isinstance(value, str) and len(value) > (MAX_NOTE if key == "text" else 120):
                raise ValueError(f"setting {key!r} is too long")
        return self


class PanelLeaf(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["panel"] = "panel"
    panel: str = Field(pattern=r"^[A-Za-z0-9_-]{1,32}$")


class SplitNode(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["split"] = "split"
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,32}$")
    orientation: Literal["horizontal", "vertical"]
    #: Percent of the split's box per child, in child order; sums to 100.
    sizes: list[float] = Field(min_length=2, max_length=MAX_PANELS)
    children: list[Annotated[SplitNode | PanelLeaf, Field(discriminator="type")]] = Field(
        min_length=2, max_length=MAX_PANELS
    )

    @model_validator(mode="after")
    def _sizes(self) -> SplitNode:
        if len(self.sizes) != len(self.children):
            raise ValueError(f"split {self.id}: {len(self.sizes)} sizes for {len(self.children)} children")
        if any(not (0 < s <= 100) for s in self.sizes):
            raise ValueError(f"split {self.id}: every size is in (0, 100]")
        if abs(sum(self.sizes) - 100.0) > 1.0:
            raise ValueError(f"split {self.id}: sizes sum to {sum(self.sizes):.2f}, not 100")
        return self


LayoutNode = Annotated[SplitNode | PanelLeaf, Field(discriminator="type")]


class LayoutDoc(BaseModel):
    """A whole workspace: the tree and the panels its leaves name."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    schema_: Literal["fiboki.workspace.layout/1"] = Field(default=LAYOUT_SCHEMA, alias="schema")
    root: LayoutNode
    panels: list[PanelConfig] = Field(min_length=1, max_length=MAX_PANELS)

    @model_validator(mode="after")
    def _tree(self) -> LayoutDoc:
        ids = [p.id for p in self.panels]
        if len(set(ids)) != len(ids):
            raise ValueError("panel ids must be unique")
        seen: list[str] = []
        split_ids: list[str] = []

        def walk(node: SplitNode | PanelLeaf, depth: int) -> None:
            if depth > MAX_DEPTH:
                raise ValueError(f"the layout is nested deeper than {MAX_DEPTH} splits")
            if isinstance(node, PanelLeaf):
                seen.append(node.panel)
                return
            split_ids.append(node.id)
            for child in node.children:
                walk(child, depth + 1)

        walk(self.root, 0)
        if len(set(split_ids)) != len(split_ids):
            raise ValueError("split ids must be unique")
        if sorted(seen) != sorted(ids):
            missing = sorted(set(ids) - set(seen))
            extra = sorted(set(seen) - set(ids))
            dup = sorted({x for x in seen if seen.count(x) > 1})
            raise ValueError(
                "every panel appears in the tree exactly once"
                + (f"; not placed: {missing}" if missing else "")
                + (f"; unknown: {extra}" if extra else "")
                + (f"; placed twice: {dup}" if dup else "")
            )
        return self


SplitNode.model_rebuild()


class LayoutPreset(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    name: str
    description: str
    layout: LayoutDoc


def _p(pid: str, kind: str, *, group: str = "none", role: str = "both", symbol: str | None = None,
       title: str = "", **settings: SettingValue) -> PanelConfig:
    return PanelConfig(
        id=pid, kind=kind, title=title, symbol=symbol,  # type: ignore[arg-type]
        link=PanelLink(group=group, role=role),  # type: ignore[arg-type]
        settings=dict(settings),
    )


def _leaf(pid: str) -> PanelLeaf:
    return PanelLeaf(panel=pid)


def _split(sid: str, orientation: str, sizes: list[float], *children: SplitNode | PanelLeaf) -> SplitNode:
    return SplitNode(id=sid, orientation=orientation, sizes=sizes, children=list(children))  # type: ignore[arg-type]


PRESETS: tuple[LayoutPreset, ...] = (
    LayoutPreset(
        key="fx-monitoring",
        name="FX monitoring",
        description="Watchlist drives the chart, news and calendar (amber group); pulse and alerts below.",
        layout=LayoutDoc(
            root=_split(
                "s0", "vertical", [62, 38],
                _split("s1", "horizontal", [30, 70], _leaf("wl"), _leaf("chart")),
                _split("s2", "horizontal", [34, 33, 33], _leaf("news"), _leaf("eco"), _leaf("pulse")),
            ),
            panels=[
                _p("wl", "watchlist", group="amber", role="source", watchlist="Majors"),
                _p("chart", "chart", group="amber", role="destination", symbol="EURUSD", timeframe="H1"),
                _p("news", "news", group="amber", role="destination", symbol="EURUSD"),
                _p("eco", "calendar", group="amber", role="destination", symbol="EURUSD", impact="high"),
                _p("pulse", "market_pulse", group="amber", role="source", window="24h"),
            ],
        ),
    ),
    LayoutPreset(
        key="research",
        name="Research",
        description="Stored runs beside a chart of the instrument under study, with a notes pad.",
        layout=LayoutDoc(
            root=_split(
                "s0", "horizontal", [45, 55],
                _leaf("runs"),
                _split("s1", "vertical", [65, 35], _leaf("chart"), _leaf("notes")),
            ),
            panels=[
                _p("runs", "research_runs"),
                _p("chart", "chart", group="cyan", role="destination", symbol="EURUSD", timeframe="H4"),
                _p("notes", "notes", text=""),
            ],
        ),
    ),
    LayoutPreset(
        key="incident",
        name="Incident",
        description="System board, alert notifications, the fleet and the limit board on one screen.",
        layout=LayoutDoc(
            root=_split(
                "s0", "horizontal", [50, 50],
                _leaf("board"),
                _split("s1", "vertical", [34, 33, 33], _leaf("alerts"), _leaf("fleet"), _leaf("risk")),
            ),
            panels=[
                _p("board", "system_board"),
                _p("alerts", "alerts"),
                _p("fleet", "fleet"),
                _p("risk", "risk"),
            ],
        ),
    ),
)
