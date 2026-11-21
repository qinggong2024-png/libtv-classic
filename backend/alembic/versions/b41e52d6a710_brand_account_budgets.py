"""Brand account scopes and conservative USD budget evidence."""

import uuid
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from alembic import op

revision = "b41e52d6a710"
down_revision = "c8d4e6f1a203"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "brand",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False, unique=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    with op.batch_alter_table("app_user") as batch:
        batch.add_column(sa.Column("brand_id", sa.Uuid(), nullable=True))
        batch.add_column(
            sa.Column("is_brand_admin", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(sa.Column("daily_budget_microusd", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("image_price_microusd", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("video_price_microusd", sa.BigInteger(), nullable=True))
        batch.create_foreign_key("fk_user_brand", "brand", ["brand_id"], ["id"])
        batch.create_index("ix_app_user_brand_id", ["brand_id"])
    op.create_table(
        "account_usage",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("app_user.id"), nullable=False),
        sa.Column("brand_id", sa.Uuid(), sa.ForeignKey("brand.id"), nullable=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("units", sa.Integer(), nullable=False),
        sa.Column("amount_microusd", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("kind", "operation_id", name="uq_usage_operation"),
    )
    op.create_index("ix_usage_user_day", "account_usage", ["user_id", "business_date"])
    op.create_index("ix_account_usage_operation_id", "account_usage", ["operation_id"])
    _backfill_unpriced_usage()


def _backfill_unpriced_usage() -> None:
    """Keep historical activity visible without inventing historical dollar prices."""
    bind = op.get_bind()
    metadata = sa.MetaData()
    tables = set(sa.inspect(bind).get_table_names())
    usage = sa.Table("account_usage", metadata, autoload_with=bind)
    users = sa.Table("app_user", metadata, autoload_with=bind)
    for table_name, kind in (("points_reservation", "VIDEO"), ("image_generation_batch", "IMAGE")):
        if table_name not in tables:
            continue
        source = sa.Table(table_name, metadata, autoload_with=bind)
        rows = bind.execute(sa.select(source).join(users, users.c.id == source.c.user_id))
        for row in rows.mappings():
            created = row["created_at"]
            if isinstance(created, str):
                created = datetime.fromisoformat(created)
            if created.tzinfo is None:
                created = created.replace(tzinfo=UTC)
            status = str(row["status"])
            state = "RESERVED"
            if status in {"UNCERTAIN", "WRITE_UNCERTAIN", "UNKNOWN"}:
                state = "UNCERTAIN"
            elif status == "RELEASED":
                state = "RELEASED"
            elif status in {"SETTLED", "COMPLETED", "PARTIAL_FAILED", "FAILED", "MANUAL_REVIEW"}:
                state = "ESTIMATED"
            bind.execute(
                usage.insert().values(
                    id=uuid.uuid4() if bind.dialect.name == "postgresql" else uuid.uuid4().hex,
                    user_id=row["user_id"],
                    brand_id=None,
                    kind=kind,
                    operation_id=row["attempt_id"] if kind == "VIDEO" else row["id"],
                    business_date=row["business_date"]
                    if kind == "VIDEO"
                    else created.astimezone(ZoneInfo("Asia/Shanghai")).date(),
                    units=1 if kind == "VIDEO" else row["expected_generation_count"],
                    amount_microusd=None,
                    status=state,
                    created_at=created,
                )
            )


def downgrade() -> None:
    op.drop_table("account_usage")
    with op.batch_alter_table("app_user") as batch:
        batch.drop_index("ix_app_user_brand_id")
        batch.drop_constraint("fk_user_brand", type_="foreignkey")
        for name in (
            "video_price_microusd",
            "image_price_microusd",
            "daily_budget_microusd",
            "is_brand_admin",
            "brand_id",
        ):
            batch.drop_column(name)
    op.drop_table("brand")
