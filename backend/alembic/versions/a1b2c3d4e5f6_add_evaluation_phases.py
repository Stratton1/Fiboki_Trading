"""add evaluation_phases table and phase_id columns

Revision ID: a1b2c3d4e5f6
Revises: 393a6aa3262b
Create Date: 2026-05-08 00:00:00.000000

Adds:
  - evaluation_phases table (phase archive/tracking)
  - paper_bots.phase_id FK (nullable, backward-compat)
  - paper_bots.archived_at (nullable)
  - paper_trades.phase_id FK (nullable, backward-compat)
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "393a6aa3262b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "evaluation_phases",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("phase_label", sa.String(length=20), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("archived_at", sa.DateTime(), nullable=True),
        sa.Column("initial_balance", sa.Float(), nullable=False, server_default="1000.0"),
        sa.Column("final_balance", sa.Float(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="GBP"),
        sa.Column("normalized_baseline", sa.Float(), nullable=False, server_default="1000.0"),
        sa.Column("broker_balance_at_start", sa.Float(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("total_trades", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("net_pnl", sa.Float(), nullable=True, server_default="0.0"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    # Add phase_id + archived_at to paper_bots
    with op.batch_alter_table("paper_bots") as batch_op:
        batch_op.add_column(
            sa.Column("phase_id", sa.Integer(), sa.ForeignKey("evaluation_phases.id"), nullable=True)
        )
        batch_op.add_column(
            sa.Column("archived_at", sa.DateTime(), nullable=True)
        )
        batch_op.create_index("ix_paper_bots_phase_id", ["phase_id"])

    # Add phase_id to paper_trades
    with op.batch_alter_table("paper_trades") as batch_op:
        batch_op.add_column(
            sa.Column("phase_id", sa.Integer(), sa.ForeignKey("evaluation_phases.id"), nullable=True)
        )
        batch_op.create_index("ix_paper_trades_phase_id", ["phase_id"])


def downgrade() -> None:
    with op.batch_alter_table("paper_trades") as batch_op:
        batch_op.drop_index("ix_paper_trades_phase_id")
        batch_op.drop_column("phase_id")

    with op.batch_alter_table("paper_bots") as batch_op:
        batch_op.drop_index("ix_paper_bots_phase_id")
        batch_op.drop_column("archived_at")
        batch_op.drop_column("phase_id")

    op.drop_table("evaluation_phases")
