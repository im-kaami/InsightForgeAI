import sqlalchemy as sa
from alembic import op

revision = "0016_run_costs"
down_revision = "0015_workspaces"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("runs") as batch:
        batch.add_column(sa.Column("llm_provider", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("llm_model", sa.String(length=200), nullable=True))
        batch.add_column(sa.Column("cost_usd", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("runs") as batch:
        batch.drop_column("cost_usd")
        batch.drop_column("llm_model")
        batch.drop_column("llm_provider")
