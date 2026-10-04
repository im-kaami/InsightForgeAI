import sqlalchemy as sa
from alembic import op

revision = "0015_workspaces"
down_revision = "0014_share_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspaces",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("created_by", sa.String(length=32), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "workspace_members",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("workspace_id", sa.String(length=32), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("user_id", sa.String(length=32), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("workspace_id", "user_id", name="uq_workspace_member"),
    )
    op.create_table(
        "workspace_invites",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("workspace_id", sa.String(length=32), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("invited_by", sa.String(length=32), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    with op.batch_alter_table("workspace_members") as batch_op:
        batch_op.create_index("ix_workspace_members_workspace_id", ["workspace_id"])
        batch_op.create_index("ix_workspace_members_user_id", ["user_id"])
    with op.batch_alter_table("workspace_invites") as batch_op:
        batch_op.create_index("ix_workspace_invites_workspace_id", ["workspace_id"])
        batch_op.create_index("ix_workspace_invites_token_hash", ["token_hash"], unique=True)
    for table in ("datasets", "dashboards"):
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(sa.Column("workspace_id", sa.String(length=32), nullable=True))
            batch_op.create_index(f"ix_{table}_workspace_id", ["workspace_id"])


def downgrade() -> None:
    for table in ("dashboards", "datasets"):
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_index(f"ix_{table}_workspace_id")
            batch_op.drop_column("workspace_id")
    with op.batch_alter_table("workspace_invites") as batch_op:
        batch_op.drop_index("ix_workspace_invites_token_hash")
        batch_op.drop_index("ix_workspace_invites_workspace_id")
    with op.batch_alter_table("workspace_members") as batch_op:
        batch_op.drop_index("ix_workspace_members_user_id")
        batch_op.drop_index("ix_workspace_members_workspace_id")
    op.drop_table("workspace_invites")
    op.drop_table("workspace_members")
    op.drop_table("workspaces")
