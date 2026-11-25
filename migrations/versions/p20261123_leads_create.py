"""Create leads table for confirmed enquiries (not populated in P2-02)."""

from alembic import op
import sqlalchemy as sa

revision = "p20261123_leads"
down_revision = "04fe24a7e5c7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "leads",
        sa.Column("lead_id", sa.String(length=36), nullable=False),
        sa.Column("property_id", sa.String(length=20), nullable=False),
        sa.Column("customer_name", sa.String(length=100), nullable=False),
        sa.Column("phone", sa.String(length=20), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("contact_purpose", sa.String(length=255), nullable=False),
        sa.Column("consented_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=30), server_default="NEW", nullable=False),
        sa.Column("source", sa.String(length=30), server_default="STREAMLIT", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["property_id"], ["properties.property_id"], ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("lead_id"),
    )
    op.create_index("ix_leads_property_id", "leads", ["property_id"])


def downgrade() -> None:
    op.drop_index("ix_leads_property_id", table_name="leads")
    op.drop_table("leads")
