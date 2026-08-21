"""Add platform model visibility and global default settings."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260818_0005"
down_revision = "20260817_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_access_policies",
        sa.Column("model_id", sa.String(255), primary_key=True),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_table(
        "system_settings",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.String(255), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_table("system_settings")
    op.drop_table("model_access_policies")
