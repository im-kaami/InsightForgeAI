import sqlalchemy as sa
from alembic import op

revision = "0007_table_relationships"
down_revision = "0006_saved_models"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("datasets") as batch_op:
        batch_op.add_column(
            sa.Column("relationships_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'"))
        )


def downgrade() -> None:
    with op.batch_alter_table("datasets") as batch_op:
        batch_op.drop_column("relationships_json")
