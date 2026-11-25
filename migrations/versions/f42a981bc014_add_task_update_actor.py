"""Record the employee who last transitioned a follow-up task."""

from alembic import op
import sqlalchemy as sa

revision = "f42a981bc014"
down_revision = "d6714034f15a"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("follow_up_tasks", sa.Column("updated_by", sa.String(64), nullable=True))


def downgrade():
    op.drop_column("follow_up_tasks", "updated_by")
