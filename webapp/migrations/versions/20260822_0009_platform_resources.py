"""Add platform resource scopes and the super administrator role."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260822_0009"
down_revision = "20260822_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE user_role ADD VALUE IF NOT EXISTS 'SUPER_ADMIN'")
        op.execute("UPDATE users SET role = 'SUPER_ADMIN' WHERE role = 'ADMIN'")
    op.add_column("templates", sa.Column("scope", sa.String(length=16), nullable=False, server_default="user"))
    op.add_column("templates", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("templates", sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"))
    op.create_index("ix_templates_scope", "templates", ["scope"])
    op.create_index("ix_templates_is_active", "templates", ["is_active"])
    op.add_column("prompt_snippets", sa.Column("scope", sa.String(length=16), nullable=False, server_default="user"))
    op.add_column("prompt_snippets", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("prompt_snippets", sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"))
    op.create_index("ix_prompt_snippets_scope", "prompt_snippets", ["scope"])
    op.create_index("ix_prompt_snippets_is_active", "prompt_snippets", ["is_active"])


def downgrade() -> None:
    op.drop_index("ix_prompt_snippets_is_active", table_name="prompt_snippets")
    op.drop_index("ix_prompt_snippets_scope", table_name="prompt_snippets")
    op.drop_column("prompt_snippets", "sort_order")
    op.drop_column("prompt_snippets", "is_active")
    op.drop_column("prompt_snippets", "scope")
    op.drop_index("ix_templates_is_active", table_name="templates")
    op.drop_index("ix_templates_scope", table_name="templates")
    op.drop_column("templates", "sort_order")
    op.drop_column("templates", "is_active")
    op.drop_column("templates", "scope")
