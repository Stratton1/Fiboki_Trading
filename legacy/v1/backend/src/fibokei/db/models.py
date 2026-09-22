"""SQLAlchemy 2.0 ORM models for Fiboki persistence."""

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class DatasetModel(Base):
    __tablename__ = "datasets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instrument: Mapped[str] = mapped_column(String(20), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False)
    source_id: Mapped[str | None] = mapped_column(String(100))
    bar_count: Mapped[int] = mapped_column(Integer, default=0)
    start_date: Mapped[datetime | None] = mapped_column(DateTime)
    end_date: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20), default="active")
    file_path: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )


class BacktestRunModel(Base):
    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    instrument: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False)
    config_json: Mapped[dict | None] = mapped_column(JSON)
    start_date: Mapped[datetime | None] = mapped_column(DateTime)
    end_date: Mapped[datetime | None] = mapped_column(DateTime)
    total_trades: Mapped[int] = mapped_column(Integer, default=0)
    net_profit: Mapped[float] = mapped_column(Float, default=0.0)
    sharpe_ratio: Mapped[float | None] = mapped_column(Float)
    max_drawdown_pct: Mapped[float | None] = mapped_column(Float)
    metrics_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    trades: Mapped[list["TradeModel"]] = relationship(
        back_populates="backtest_run", cascade="all, delete-orphan"
    )


class TradeModel(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    backtest_run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("backtest_runs.id"), nullable=False, index=True
    )
    strategy_id: Mapped[str] = mapped_column(String(50), nullable=False)
    instrument: Mapped[str] = mapped_column(String(20), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    entry_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    exit_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    exit_price: Mapped[float] = mapped_column(Float, nullable=False)
    exit_reason: Mapped[str] = mapped_column(String(50), nullable=False)
    pnl: Mapped[float] = mapped_column(Float, nullable=False)
    bars_in_trade: Mapped[int] = mapped_column(Integer, default=0)

    backtest_run: Mapped["BacktestRunModel"] = relationship(back_populates="trades")


class ResearchResultModel(Base):
    __tablename__ = "research_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    strategy_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    instrument: Mapped[str] = mapped_column(String(20), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False)
    composite_score: Mapped[float] = mapped_column(Float, default=0.0)
    rank: Mapped[int] = mapped_column(Integer, default=0)
    metrics_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )


class StrategyConfigModel(Base):
    __tablename__ = "strategy_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    config_json: Mapped[dict | None] = mapped_column(JSON)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )


class EvaluationPhaseModel(Base):
    """An evaluation phase — archive of a period of paper/demo trading.

    Phase A captures the initial test period (existing bots/trades).
    Phase B and beyond are clean forward-tracking evaluations.
    Each phase carries its own initial_balance baseline (normalised to £1,000
    for evaluation purposes, regardless of the IG broker account size).
    """

    __tablename__ = "evaluation_phases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    phase_label: Mapped[str] = mapped_column(String(20), nullable=False)  # "phase_a", "phase_b", …
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime)
    initial_balance: Mapped[float] = mapped_column(Float, nullable=False, default=1000.0)
    final_balance: Mapped[float | None] = mapped_column(Float)   # set when archived
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="GBP")
    # Normalised baseline for IG demo tracking (£1,000 virtual baseline)
    normalized_baseline: Mapped[float] = mapped_column(Float, nullable=False, default=1000.0)
    # IG broker actual balance at phase start (for reference / scaling)
    broker_balance_at_start: Mapped[float | None] = mapped_column(Float)
    description: Mapped[str | None] = mapped_column(Text)
    total_trades: Mapped[int] = mapped_column(Integer, default=0)
    net_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    bots: Mapped[list["PaperBotModel"]] = relationship(back_populates="phase")
    trades: Mapped[list["PaperTradeModel"]] = relationship(back_populates="phase")


class PaperBotModel(Base):
    """Persistent state for a paper trading bot."""

    __tablename__ = "paper_bots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bot_id: Mapped[str] = mapped_column(String(20), nullable=False, unique=True, index=True)
    strategy_id: Mapped[str] = mapped_column(String(50), nullable=False)
    instrument: Mapped[str] = mapped_column(String(20), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False)
    risk_pct: Mapped[float] = mapped_column(Float, default=1.0)
    source_type: Mapped[str | None] = mapped_column(String(20))  # "research" | "backtest" | "manual"
    source_id: Mapped[str | None] = mapped_column(String(100))  # research run_id or backtest run id
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="idle")
    bars_seen: Mapped[int] = mapped_column(Integer, default=0)
    last_evaluated_bar: Mapped[datetime | None] = mapped_column(DateTime)
    position_json: Mapped[dict | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)
    # Phase tracking — nullable for backward compat with pre-phase bots
    phase_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("evaluation_phases.id"), nullable=True, index=True
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    trades: Mapped[list["PaperTradeModel"]] = relationship(
        back_populates="paper_bot", cascade="all, delete-orphan"
    )
    phase: Mapped["EvaluationPhaseModel | None"] = relationship(
        back_populates="bots", foreign_keys=[phase_id]
    )


class PaperTradeModel(Base):
    """Persisted paper trading trade."""

    __tablename__ = "paper_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    paper_bot_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("paper_bots.id"), nullable=False, index=True
    )
    bot_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    strategy_id: Mapped[str] = mapped_column(String(50), nullable=False)
    instrument: Mapped[str] = mapped_column(String(20), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    entry_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    exit_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    exit_price: Mapped[float] = mapped_column(Float, nullable=False)
    exit_reason: Mapped[str] = mapped_column(String(50), nullable=False)
    pnl: Mapped[float] = mapped_column(Float, nullable=False)
    bars_in_trade: Mapped[int] = mapped_column(Integer, default=0)
    # True for trades generated during live forward monitoring (entry_time >= bot.created_at).
    # False for trades replayed from historical data before the bot was deployed.
    # Nullable for backward compat — existing rows are NULL and treated as live by default
    # unless filtered explicitly.
    is_live: Mapped[bool | None] = mapped_column(Boolean, nullable=True, index=True)
    # Phase tracking — nullable for backward compat
    phase_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("evaluation_phases.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    paper_bot: Mapped["PaperBotModel"] = relationship(back_populates="trades")
    phase: Mapped["EvaluationPhaseModel | None"] = relationship(
        back_populates="trades", foreign_keys=[phase_id]
    )


class PaperAccountModel(Base):
    """Snapshot of the paper trading account state."""

    __tablename__ = "paper_account"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    initial_balance: Mapped[float] = mapped_column(Float, nullable=False, default=1000.0)
    balance: Mapped[float] = mapped_column(Float, nullable=False, default=1000.0)
    equity: Mapped[float] = mapped_column(Float, nullable=False, default=1000.0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="GBP")
    daily_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    weekly_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class ExecutionAuditModel(Base):
    """Audit log for all execution actions (paper and demo)."""

    __tablename__ = "execution_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )
    execution_mode: Mapped[str] = mapped_column(String(20), nullable=False)  # "paper" | "ig_demo"
    action: Mapped[str] = mapped_column(String(50), nullable=False)  # "place_order", "close_position", etc.
    instrument: Mapped[str] = mapped_column(String(20), nullable=False)
    direction: Mapped[str | None] = mapped_column(String(10))
    size: Mapped[float | None] = mapped_column(Float)
    deal_id: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # "success", "failed", "rejected"
    detail_json: Mapped[dict | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)
    bot_id: Mapped[str | None] = mapped_column(String(20), index=True)
    # Slippage analytics fields (Phase 16.4)
    requested_price: Mapped[float | None] = mapped_column(Float)
    filled_price: Mapped[float | None] = mapped_column(Float)
    slippage_pips: Mapped[float | None] = mapped_column(Float)
    fill_latency_ms: Mapped[int | None] = mapped_column(Integer)


class ChartDrawingModel(Base):
    """Persisted chart drawing (trend lines, fibs, etc.)."""

    __tablename__ = "chart_drawings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    instrument: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False)
    tool_type: Mapped[str] = mapped_column(String(30), nullable=False)
    points_json: Mapped[list] = mapped_column(JSON, nullable=False)
    styles_json: Mapped[dict | None] = mapped_column(JSON)
    lock: Mapped[bool] = mapped_column(Boolean, default=False)
    visible: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class DrawingTemplateModel(Base):
    """Reusable drawing template (named set of drawings without instrument binding)."""

    __tablename__ = "drawing_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    drawings_json: Mapped[list] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class ResearchPresetModel(Base):
    """Saved research configurations (strategy × instrument × timeframe selections)."""

    __tablename__ = "research_presets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    config_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class KillSwitchModel(Base):
    """Kill switch state — single row table."""

    __tablename__ = "kill_switch"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    is_active: Mapped[bool] = mapped_column(default=False)
    reason: Mapped[str | None] = mapped_column(Text)
    activated_by: Mapped[str | None] = mapped_column(String(50))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime)
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime)


class SavedShortlistModel(Base):
    """Durable operator-curated shortlist of promising strategy combos.

    Independent of research_results — stores a snapshot so entries survive
    run deletion and result clearing.
    """

    __tablename__ = "saved_shortlist"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "strategy_id", "instrument", "timeframe",
            name="uq_shortlist_combo",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    strategy_id: Mapped[str] = mapped_column(String(50), nullable=False)
    instrument: Mapped[str] = mapped_column(String(20), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    source_run_id: Mapped[str | None] = mapped_column(String(50))
    metrics_snapshot: Mapped[dict | None] = mapped_column(JSON)
    note: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class BookmarkModel(Base):
    """User bookmark for research results, backtests, or trades."""

    __tablename__ = "bookmarks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(30), nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )


class WatchlistModel(Base):
    """User watchlist — named collection of instruments."""

    __tablename__ = "watchlists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    instrument_ids: Mapped[list] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class TradeJournalModel(Base):
    """User journal entry for a trade — notes and tags."""

    __tablename__ = "trade_journal"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    trade_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("trades.id"), nullable=False, index=True
    )
    note: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class StrategyVariantModel(Base):
    """A named parameter variant of a base strategy."""

    __tablename__ = "strategy_variants"
    __table_args__ = (
        UniqueConstraint("strategy_id", "name", name="uq_variant_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    params: Mapped[dict] = mapped_column(JSON, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    backtest_run_id: Mapped[int | None] = mapped_column(Integer)
    trade_overlap: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )


class ExecutionAccountModel(Base):
    """A configured execution destination (paper / IG demo / Tradovate demo / live).

    Phase 2 of the multi-broker fan-out architecture: replaces the env-driven
    Phase 1 ``ResolvedTarget`` config with a DB-backed source of truth.

    When ``FIBOKEI_EXECUTION_ROUTER_MODE=db_targets`` the router builds its
    target list from rows in this table joined to ``bot_execution_targets``.
    Phase 1 ``env_global_fanout`` and ``legacy_single`` modes remain as
    fallbacks.
    """

    __tablename__ = "execution_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    broker: Mapped[str] = mapped_column(String(20), nullable=False)  # paper|ig|tradovate
    environment: Mapped[str] = mapped_column(String(20), nullable=False)  # paper|demo|live
    broker_account_id: Mapped[str | None] = mapped_column(String(100))
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False, default="GBP")
    starting_balance: Mapped[float] = mapped_column(Float, nullable=False, default=1000.0)
    allocated_capital: Mapped[float] = mapped_column(Float, nullable=False, default=1000.0)
    risk_per_trade_pct: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    max_daily_loss_pct: Mapped[float] = mapped_column(Float, nullable=False, default=4.0)
    max_weekly_loss_pct: Mapped[float] = mapped_column(Float, nullable=False, default=8.0)
    max_open_positions: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Live-trading explicit allow flag — required *in addition* to global gates
    # for any live-environment account to be permitted by the router.
    live_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    config_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    targets: Mapped[list["BotExecutionTargetModel"]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )


class BotExecutionTargetModel(Base):
    """Links a paper bot to one or more execution accounts.

    A bot with no targets defaults to the seeded Paper account in
    ``db_targets`` mode (preserves Phase 1 paper-only behaviour for existing
    bots). Each target may override the account's allocation or risk %.
    """

    __tablename__ = "bot_execution_targets"
    __table_args__ = (
        UniqueConstraint("bot_id", "execution_account_id", name="uq_bot_target"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bot_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    execution_account_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("execution_accounts.id"), nullable=False, index=True
    )
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    allocation_override: Mapped[float | None] = mapped_column(Float)
    risk_per_trade_pct_override: Mapped[float | None] = mapped_column(Float)
    sizing_mode: Mapped[str] = mapped_column(
        String(30), nullable=False, default="static_allocation"
    )
    config_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    account: Mapped["ExecutionAccountModel"] = relationship(back_populates="targets")


class BotSignalModel(Base):
    """Phase 3 parent signal record — one per bot evaluation that produced a trade plan.

    A signal is the broker-neutral decision to trade. Its child
    :class:`ExecutionAttemptModel` rows record the per-target outcome of
    fanning that signal out to one or more execution accounts.

    Backwards-compatibility: the legacy ``execution_audit`` table is still
    populated alongside these tables so the existing ``/execution/audit``
    endpoint continues to work for older clients.
    """

    __tablename__ = "bot_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bot_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    strategy_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    instrument: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)  # LONG|SHORT|CLOSE
    signal_timestamp: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True
    )
    bar_time: Mapped[datetime | None] = mapped_column(DateTime)
    plan_json: Mapped[dict | None] = mapped_column(JSON)
    # Optional kind so close-on-exit signals are distinguishable from opens.
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="open")  # open|close
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    attempts: Mapped[list["ExecutionAttemptModel"]] = relationship(
        back_populates="signal", cascade="all, delete-orphan"
    )


class ExecutionAttemptModel(Base):
    """Phase 3 child attempt record — one per (signal × execution target) pair.

    The router writes one row per enabled target during dispatch. Failures,
    rejections, skips, and successful fills all have a row so the audit log
    is complete and partial successes are obvious.
    """

    __tablename__ = "execution_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bot_signal_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("bot_signals.id"), nullable=False, index=True
    )
    execution_target_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("bot_execution_targets.id"), nullable=True, index=True
    )
    execution_account_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("execution_accounts.id"), nullable=True, index=True
    )
    broker: Mapped[str] = mapped_column(String(20), nullable=False)
    environment: Mapped[str] = mapped_column(String(20), nullable=False)
    broker_account_id: Mapped[str | None] = mapped_column(String(100))
    instrument: Mapped[str] = mapped_column(String(20), nullable=False)
    broker_symbol: Mapped[str | None] = mapped_column(String(100))
    direction: Mapped[str | None] = mapped_column(String(10))
    requested_size: Mapped[float | None] = mapped_column(Float)
    adjusted_size: Mapped[float | None] = mapped_column(Float)
    filled_size: Mapped[float | None] = mapped_column(Float)
    requested_price: Mapped[float | None] = mapped_column(Float)
    filled_price: Mapped[float | None] = mapped_column(Float)
    # Vocabulary: pending|skipped|rejected|submitted|filled|partially_filled|closed|failed
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending", index=True)
    broker_order_id: Mapped[str | None] = mapped_column(String(100))
    broker_deal_id: Mapped[str | None] = mapped_column(String(100))
    broker_fill_id: Mapped[str | None] = mapped_column(String(100))
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(50))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    slippage_pips: Mapped[float | None] = mapped_column(Float)
    detail_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )

    signal: Mapped["BotSignalModel"] = relationship(back_populates="attempts")


class AlertModel(Base):
    """In-app alert for signals, trades, risk events, and summaries."""

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alert_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)  # "signal" | "trade" | "risk" | "summary"
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="info")  # "info" | "warning" | "critical"
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict | None] = mapped_column(JSON)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )


class WorkerHeartbeatModel(Base):
    """Liveness record for trading workers (one row per worker identity).

    The dedicated Railway worker upserts its row every poll loop; the API
    surfaces it via /system/status so operators can see worker health
    without reading Railway logs. A stale ``last_beat_at`` (> ~3 poll
    intervals) means the worker is down or wedged.
    """

    __tablename__ = "worker_heartbeats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    worker_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    hostname: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    started_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    last_beat_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )
    poll_interval_s: Mapped[int] = mapped_column(Integer, default=60)
    bots_active: Mapped[int] = mapped_column(Integer, default=0)
    loops_completed: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)


# ─────────────────────────────────────────────────────────────────────────
# Wave 3 — Append-only agent / bot-lifecycle / strategy-lineage ledger.
#
# These three tables form the immutable audit trail required before any
# autonomous strategy generation. They are WRITE-ONCE: the ledger repository
# (db/ledger_repository.py) exposes only create + read functions — there are
# deliberately NO update/delete paths. Every agent or human action that
# creates, mutates, promotes, demotes or vetoes a bot/strategy must append a
# row here with full provenance so any decision can be reconstructed later.
# ─────────────────────────────────────────────────────────────────────────


class AgentRunModel(Base):
    """One bounded agent execution (a single lane doing a single job)."""

    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    lane: Mapped[str] = mapped_column(String(40), nullable=False, index=True)  # builder/quant_auditor/operator/safety_governor
    agent_type: Mapped[str | None] = mapped_column(String(60))  # e.g. fiboki_strategy_author
    actor: Mapped[str] = mapped_column(String(20), nullable=False, default="agent")  # human/agent/system
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="started")  # started/succeeded/failed/vetoed
    prompt_hash: Mapped[str | None] = mapped_column(String(64))
    code_diff_hash: Mapped[str | None] = mapped_column(String(64))
    dataset_version: Mapped[str | None] = mapped_column(String(64))
    summary: Mapped[str | None] = mapped_column(Text)
    detail_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )


class BotLifecycleEventModel(Base):
    """Append-only record of every bot/strategy lifecycle transition."""

    __tablename__ = "bot_lifecycle_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(20), nullable=False, default="agent")  # human/agent/system
    bot_id: Mapped[str | None] = mapped_column(String(40), index=True)
    strategy_id: Mapped[str | None] = mapped_column(String(60), index=True)
    variant_id: Mapped[str | None] = mapped_column(String(64))
    source_strategy_id: Mapped[str | None] = mapped_column(String(60))
    instrument: Mapped[str | None] = mapped_column(String(20))
    timeframe: Mapped[str | None] = mapped_column(String(10))
    agent_run_id: Mapped[str | None] = mapped_column(String(64), index=True)
    backtest_result_id: Mapped[str | None] = mapped_column(String(64))
    research_run_id: Mapped[str | None] = mapped_column(String(64))
    oos_result_id: Mapped[str | None] = mapped_column(String(64))
    monte_carlo_result_id: Mapped[str | None] = mapped_column(String(64))
    dataset_version: Mapped[str | None] = mapped_column(String(64))
    risk_decision: Mapped[str | None] = mapped_column(String(40))
    approval_status: Mapped[str | None] = mapped_column(String(20))  # pending/approved/rejected/n_a
    reason: Mapped[str | None] = mapped_column(Text)
    stats_json: Mapped[dict | None] = mapped_column(JSON)  # paper/demo snapshots etc.
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )


class StrategyLineageModel(Base):
    """Parent→child provenance for cloned / mutated / generated strategies."""

    __tablename__ = "strategy_lineage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lineage_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    strategy_id: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    parent_strategy_id: Mapped[str | None] = mapped_column(String(60), index=True)
    variant_id: Mapped[str | None] = mapped_column(String(64))
    origin: Mapped[str] = mapped_column(String(20), nullable=False, default="hand_coded")  # hand_coded/cloned/mutated/generated
    actor: Mapped[str] = mapped_column(String(20), nullable=False, default="system")
    agent_run_id: Mapped[str | None] = mapped_column(String(64), index=True)
    code_diff_hash: Mapped[str | None] = mapped_column(String(64))
    params_json: Mapped[dict | None] = mapped_column(JSON)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )


# ─────────────────────────────────────────────────────────────────────────
# Wave 1 — Broker trade ledger.
#
# Closed-trade / transaction records imported FROM the broker (IG) so Fiboki
# can show broker-executed trades with the real broker reference and PnL —
# distinct from internal paper trades. Rule: if IG records a trade, Fiboki
# must show it. Keyed (source, reference) for idempotent re-import.
# ─────────────────────────────────────────────────────────────────────────


class BrokerTradeModel(Base):
    __tablename__ = "broker_trades"
    __table_args__ = (
        UniqueConstraint("source", "reference", name="uq_broker_trade_source_ref"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False, index=True)  # ig_demo/ig_live/paper/backtest
    broker: Mapped[str] = mapped_column(String(20), nullable=False, default="ig")
    environment: Mapped[str] = mapped_column(String(20), nullable=False, default="demo")
    # IG dealReference (e.g. "SBQLDCAC") — what the operator sees in the IG UI.
    reference: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    deal_id: Mapped[str | None] = mapped_column(String(64), index=True)  # IG internal dealId
    broker_transaction_id: Mapped[str | None] = mapped_column(String(64))
    instrument_name: Mapped[str | None] = mapped_column(String(120))  # raw broker name
    instrument: Mapped[str | None] = mapped_column(String(20), index=True)  # mapped Fiboki symbol
    epic: Mapped[str | None] = mapped_column(String(64))
    direction: Mapped[str | None] = mapped_column(String(10))  # BUY/SELL
    size: Mapped[float | None] = mapped_column(Float)
    open_level: Mapped[float | None] = mapped_column(Float)
    close_level: Mapped[float | None] = mapped_column(Float)
    pnl: Mapped[float | None] = mapped_column(Float)  # broker profitAndLoss (parsed)
    currency: Mapped[str | None] = mapped_column(String(8))
    transaction_type: Mapped[str | None] = mapped_column(String(40))
    opened_at: Mapped[datetime | None] = mapped_column(DateTime)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    bot_id: Mapped[str | None] = mapped_column(String(40), index=True)
    strategy_id: Mapped[str | None] = mapped_column(String(60), index=True)
    research_run_id: Mapped[str | None] = mapped_column(String(64))
    reconciled: Mapped[bool] = mapped_column(Boolean, default=False)
    raw_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
