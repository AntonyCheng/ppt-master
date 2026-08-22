"""Add project-local source materials."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260822_0011"
down_revision = "20260822_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_materials",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("relative_path", sa.String(length=1024), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=False, server_default="application/octet-stream"),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="ready"),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_project_materials_project_id", "project_materials", ["project_id"])
    op.create_index("ix_project_materials_status", "project_materials", ["status"])


def downgrade() -> None:
    op.drop_index("ix_project_materials_status", table_name="project_materials")
    op.drop_index("ix_project_materials_project_id", table_name="project_materials")
    op.drop_table("project_materials")
