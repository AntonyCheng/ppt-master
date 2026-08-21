"""Add managed providers, models, and asynchronous user deletion state."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260817_0004"
down_revision = "20260817_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("deletion_requested_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("jobs", sa.Column("cancellation_requested", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index("ix_jobs_cancellation_requested", "jobs", ["cancellation_requested"])
    op.create_table(
        "providers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("slug", sa.String(64), nullable=False, unique=True),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("base_url", sa.String(1024), nullable=False),
        sa.Column("api_key_ciphertext", sa.Text(), nullable=False),
        sa.Column("api_key_hint", sa.String(24), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_providers_slug", "providers", ["slug"], unique=True)
    op.create_table(
        "provider_models",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("provider_id", sa.Uuid(), sa.ForeignKey("providers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("model_id", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("provider_id", "model_id", name="uq_provider_model_id"),
    )
    op.create_index("ix_provider_models_provider_id", "provider_models", ["provider_id"])


def downgrade() -> None:
    op.drop_index("ix_provider_models_provider_id", table_name="provider_models")
    op.drop_table("provider_models")
    op.drop_index("ix_providers_slug", table_name="providers")
    op.drop_table("providers")
    op.drop_index("ix_jobs_cancellation_requested", table_name="jobs")
    op.drop_column("jobs", "cancellation_requested")
    op.drop_column("users", "deletion_requested_at")
