"""hds_v3: extend brain_customer_memory with summary/recent/count + external_id

Revision ID: b7f4c1d28e90
Revises: a3f9c2d51842
Create Date: 2026-05-16 09:00:00.000000

Adds conversation-summarization columns required by HDS-v3 Commit 5
(Messenger RAG). Existing fact-based rows continue to work; new
conversation rows use (company_id, external_id) as the logical key.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "b7f4c1d28e90"
down_revision: Union[str, None] = "a3f9c2d51842"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "brain_customer_memory",
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "brain_customer_memory",
        sa.Column("external_id", sa.Text(), nullable=True),
    )
    op.add_column(
        "brain_customer_memory",
        sa.Column("summary_text", sa.Text(), nullable=True),
    )
    op.add_column(
        "brain_customer_memory",
        sa.Column(
            "last_messages",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "brain_customer_memory",
        sa.Column(
            "message_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column(
        "brain_customer_memory",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
    )

    # Existing fact-based rows have customer_id+fact NOT NULL — relax so we
    # can also store conversation-summary rows without those fields.
    op.alter_column("brain_customer_memory", "customer_id", nullable=True)
    op.alter_column("brain_customer_memory", "fact", nullable=True)

    op.create_index(
        "ix_brain_customer_memory_company_external",
        "brain_customer_memory",
        ["company_id", "external_id"],
        unique=False,
    )
    op.create_index(
        "uq_brain_customer_memory_company_external",
        "brain_customer_memory",
        ["company_id", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_brain_customer_memory_company_external",
        table_name="brain_customer_memory",
    )
    op.drop_index(
        "ix_brain_customer_memory_company_external",
        table_name="brain_customer_memory",
    )
    op.alter_column("brain_customer_memory", "fact", nullable=False)
    op.alter_column("brain_customer_memory", "customer_id", nullable=False)
    op.drop_column("brain_customer_memory", "updated_at")
    op.drop_column("brain_customer_memory", "message_count")
    op.drop_column("brain_customer_memory", "last_messages")
    op.drop_column("brain_customer_memory", "summary_text")
    op.drop_column("brain_customer_memory", "external_id")
    op.drop_column("brain_customer_memory", "company_id")
