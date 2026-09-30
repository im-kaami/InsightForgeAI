import sqlalchemy as sa
from alembic import op

revision = "0005_recipes_and_rules"
down_revision = "0004_dataset_notes"
branch_labels = None
depends_on = None


def _json_column(name: str) -> sa.Column:
    return sa.Column(name, sa.JSON(), nullable=False, server_default=sa.text("'{}'"))


def upgrade() -> None:
    with op.batch_alter_table("datasets") as batch_op:
        batch_op.add_column(_json_column("recipe_json"))
        batch_op.add_column(_json_column("rules_json"))
    with op.batch_alter_table("dataset_versions") as batch_op:
        batch_op.add_column(_json_column("recipe_json"))
        batch_op.add_column(_json_column("validation_json"))


def downgrade() -> None:
    with op.batch_alter_table("dataset_versions") as batch_op:
        batch_op.drop_column("validation_json")
        batch_op.drop_column("recipe_json")
    with op.batch_alter_table("datasets") as batch_op:
        batch_op.drop_column("rules_json")
        batch_op.drop_column("recipe_json")
