"""Persist non-replayable Kling submission attempts and reconciliation state."""

import sqlalchemy as sa
from alembic import op

revision = "c2a6f8d1e4b0"
down_revision = "f6a12c9e408b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("generation_attempt")
    }
    additions = [
        ("submission_key", sa.String(200)),
        ("provider_account_id", sa.String(160)),
        ("submission_attempt_count", sa.Integer(), sa.text("0")),
        ("submission_started_at", sa.DateTime(timezone=True)),
        ("submission_finished_at", sa.DateTime(timezone=True)),
        ("reconciliation_attempt_count", sa.Integer(), sa.text("0")),
        ("next_reconciliation_at", sa.DateTime(timezone=True)),
        ("last_reconciliation_at", sa.DateTime(timezone=True)),
        ("last_reconciliation_error", sa.String(500)),
    ]
    for item in additions:
        name, column, *rest = item
        if name not in columns:
            kwargs = {"nullable": True}
            if rest:
                kwargs["server_default"] = rest[0]
            op.add_column("generation_attempt", sa.Column(name, column, **kwargs))

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "generation_submission_attempt" not in inspector.get_table_names():
        op.create_table(
            "generation_submission_attempt",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("attempt_id", sa.Uuid(), nullable=False),
            sa.Column("sequence", sa.Integer(), nullable=False),
            sa.Column("provider", sa.String(80), nullable=False),
            sa.Column("idempotency_key", sa.String(200), nullable=False),
            sa.Column("input_fingerprint", sa.String(64)),
            sa.Column("status", sa.String(32), nullable=False, server_default="STARTED"),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("finished_at", sa.DateTime(timezone=True)),
            sa.Column("provider_task_id", sa.String(160)),
            sa.Column("error_code", sa.String(80)),
            sa.Column("error_message", sa.Text()),
            sa.Column("details_json", sa.JSON()),
            sa.ForeignKeyConstraint(["attempt_id"], ["generation_attempt.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("attempt_id", "sequence", name="uq_submission_attempt_sequence"),
            sa.UniqueConstraint("idempotency_key", name="uq_submission_attempt_idempotency_key"),
        )
        op.create_index(
            "ix_generation_submission_attempt_attempt_id",
            "generation_submission_attempt",
            ["attempt_id"],
        )
        op.create_index(
            "ix_generation_submission_attempt_provider_task_id",
            "generation_submission_attempt",
            ["provider_task_id"],
        )
        op.create_index(
            "ix_submission_attempt_status",
            "generation_submission_attempt",
            ["status", "started_at"],
        )

    active = sa.text(
        "status IN ('PREPARED','DISPATCH_PENDING','LAUNCHING','RUNNING',"
        "'RECONCILING','UNKNOWN_AFTER_SUBMIT','SUCCEEDED')"
    )
    indexes = {item["name"] for item in sa.inspect(bind).get_indexes("generation_attempt")}
    if "uq_attempt_active_intent" in indexes:
        op.drop_index("uq_attempt_active_intent", table_name="generation_attempt")
    op.create_index(
        "uq_attempt_active_intent",
        "generation_attempt",
        ["intent_hash"],
        unique=True,
        sqlite_where=active,
        postgresql_where=active,
    )
    if "uq_attempt_active_variant" not in indexes:
        op.create_index(
            "uq_attempt_active_variant",
            "generation_attempt",
            ["variant_id"],
            unique=True,
            sqlite_where=active,
            postgresql_where=active,
        )
    for name in ("submission_key", "provider_account_id"):
        index = f"ix_generation_attempt_{name}"
        if index not in indexes:
            op.create_index(index, "generation_attempt", [name])


def downgrade() -> None:
    if (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM generation_attempt WHERE status='RECONCILING'"))
        .scalar()
    ):
        raise RuntimeError(
            "Resolve RECONCILING attempts before downgrading; never discard receipts"
        )
    op.drop_index("uq_attempt_active_variant", table_name="generation_attempt")
    op.drop_index("uq_attempt_active_intent", table_name="generation_attempt")
    active = sa.text(
        "status IN ('PREPARED','DISPATCH_PENDING','LAUNCHING','RUNNING',"
        "'UNKNOWN_AFTER_SUBMIT','SUCCEEDED')"
    )
    op.create_index(
        "uq_attempt_active_intent",
        "generation_attempt",
        ["intent_hash"],
        unique=True,
        sqlite_where=active,
        postgresql_where=active,
    )
    op.drop_table("generation_submission_attempt")
    for name in ("submission_key", "provider_account_id"):
        op.drop_index(f"ix_generation_attempt_{name}", table_name="generation_attempt")
    with op.batch_alter_table("generation_attempt") as batch:
        for name in (
            "last_reconciliation_error",
            "last_reconciliation_at",
            "next_reconciliation_at",
            "reconciliation_attempt_count",
            "submission_finished_at",
            "submission_started_at",
            "submission_attempt_count",
            "submission_key",
            "provider_account_id",
        ):
            batch.drop_column(name)
