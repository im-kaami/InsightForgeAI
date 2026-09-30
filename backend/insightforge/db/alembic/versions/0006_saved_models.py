import sqlalchemy as sa
from alembic import op

revision = "0006_saved_models"
down_revision = "0005_recipes_and_rules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "saved_models",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("owner_id", sa.String(length=32), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("dataset_id", sa.String(length=32), sa.ForeignKey("datasets.id"), nullable=False),
        sa.Column("dataset_version_id", sa.String(length=32), nullable=False),
        sa.Column("run_id", sa.String(length=32), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("artifact_position", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("task", sa.String(length=20), nullable=False),
        sa.Column("target", sa.String(length=200), nullable=False),
        sa.Column("features_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("date_column", sa.String(length=200), nullable=True),
        sa.Column("source_sql", sa.Text(), nullable=False),
        sa.Column("metrics_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("profile_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("model_type", sa.String(length=100), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    with op.batch_alter_table("saved_models") as batch_op:
        batch_op.create_index("ix_saved_models_owner_id", ["owner_id"])
        batch_op.create_index("ix_saved_models_dataset_id", ["dataset_id"])


def downgrade() -> None:
    with op.batch_alter_table("saved_models") as batch_op:
        batch_op.drop_index("ix_saved_models_dataset_id")
        batch_op.drop_index("ix_saved_models_owner_id")
    op.drop_table("saved_models")
