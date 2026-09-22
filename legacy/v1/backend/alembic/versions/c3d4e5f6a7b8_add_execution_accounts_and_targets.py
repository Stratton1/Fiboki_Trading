"""add execution_accounts and bot_execution_targets tables

Revision ID: c3d4e5f6a7b8
Revises: a1b2c3d4e5f6
Create Date: 2026-05-08 12:00:00.000000

Phase 2 of the multi-broker fan-out architecture. Adds:

- ``execution_accounts``  — operator-managed broker destinations
- ``bot_execution_targets`` — per-bot links to execution accounts

Seeds a single default Paper execution account so existing bots remain
paper-only when ``FIBOKEI_EXECUTION_ROUTER_MODE=db_targets`` is enabled.
"""

from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "execution_accounts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False, unique=True),
        sa.Column("broker", sa.String(length=20), nullable=False),
        sa.Column("environment", sa.String(length=20), nullable=False),
        sa.Column("broker_account_id", sa.String(length=100), nullable=True),
        sa.Column("base_currency", sa.String(length=3), nullable=False, server_default="GBP"),
        sa.Column("starting_balance", sa.Float(), nullable=False, server_default="1000.0"),
        sa.Column("allocated_capital", sa.Float(), nullable=False, server_default="1000.0"),
        sa.Column("risk_per_trade_pct", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("max_daily_loss_pct", sa.Float(), nullable=False, server_default="4.0"),
        sa.Column("max_weekly_loss_pct", sa.Float(), nullable=False, server_default="8.0"),
        sa.Column("max_open_positions", sa.Integer(), nullable=False, server_default="20"),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("live_allowed", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("config_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "bot_execution_targets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("bot_id", sa.String(length=20), nullable=False),
        sa.Column("execution_account_id", sa.Integer(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("allocation_override", sa.Float(), nullable=True),
        sa.Column("risk_per_trade_pct_override", sa.Float(), nullable=True),
        sa.Column(
            "sizing_mode",
            sa.String(length=30),
            nullable=False,
            server_default="static_allocation",
        ),
        sa.Column("config_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["execution_account_id"], ["execution_accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("bot_id", "execution_account_id", name="uq_bot_target"),
    )
    op.create_index(
        "ix_bot_execution_targets_bot_id", "bot_execution_targets", ["bot_id"]
    )
    op.create_index(
        "ix_bot_execution_targets_execution_account_id",
        "bot_execution_targets",
        ["execution_account_id"],
    )

    # Seed a single default Paper execution account so existing bots have a
    # stable destination in db_targets mode without operator intervention.
    now = datetime.now(timezone.utc)
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "INSERT INTO execution_accounts ("
            "name, broker, environment, base_currency, "
            "starting_balance, allocated_capital, risk_per_trade_pct, "
            "max_daily_loss_pct, max_weekly_loss_pct, max_open_positions, "
            "is_enabled, is_default, live_allowed, created_at, updated_at"
            ") VALUES ("
            ":name, :broker, :env, :ccy, "
            ":sb, :alloc, :risk, :daily, :weekly, :maxpos, "
            ":enabled, :is_default, :live_allowed, :created, :updated"
            ")"
        ),
        {
            "name": "Paper",
            "broker": "paper",
            "env": "paper",
            "ccy": "GBP",
            "sb": 1000.0,
            "alloc": 1000.0,
            "risk": 1.0,
            "daily": 4.0,
            "weekly": 8.0,
            "maxpos": 20,
            "enabled": True,
            "is_default": True,
            "live_allowed": False,
            "created": now,
            "updated": now,
        },
    )


def downgrade() -> None:
    op.drop_index(
        "ix_bot_execution_targets_execution_account_id",
        table_name="bot_execution_targets",
    )
    op.drop_index(
        "ix_bot_execution_targets_bot_id", table_name="bot_execution_targets"
    )
    op.drop_table("bot_execution_targets")
    op.drop_table("execution_accounts")
