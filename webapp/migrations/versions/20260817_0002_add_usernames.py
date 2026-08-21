"""Add independent account names for username/password login."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260817_0002"
down_revision = "20260817_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add and backfill the account name column before enforcing uniqueness."""

    op.add_column("users", sa.Column("username", sa.String(64), nullable=True))
    op.execute(
        sa.text(
            """
            UPDATE users
            SET username = left(
                regexp_replace(split_part(email, '@', 1), '[^A-Za-z0-9_.-]', '_', 'g')
                || '_' || substr(replace(id::text, '-', ''), 1, 8),
                64
            )
            WHERE username IS NULL
            """
        )
    )
    op.alter_column("users", "username", nullable=False)
    op.create_index("ix_users_username", "users", ["username"], unique=True)


def downgrade() -> None:
    """Remove account names and restore email-only account identity."""

    op.drop_index("ix_users_username", table_name="users")
    op.drop_column("users", "username")
