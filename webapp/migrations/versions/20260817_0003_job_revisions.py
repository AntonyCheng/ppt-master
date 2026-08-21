"""Track the successful revision used as the base for each job."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260817_0003"
down_revision = "20260817_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add a nullable self-reference so old jobs remain valid."""

    op.add_column("jobs", sa.Column("base_job_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_jobs_base_job_id",
        "jobs",
        "jobs",
        ["base_job_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_jobs_base_job_id", "jobs", ["base_job_id"])


def downgrade() -> None:
    """Remove the revision link without deleting any jobs."""

    op.drop_index("ix_jobs_base_job_id", table_name="jobs")
    op.drop_constraint("fk_jobs_base_job_id", "jobs", type_="foreignkey")
    op.drop_column("jobs", "base_job_id")
