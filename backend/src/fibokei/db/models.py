"""SQLAlchemy 2.0 ORM models for FIBOKEI persistence."""

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
