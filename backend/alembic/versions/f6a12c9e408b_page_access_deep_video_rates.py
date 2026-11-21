"""Page grants and separate depth/recreation USD rates, preserving existing estimates."""

import sqlalchemy as sa
from alembic import op

revision = "f6a12c9e408b"
down_revision = "b41e52d6a710"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Additive nullable columns leave credentials, identity, grants and old amounts intact.
    op.add_column("app_user", sa.Column("page_permissions_json", sa.JSON(), nullable=True))
    op.add_column("app_user", sa.Column("depth_video_price_microusd", sa.BigInteger()))
    op.add_column("app_user", sa.Column("recreate_video_price_microusd", sa.BigInteger()))
    op.add_column("account_usage", sa.Column("pricing_category", sa.String(24), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("account_usage") as batch:
        batch.drop_column("pricing_category")
    with op.batch_alter_table("app_user") as batch:
        batch.drop_column("recreate_video_price_microusd")
        batch.drop_column("depth_video_price_microusd")
        batch.drop_column("page_permissions_json")
