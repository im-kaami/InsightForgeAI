import sqlalchemy as sa
from alembic import op

revision = "0017_run_queue"
down_revision = "0016_run_costs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("runs") as batch:
        batch.add_column(sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("runs") as batch:
        batch.drop_column("attempts")
