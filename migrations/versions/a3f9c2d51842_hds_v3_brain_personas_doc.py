"""hds_v3: brain_personas operating manual table

Revision ID: a3f9c2d51842
Revises: 6eb8936e7f1a
Create Date: 2026-05-15 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'a3f9c2d51842'
down_revision: Union[str, None] = '6eb8936e7f1a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'brain_personas',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('company_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('version', sa.Integer(), server_default=sa.text('1'), nullable=False),
        sa.Column('persona_text', sa.Text(), nullable=False),
        sa.Column('word_count', sa.Integer(), nullable=True),
        sa.Column('source_urls', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            'gemini_cost_usd',
            sa.Numeric(precision=10, scale=6),
            server_default=sa.text('0'),
            nullable=True,
        ),
        sa.Column('tokens_in', sa.Integer(), server_default=sa.text('0'), nullable=True),
        sa.Column('tokens_out', sa.Integer(), server_default=sa.text('0'), nullable=True),
        sa.Column(
            'meta',
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=True,
        ),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('NOW()'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_brain_personas_company_id_version',
        'brain_personas',
        ['company_id', sa.text('version DESC')],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_brain_personas_company_id_version', table_name='brain_personas')
    op.drop_table('brain_personas')
