import sqlalchemy as sa
from alembic import op

revision = "0022_email_verified"
down_revision = "0021_sso_accounts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing accounts count as verified; only accounts registered while email is set up are checked.
    with op.batch_alter_table("users") as batch:
        batch.add_column(
            sa.Column("email_verified", sa.Boolean(), nullable=False, server_default=sa.true())
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("email_verified")
