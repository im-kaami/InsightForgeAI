import sqlalchemy as sa
from alembic import op

revision = "0003_verified_reports"
down_revision = "0002_schedule_timezone"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("datasets") as batch_op:
        batch_op.add_column(sa.Column("current_version_id", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("profile_json", sa.JSON(), nullable=True))
        batch_op.add_column(
            sa.Column("llm_policy", sa.String(length=20), nullable=False, server_default="local")
        )
    with op.batch_alter_table("runs") as batch_op:
        batch_op.add_column(sa.Column("dataset_version_id", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("definition_id", sa.String(length=32), nullable=True))
        batch_op.add_column(
            sa.Column("request_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'"))
        )
        batch_op.add_column(
            sa.Column("provenance_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'"))
        )
        batch_op.add_column(
            sa.Column(
                "verification_status",
                sa.String(length=32),
                nullable=False,
                server_default="exploratory",
            )
        )
        batch_op.add_column(
            sa.Column("warnings_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'"))
        )
        batch_op.add_column(sa.Column("fallback_reason", sa.Text(), nullable=True))
    op.create_table(
        "dataset_versions",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("dataset_id", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=32), nullable=False),
        sa.Column("base_version_id", sa.String(length=32), nullable=True),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("sources_json", sa.JSON(), nullable=False),
        sa.Column("schema_json", sa.JSON(), nullable=False),
        sa.Column("profile_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"]),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dataset_versions_dataset_id", "dataset_versions", ["dataset_id"])
    op.create_index("ix_dataset_versions_owner_id", "dataset_versions", ["owner_id"])
    op.create_table(
        "report_definitions",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("dataset_id", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=32), nullable=False),
        sa.Column("session_id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("previous_id", sa.String(length=32), nullable=True),
        sa.Column("definition_json", sa.JSON(), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"]),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_report_definitions_dataset_id", "report_definitions", ["dataset_id"])
    op.create_index("ix_report_definitions_owner_id", "report_definitions", ["owner_id"])
    op.create_index("ix_report_definitions_session_id", "report_definitions", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_report_definitions_session_id", table_name="report_definitions")
    op.drop_index("ix_report_definitions_owner_id", table_name="report_definitions")
    op.drop_index("ix_report_definitions_dataset_id", table_name="report_definitions")
    op.drop_table("report_definitions")
    op.drop_index("ix_dataset_versions_owner_id", table_name="dataset_versions")
    op.drop_index("ix_dataset_versions_dataset_id", table_name="dataset_versions")
    op.drop_table("dataset_versions")
    with op.batch_alter_table("runs") as batch_op:
        batch_op.drop_column("fallback_reason")
        batch_op.drop_column("warnings_json")
        batch_op.drop_column("verification_status")
        batch_op.drop_column("provenance_json")
        batch_op.drop_column("request_json")
        batch_op.drop_column("definition_id")
        batch_op.drop_column("dataset_version_id")
    with op.batch_alter_table("datasets") as batch_op:
        batch_op.drop_column("llm_policy")
        batch_op.drop_column("profile_json")
        batch_op.drop_column("current_version_id")
