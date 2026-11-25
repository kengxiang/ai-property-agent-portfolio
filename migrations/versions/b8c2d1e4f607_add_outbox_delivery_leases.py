"""Add Outbox claim ownership and retry scheduling."""

import sqlalchemy as sa
from alembic import op

revision = "b8c2d1e4f607"
down_revision = "a0a4398892c4"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("outbox_events", sa.Column("next_attempt_at", sa.DateTime(timezone=True)))
    op.add_column("outbox_events", sa.Column("lease_expires_at", sa.DateTime(timezone=True)))
    op.add_column("outbox_events", sa.Column("claim_token", sa.String(36)))
    op.drop_constraint("ck_outbox_delivery_status", "outbox_events", type_="check")
    op.create_check_constraint(
        "ck_outbox_delivery_status", "outbox_events",
        "delivery_status IN ('PENDING', 'PROCESSING', 'DELIVERED', 'RETRY', 'FAILED')",
    )


def downgrade():
    op.execute("UPDATE outbox_events SET delivery_status = 'RETRY' WHERE delivery_status = 'FAILED'")
    op.drop_constraint("ck_outbox_delivery_status", "outbox_events", type_="check")
    op.create_check_constraint(
        "ck_outbox_delivery_status", "outbox_events",
        "delivery_status IN ('PENDING', 'PROCESSING', 'DELIVERED', 'RETRY')",
    )
    op.drop_column("outbox_events", "claim_token")
    op.drop_column("outbox_events", "lease_expires_at")
    op.drop_column("outbox_events", "next_attempt_at")
