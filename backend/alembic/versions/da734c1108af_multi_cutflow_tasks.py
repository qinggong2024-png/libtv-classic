"""Add independent ordered multi-source CUT tasks."""
from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op

revision: str = "da734c1108af"
down_revision: str | Sequence[str] | None = "c9e3f4a5b6d7"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        "multi_cutflow_task",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("sources_json", sa.JSON(), nullable=False),
        sa.Column("manifest_json", sa.JSON(), nullable=False),
        sa.Column("manifest_path", sa.String(1000)),
        sa.Column("output_json", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(80)),
        sa.Column("error_message", sa.Text()),
        sa.Column("lease_token", sa.String(40)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("next_run_at", sa.DateTime(timezone=True)),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_multi_cutflow_user_key"),
    )
    op.create_index("ix_multi_cutflow_task_user_id", "multi_cutflow_task", ["user_id"])
    op.create_index("ix_multi_cutflow_task_status", "multi_cutflow_task", ["status"])
    op.create_index("ix_multi_cutflow_status_created", "multi_cutflow_task", ["status", "created_at"])

def downgrade() -> None:
    op.drop_index("ix_multi_cutflow_status_created", table_name="multi_cutflow_task")
    op.drop_index("ix_multi_cutflow_task_status", table_name="multi_cutflow_task")
    op.drop_index("ix_multi_cutflow_task_user_id", table_name="multi_cutflow_task")
    op.drop_table("multi_cutflow_task")
