import sqlalchemy as sa
from alembic import op

revision = "0011_metric_follows"
down_revision = "0010_approved_queries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "metric_follows",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("owner_id", sa.String(length=32), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("dataset_id", sa.String(length=32), sa.ForeignKey("datasets.id"), nullable=False),
        sa.Column("metric", sa.String(length=60), nullable=False),
        sa.Column("group_by", sa.String(length=200), nullable=True),
        sa.Column("days", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("threshold_percent", sa.Float(), nullable=False, server_default="10"),
        sa.Column("cron", sa.String(length=100), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="UTC"),
        sa.Column("on_new_data", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_run_id", sa.String(length=32), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "metric_checks",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("owner_id", sa.String(length=32), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("follow_id", sa.String(length=32), sa.ForeignKey("metric_follows.id"), nullable=False),
        sa.Column("run_id", sa.String(length=32), nullable=True),
        sa.Column("trigger", sa.String(length=20), nullable=False),
        sa.Column("version_id", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("current", sa.Float(), nullable=True),
        sa.Column("previous", sa.Float(), nullable=True),
        sa.Column("change_percent", sa.Float(), nullable=True),
        sa.Column("message", sa.Text(), nullable=False, server_default=""),
        sa.Column("alert", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    with op.batch_alter_table("metric_follows") as batch_op:
        batch_op.create_index("ix_metric_follows_owner_id", ["owner_id"])
        batch_op.create_index("ix_metric_follows_dataset_id", ["dataset_id"])
    with op.batch_alter_table("metric_checks") as batch_op:
        batch_op.create_index("ix_metric_checks_owner_id", ["owner_id"])
        batch_op.create_index("ix_metric_checks_follow_id", ["follow_id"])


def downgrade() -> None:
    with op.batch_alter_table("metric_checks") as batch_op:
        batch_op.drop_index("ix_metric_checks_follow_id")
        batch_op.drop_index("ix_metric_checks_owner_id")
    with op.batch_alter_table("metric_follows") as batch_op:
        batch_op.drop_index("ix_metric_follows_dataset_id")
        batch_op.drop_index("ix_metric_follows_owner_id")
    op.drop_table("metric_checks")
    op.drop_table("metric_follows")
