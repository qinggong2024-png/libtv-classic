"""Persist employee-specific CUT output viewing without approval side effects."""

import sqlalchemy as sa
from alembic import op

revision = "e7b9c6a13240"
down_revision = ("c2a6f8d1e4b0", "da734c1108af")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cutflow_output_view",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("output_id", sa.String(200), nullable=False),
        sa.Column("viewed_at", sa.DateTime(timezone=True),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["batch_id"], ["multi_cutflow_task.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "batch_id", "output_id"),
    )
    op.create_index("ix_cutflow_output_view_batch", "cutflow_output_view", ["batch_id"])


def downgrade() -> None:
    op.drop_index("ix_cutflow_output_view_batch", table_name="cutflow_output_view")
    op.drop_table("cutflow_output_view")
