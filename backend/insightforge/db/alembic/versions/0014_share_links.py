import sqlalchemy as sa
from alembic import op

revision = "0014_share_links"
down_revision = "0013_dashboards"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "share_links",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("owner_id", sa.String(length=32), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("target_id", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("prefix", sa.String(length=16), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("view_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_viewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    with op.batch_alter_table("share_links") as batch_op:
        batch_op.create_index("ix_share_links_owner_id", ["owner_id"])
        batch_op.create_index("ix_share_links_target_id", ["target_id"])
        batch_op.create_index("ix_share_links_token_hash", ["token_hash"], unique=True)


def downgrade() -> None:
    with op.batch_alter_table("share_links") as batch_op:
        batch_op.drop_index("ix_share_links_token_hash")
        batch_op.drop_index("ix_share_links_target_id")
        batch_op.drop_index("ix_share_links_owner_id")
    op.drop_table("share_links")
