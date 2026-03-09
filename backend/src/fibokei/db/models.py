"""SQLAlchemy 2.0 ORM models for Fiboki persistence."""

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
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


class PaperBotModel(Base):
    """Persistent state for a paper trading bot."""

    __tablename__ = "paper_bots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bot_id: Mapped[str] = mapped_column(String(20), nullable=False, unique=True, index=True)
    strategy_id: Mapped[str] = mapped_column(String(50), nullable=False)
    instrument: Mapped[str] = mapped_column(String(20), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False)
    risk_pct: Mapped[float] = mapped_column(Float, default=1.0)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="idle")
    bars_seen: Mapped[int] = mapped_column(Integer, default=0)
    last_evaluated_bar: Mapped[datetime | None] = mapped_column(DateTime)
    position_json: Mapped[dict | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    paper_bot: Mapped["PaperBotModel"] = relationship(back_populates="trades")


class PaperAccountModel(Base):
    """Snapshot of the paper trading account state."""

    __tablename__ = "paper_account"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    initial_balance: Mapped[float] = mapped_column(Float, nullable=False, default=10000.0)
    balance: Mapped[float] = mapped_column(Float, nullable=False, default=10000.0)
    equity: Mapped[float] = mapped_column(Float, nullable=False, default=10000.0)
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


class KillSwitchModel(Base):
    """Kill switch state — single row table."""

    __tablename__ = "kill_switch"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    is_active: Mapped[bool] = mapped_column(default=False)
    reason: Mapped[str | None] = mapped_column(Text)
    activated_by: Mapped[str | None] = mapped_column(String(50))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime)
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime)
