import sqlalchemy as sa
from alembic import op

revision = "0013_dashboards"
down_revision = "0012_api_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dashboards",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("owner_id", sa.String(length=32), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "dashboard_items",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("dashboard_id", sa.String(length=32), sa.ForeignKey("dashboards.id"), nullable=False),
        sa.Column("owner_id", sa.String(length=32), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("dataset_id", sa.String(length=32), nullable=True),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("config_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("snapshot_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("source_run_id", sa.String(length=32), nullable=True),
        sa.Column("version_id", sa.String(length=32), nullable=True),
        sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    with op.batch_alter_table("dashboards") as batch_op:
        batch_op.create_index("ix_dashboards_owner_id", ["owner_id"])
    with op.batch_alter_table("dashboard_items") as batch_op:
        batch_op.create_index("ix_dashboard_items_dashboard_id", ["dashboard_id"])
        batch_op.create_index("ix_dashboard_items_owner_id", ["owner_id"])
        batch_op.create_index("ix_dashboard_items_dataset_id", ["dataset_id"])


def downgrade() -> None:
    with op.batch_alter_table("dashboard_items") as batch_op:
        batch_op.drop_index("ix_dashboard_items_dataset_id")
        batch_op.drop_index("ix_dashboard_items_owner_id")
        batch_op.drop_index("ix_dashboard_items_dashboard_id")
    with op.batch_alter_table("dashboards") as batch_op:
        batch_op.drop_index("ix_dashboards_owner_id")
    op.drop_table("dashboard_items")
    op.drop_table("dashboards")
