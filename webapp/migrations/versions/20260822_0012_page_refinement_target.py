"""Record the target slide for page-scoped refinement jobs."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260822_0012"
down_revision = "20260822_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("target_slide_number", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "target_slide_number")
