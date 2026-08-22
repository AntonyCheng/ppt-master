"""Add reusable prompt snippets for each user."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260822_0008"
down_revision = "20260821_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "prompt_snippets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False, server_default="个人"),
        sa.Column("used_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_prompt_snippets_owner_id", "prompt_snippets", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_prompt_snippets_owner_id", table_name="prompt_snippets")
    op.drop_table("prompt_snippets")
