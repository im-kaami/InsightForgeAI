import sqlalchemy as sa
from alembic import op

revision = "0004_dataset_notes"
down_revision = "0003_verified_reports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("datasets") as batch_op:
        batch_op.add_column(
            sa.Column("notes_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'"))
        )


def downgrade() -> None:
    with op.batch_alter_table("datasets") as batch_op:
        batch_op.drop_column("notes_json")
