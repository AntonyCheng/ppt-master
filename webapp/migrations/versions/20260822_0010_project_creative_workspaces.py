"""Persist project requirements, outlines, and confirmation snapshots."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260822_0010"
down_revision = "20260822_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create project-local authoring state without changing existing jobs."""

    op.create_table(
        "project_creative_states",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False, server_default="requirements"),
        sa.Column("requirements", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("outline", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("notes_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("selected_template_id", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["selected_template_id"], ["templates.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("project_id"),
    )
    op.create_index("ix_project_creative_states_stage", "project_creative_states", ["stage"])
    op.create_table(
        "project_creative_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_project_creative_snapshots_project_id", "project_creative_snapshots", ["project_id"])
    op.create_index("ix_project_creative_snapshots_kind", "project_creative_snapshots", ["kind"])


def downgrade() -> None:
    """Remove optional authoring state while retaining projects and jobs."""

    op.drop_index("ix_project_creative_snapshots_kind", table_name="project_creative_snapshots")
    op.drop_index("ix_project_creative_snapshots_project_id", table_name="project_creative_snapshots")
    op.drop_table("project_creative_snapshots")
    op.drop_index("ix_project_creative_states_stage", table_name="project_creative_states")
    op.drop_table("project_creative_states")
