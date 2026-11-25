"""Persist the primary model provider with a default of local in the application."""

from alembic import op
import sqlalchemy as sa

revision = "b6f5d72ac119"
down_revision = "f42a981bc014"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "model_settings",
        sa.Column("name", sa.String(16), primary_key=True),
        sa.Column("provider", sa.String(8), nullable=False),
        sa.Column("cloud_model", sa.String(256), nullable=True),
        sa.CheckConstraint("name = 'primary'", name="ck_model_settings_singleton"),
        sa.CheckConstraint("provider IN ('local', 'cloud')", name="ck_model_settings_provider"),
    )


def downgrade():
    op.drop_table("model_settings")
