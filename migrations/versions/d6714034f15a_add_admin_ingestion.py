"""Add administrator accounts, revocable sessions and property audit."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "d6714034f15a"
down_revision = "c31e24b905af"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "admin_accounts",
        sa.Column("username", sa.String(64), primary_key=True),
        sa.Column("password_hash", sa.String(128), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
    )
    op.create_table(
        "admin_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("username", sa.String(64), sa.ForeignKey("admin_accounts.username", ondelete="CASCADE"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_admin_sessions_username", "admin_sessions", ["username"])
    op.create_index("ix_admin_sessions_expires_at", "admin_sessions", ["expires_at"])
    op.create_table(
        "admin_audit",
        sa.Column("audit_id", sa.String(36), primary_key=True),
        sa.Column("username", sa.String(64), sa.ForeignKey("admin_accounts.username"), nullable=False),
        sa.Column("action", sa.String(30), nullable=False),
        sa.Column("property_ids", JSONB(), nullable=False),
        sa.Column("succeeded", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_admin_audit_username", "admin_audit", ["username"])


def downgrade():
    for table in ("admin_audit", "admin_sessions", "admin_accounts"):
        op.drop_table(table)
