"""add bot_signals and execution_attempts tables (Phase 3 parent-child audit)

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-05-08 13:00:00.000000

Phase 3 of the multi-broker fan-out architecture. First-class parent-child
audit: one ``bot_signals`` row per bot evaluation that produced a plan,
plus one ``execution_attempts`` row per (signal × enabled target) pair.

The legacy ``execution_audit`` table is left in place — the new endpoints
use the new tables but the old endpoint still works for back-compat.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bot_signals",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("bot_id", sa.String(length=20), nullable=False),
        sa.Column("strategy_id", sa.String(length=50), nullable=False),
        sa.Column("instrument", sa.String(length=20), nullable=False),
        sa.Column("timeframe", sa.String(length=10), nullable=False),
        sa.Column("direction", sa.String(length=10), nullable=False),
        sa.Column("signal_timestamp", sa.DateTime(), nullable=False),
        sa.Column("bar_time", sa.DateTime(), nullable=True),
        sa.Column("plan_json", sa.JSON(), nullable=True),
        sa.Column("kind", sa.String(length=20), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bot_signals_bot_id", "bot_signals", ["bot_id"])
    op.create_index("ix_bot_signals_strategy_id", "bot_signals", ["strategy_id"])
    op.create_index("ix_bot_signals_instrument", "bot_signals", ["instrument"])
    op.create_index(
        "ix_bot_signals_signal_timestamp", "bot_signals", ["signal_timestamp"]
    )

    op.create_table(
        "execution_attempts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("bot_signal_id", sa.Integer(), nullable=False),
        sa.Column("execution_target_id", sa.Integer(), nullable=True),
        sa.Column("execution_account_id", sa.Integer(), nullable=True),
        sa.Column("broker", sa.String(length=20), nullable=False),
        sa.Column("environment", sa.String(length=20), nullable=False),
        sa.Column("broker_account_id", sa.String(length=100), nullable=True),
        sa.Column("instrument", sa.String(length=20), nullable=False),
        sa.Column("broker_symbol", sa.String(length=100), nullable=True),
        sa.Column("direction", sa.String(length=10), nullable=True),
        sa.Column("requested_size", sa.Float(), nullable=True),
        sa.Column("adjusted_size", sa.Float(), nullable=True),
        sa.Column("filled_size", sa.Float(), nullable=True),
        sa.Column("requested_price", sa.Float(), nullable=True),
        sa.Column("filled_price", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="pending"),
        sa.Column("broker_order_id", sa.String(length=100), nullable=True),
        sa.Column("broker_deal_id", sa.String(length=100), nullable=True),
        sa.Column("broker_fill_id", sa.String(length=100), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=50), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("slippage_pips", sa.Float(), nullable=True),
        sa.Column("detail_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["bot_signal_id"], ["bot_signals.id"]),
        sa.ForeignKeyConstraint(
            ["execution_target_id"], ["bot_execution_targets.id"]
        ),
        sa.ForeignKeyConstraint(
            ["execution_account_id"], ["execution_accounts.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_execution_attempts_bot_signal_id",
        "execution_attempts",
        ["bot_signal_id"],
    )
    op.create_index(
        "ix_execution_attempts_execution_target_id",
        "execution_attempts",
        ["execution_target_id"],
    )
    op.create_index(
        "ix_execution_attempts_execution_account_id",
        "execution_attempts",
        ["execution_account_id"],
    )
    op.create_index(
        "ix_execution_attempts_status", "execution_attempts", ["status"]
    )
    op.create_index(
        "ix_execution_attempts_created_at", "execution_attempts", ["created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_execution_attempts_created_at", table_name="execution_attempts")
    op.drop_index("ix_execution_attempts_status", table_name="execution_attempts")
    op.drop_index(
        "ix_execution_attempts_execution_account_id", table_name="execution_attempts"
    )
    op.drop_index(
        "ix_execution_attempts_execution_target_id", table_name="execution_attempts"
    )
    op.drop_index(
        "ix_execution_attempts_bot_signal_id", table_name="execution_attempts"
    )
    op.drop_table("execution_attempts")

    op.drop_index("ix_bot_signals_signal_timestamp", table_name="bot_signals")
    op.drop_index("ix_bot_signals_instrument", table_name="bot_signals")
    op.drop_index("ix_bot_signals_strategy_id", table_name="bot_signals")
    op.drop_index("ix_bot_signals_bot_id", table_name="bot_signals")
    op.drop_table("bot_signals")
