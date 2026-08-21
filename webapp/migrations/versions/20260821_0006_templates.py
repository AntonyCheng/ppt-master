"""Add user-owned PPTX template import drafts."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260821_0006"
down_revision = "20260818_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "templates",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("workspace_relpath", sa.String(512), nullable=False, unique=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="analyzing"),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_templates_owner_id", "templates", ["owner_id"])
    op.create_index("ix_templates_status", "templates", ["status"])


def downgrade() -> None:
    op.drop_index("ix_templates_status", table_name="templates")
    op.drop_index("ix_templates_owner_id", table_name="templates")
    op.drop_table("templates")
