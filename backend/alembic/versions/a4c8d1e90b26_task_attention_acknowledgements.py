"""Persist per-user task attention acknowledgement independently of task execution.

Revision ID: a4c8d1e90b26
Revises: e7b9c6a13240
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a4c8d1e90b26"
down_revision: str | Sequence[str] | None = "e7b9c6a13240"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_attention_acknowledgement",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("signature", sa.String(96), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["parent_task.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "task_id", name="pk_task_attention_user_task"),
    )


def downgrade() -> None:
    op.drop_table("task_attention_acknowledgement")
