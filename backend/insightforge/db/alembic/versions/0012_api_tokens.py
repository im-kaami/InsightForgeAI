import sqlalchemy as sa
from alembic import op

revision = "0012_api_tokens"
down_revision = "0011_metric_follows"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_tokens",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("owner_id", sa.String(length=32), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("scope", sa.String(length=20), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("prefix", sa.String(length=16), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    with op.batch_alter_table("api_tokens") as batch_op:
        batch_op.create_index("ix_api_tokens_owner_id", ["owner_id"])
        batch_op.create_index("ix_api_tokens_token_hash", ["token_hash"], unique=True)


def downgrade() -> None:
    with op.batch_alter_table("api_tokens") as batch_op:
        batch_op.drop_index("ix_api_tokens_token_hash")
        batch_op.drop_index("ix_api_tokens_owner_id")
    op.drop_table("api_tokens")
