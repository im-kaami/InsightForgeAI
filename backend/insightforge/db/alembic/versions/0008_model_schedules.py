import sqlalchemy as sa
from alembic import op

revision = "0008_model_schedules"
down_revision = "0007_table_relationships"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_schedules",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("owner_id", sa.String(length=32), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "model_id", sa.String(length=32), sa.ForeignKey("saved_models.id"), nullable=False, unique=True
        ),
        sa.Column("cron", sa.String(length=100), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="UTC"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "model_scorings",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("owner_id", sa.String(length=32), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("model_id", sa.String(length=32), sa.ForeignKey("saved_models.id"), nullable=False),
        sa.Column("trigger", sa.String(length=20), nullable=False),
        sa.Column("version_id", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("rows_scored", sa.Integer(), nullable=True),
        sa.Column("max_psi", sa.Float(), nullable=True),
        sa.Column("verdict", sa.String(length=50), nullable=True),
        sa.Column("reasons_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("alert", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    with op.batch_alter_table("model_schedules") as batch_op:
        batch_op.create_index("ix_model_schedules_owner_id", ["owner_id"])
    with op.batch_alter_table("model_scorings") as batch_op:
        batch_op.create_index("ix_model_scorings_owner_id", ["owner_id"])
        batch_op.create_index("ix_model_scorings_model_id", ["model_id"])


def downgrade() -> None:
    with op.batch_alter_table("model_scorings") as batch_op:
        batch_op.drop_index("ix_model_scorings_model_id")
        batch_op.drop_index("ix_model_scorings_owner_id")
    with op.batch_alter_table("model_schedules") as batch_op:
        batch_op.drop_index("ix_model_schedules_owner_id")
    op.drop_table("model_scorings")
    op.drop_table("model_schedules")
