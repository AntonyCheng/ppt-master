"""Persist page-scoped AI refinement conversations."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260822_0013"
down_revision = "20260822_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "page_refinement_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.Column("slide_number", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_page_refinement_messages_project_id", "page_refinement_messages", ["project_id"])
    op.create_index("ix_page_refinement_messages_job_id", "page_refinement_messages", ["job_id"])
    op.create_index("ix_page_refinement_messages_slide_number", "page_refinement_messages", ["slide_number"])


def downgrade() -> None:
    op.drop_index("ix_page_refinement_messages_slide_number", table_name="page_refinement_messages")
    op.drop_index("ix_page_refinement_messages_job_id", table_name="page_refinement_messages")
    op.drop_index("ix_page_refinement_messages_project_id", table_name="page_refinement_messages")
    op.drop_table("page_refinement_messages")
