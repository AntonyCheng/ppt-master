"""Make imported templates usable by generation jobs."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260821_0007"
down_revision = "20260821_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE templates SET status = 'ready' WHERE status = 'draft_ready'")
    op.add_column("jobs", sa.Column("template_id", sa.Uuid(), nullable=True))
    op.add_column("jobs", sa.Column("template_name", sa.String(length=160), nullable=True))
    op.add_column("jobs", sa.Column("template_workspace_relpath", sa.String(length=512), nullable=True))
    op.add_column("jobs", sa.Column("template_root", sa.String(length=255), nullable=True))
    op.create_foreign_key(
        "fk_jobs_template_id_templates",
        "jobs",
        "templates",
        ["template_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_jobs_template_id", "jobs", ["template_id"])


def downgrade() -> None:
    op.drop_index("ix_jobs_template_id", table_name="jobs")
    op.drop_constraint("fk_jobs_template_id_templates", "jobs", type_="foreignkey")
    op.drop_column("jobs", "template_root")
    op.drop_column("jobs", "template_workspace_relpath")
    op.drop_column("jobs", "template_name")
    op.drop_column("jobs", "template_id")
    op.execute("UPDATE templates SET status = 'draft_ready' WHERE status = 'ready'")
