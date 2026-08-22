"""Add deterministic ordering to page refinement conversations."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260822_0014"
down_revision = "20260822_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SEQUENCE page_refinement_messages_order_seq")
    op.add_column(
        "page_refinement_messages",
        sa.Column("message_order", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "page_refinement_messages",
        sa.Column("client_message_id", sa.String(length=64), nullable=True),
    )
    op.execute(
        """
        WITH ordered AS (
            SELECT id,
                   row_number() OVER (
                       ORDER BY created_at,
                                CASE WHEN role = 'user' THEN 0 ELSE 1 END,
                                id
                   ) AS order_value
            FROM page_refinement_messages
        )
        UPDATE page_refinement_messages AS messages
        SET message_order = ordered.order_value
        FROM ordered
        WHERE messages.id = ordered.id
        """
    )
    op.execute(
        """
        SELECT setval(
            'page_refinement_messages_order_seq',
            COALESCE((SELECT MAX(message_order) FROM page_refinement_messages), 1),
            EXISTS (SELECT 1 FROM page_refinement_messages)
        )
        """
    )
    op.alter_column(
        "page_refinement_messages",
        "message_order",
        existing_type=sa.BigInteger(),
        nullable=False,
        server_default=sa.text("nextval('page_refinement_messages_order_seq'::regclass)"),
    )
    op.create_index(
        "ix_page_refinement_messages_conversation_order",
        "page_refinement_messages",
        ["project_id", "slide_number", "message_order"],
    )
    op.create_index(
        "ux_page_refinement_messages_client_id",
        "page_refinement_messages",
        ["project_id", "slide_number", "client_message_id"],
        unique=True,
        postgresql_where=sa.text("client_message_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_page_refinement_messages_conversation_order",
        table_name="page_refinement_messages",
    )
    op.drop_index(
        "ux_page_refinement_messages_client_id",
        table_name="page_refinement_messages",
    )
    op.drop_column("page_refinement_messages", "client_message_id")
    op.drop_column("page_refinement_messages", "message_order")
    op.execute("DROP SEQUENCE page_refinement_messages_order_seq")
