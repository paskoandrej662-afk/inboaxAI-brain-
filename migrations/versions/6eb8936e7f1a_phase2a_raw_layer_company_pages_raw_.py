"""phase2a raw layer (company_pages, raw_page_blocks, ingestion_jobs, ingestion_costs)

Revision ID: 6eb8936e7f1a
Revises: eba2b4ef7fe9
Create Date: 2026-05-11 21:34:47.040220

Phase 2A — Universal Ingestion Engine v2 raw layer.
Pridava 4 tabulky pre surovu vrstvu (Layer A) ingestion pipeline-u:
  - ingestion_jobs   : jeden riadok = jedno spustenie ingestu pre danu spolocnost
  - company_pages    : kazda navstivena/renderovana stranka v ramci jobu
  - raw_page_blocks  : heuristicky detegovane bloky kandidati na stranke (Phase 2B ich klasifikuje)
  - ingestion_costs  : audit nakladov (render, vision call, image describe, ...) v EUR + tokeny

Konvencia (zhodna so vsetkymi brain_* tabulkami): company_id uuid NOT NULL, BEZ FK.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '6eb8936e7f1a'
down_revision: Union[str, None] = 'eba2b4ef7fe9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- ingestion_jobs -------------------------------------------------
    op.create_table(
        'ingestion_jobs',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('company_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('source_url', sa.Text(), nullable=False),
        sa.Column('mode', sa.Text(), server_default=sa.text("'standard'"), nullable=False),
        sa.Column('status', sa.Text(), server_default=sa.text("'queued'"), nullable=False),
        sa.Column('progress', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('budget_eur', sa.Numeric(10, 4), server_default=sa.text('1.20'), nullable=False),
        sa.Column('cost_total_eur', sa.Numeric(10, 6), server_default=sa.text('0'), nullable=False),
        sa.Column('pages_visited', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('pages_succeeded', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('pages_failed', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('blocks_found', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('errors', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column('warnings', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column('result_summary', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("mode IN ('standard', 'deep', 'quick')", name='ck_ingestion_jobs_mode'),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'partial', 'failed')",
            name='ck_ingestion_jobs_status',
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_ingestion_jobs_company_status_created',
        'ingestion_jobs',
        ['company_id', 'status', sa.text('created_at DESC')],
        unique=False,
    )
    op.create_index(
        'ix_ingestion_jobs_active',
        'ingestion_jobs',
        ['status', 'created_at'],
        unique=False,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )

    # --- company_pages --------------------------------------------------
    op.create_table(
        'company_pages',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('company_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('job_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('url', sa.Text(), nullable=False),
        sa.Column('url_normalized', sa.Text(), nullable=False),
        sa.Column('final_url', sa.Text(), nullable=True),
        sa.Column('title', sa.Text(), nullable=True),
        sa.Column('http_status', sa.Integer(), nullable=True),
        sa.Column('render_status', sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column('render_method', sa.Text(), server_default=sa.text("'playwright_headless'"), nullable=False),
        sa.Column('render_ms', sa.Integer(), nullable=True),
        sa.Column('retry_count', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('discovery_method', sa.Text(), server_default=sa.text("'bfs'"), nullable=False),
        sa.Column('priority_score', sa.Numeric(3, 2), server_default=sa.text('0.50'), nullable=False),
        sa.Column('depth', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('parent_url', sa.Text(), nullable=True),
        # raw HTML; v Phase 2A nechavame v DB, neskor moze ist do object storage
        sa.Column('html', sa.Text(), nullable=True),
        sa.Column('html_storage_path', sa.Text(), nullable=True),
        # sha256 of html for change detection
        sa.Column('content_hash', sa.Text(), nullable=True),
        sa.Column('visible_text', sa.Text(), nullable=True),
        sa.Column('dom_size', sa.Integer(), nullable=True),
        sa.Column('text_length', sa.Integer(), nullable=True),
        sa.Column('screenshot_path', sa.Text(), nullable=True),
        sa.Column('raw_data', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint(
            "render_status IN ('pending', 'success', 'timeout', 'blocked', 'error', 'skipped')",
            name='ck_company_pages_render_status',
        ),
        sa.CheckConstraint(
            "render_method IN ('playwright_headless', 'httpx', 'sitemap_only')",
            name='ck_company_pages_render_method',
        ),
        sa.CheckConstraint(
            "discovery_method IN ('sitemap', 'homepage_link', 'bfs', 'rendered_link', 'seed', 'robots')",
            name='ck_company_pages_discovery_method',
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'uq_company_pages_company_url_normalized',
        'company_pages',
        ['company_id', 'url_normalized'],
        unique=True,
    )
    op.create_index('ix_company_pages_job_id', 'company_pages', ['job_id'], unique=False)
    op.create_index(
        'ix_company_pages_company_render_status',
        'company_pages',
        ['company_id', 'render_status'],
        unique=False,
    )
    op.create_index(
        'ix_company_pages_company_priority',
        'company_pages',
        ['company_id', sa.text('priority_score DESC')],
        unique=False,
    )

    # --- raw_page_blocks ------------------------------------------------
    op.create_table(
        'raw_page_blocks',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('job_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('company_id', postgresql.UUID(as_uuid=True), nullable=False),
        # logical FK to company_pages.id (bez DB FK, brain konvencia)
        sa.Column('page_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('source_url', sa.Text(), nullable=False),
        sa.Column('block_type', sa.Text(), server_default=sa.text("'candidate'"), nullable=False),
        sa.Column('block_type_hint', sa.Text(), nullable=True),
        sa.Column('selector', sa.Text(), nullable=True),
        sa.Column('dom_path', sa.Text(), nullable=True),
        sa.Column('parent_selector', sa.Text(), nullable=True),
        sa.Column('section_heading', sa.Text(), nullable=True),
        sa.Column('text', sa.Text(), nullable=True),
        sa.Column('html', sa.Text(), nullable=True),
        # sha256 of text for dedup
        sa.Column('text_hash', sa.Text(), nullable=True),
        sa.Column('headings', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column('images', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column('links', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column('signals', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column('position_index', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('depth', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('extraction_method', sa.Text(), server_default=sa.text("'heuristic_block'"), nullable=False),
        sa.Column('confidence', sa.Numeric(3, 2), server_default=sa.text('0.50'), nullable=False),
        sa.Column('status', sa.Text(), server_default=sa.text("'raw'"), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_raw_page_blocks_page_id', 'raw_page_blocks', ['page_id'], unique=False)
    op.create_index('ix_raw_page_blocks_job_id', 'raw_page_blocks', ['job_id'], unique=False)
    op.create_index(
        'ix_raw_page_blocks_company_type_hint',
        'raw_page_blocks',
        ['company_id', 'block_type_hint'],
        unique=False,
    )

    # --- ingestion_costs ------------------------------------------------
    op.create_table(
        'ingestion_costs',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('job_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('operation', sa.Text(), nullable=False),
        sa.Column('model', sa.Text(), nullable=True),
        sa.Column('input_tokens', sa.Integer(), nullable=True),
        sa.Column('output_tokens', sa.Integer(), nullable=True),
        sa.Column('cache_read_tokens', sa.Integer(), nullable=True),
        sa.Column('cache_creation_tokens', sa.Integer(), nullable=True),
        sa.Column('bytes_in', sa.Integer(), nullable=True),
        sa.Column('bytes_out', sa.Integer(), nullable=True),
        sa.Column('duration_ms', sa.Integer(), nullable=True),
        sa.Column('est_cost_eur', sa.Numeric(10, 6), server_default=sa.text('0'), nullable=False),
        sa.Column('hard_limit_hit', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_ingestion_costs_job_created', 'ingestion_costs', ['job_id', 'created_at'], unique=False)

    # --- updated_at trigger pre ingestion_jobs --------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION ingestion_jobs_set_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
          NEW.updated_at = now();
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER ingestion_jobs_updated_at_trg
          BEFORE UPDATE ON ingestion_jobs
          FOR EACH ROW EXECUTE FUNCTION ingestion_jobs_set_updated_at();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS ingestion_jobs_updated_at_trg ON ingestion_jobs;")
    op.execute("DROP FUNCTION IF EXISTS ingestion_jobs_set_updated_at();")

    op.drop_index('ix_ingestion_costs_job_created', table_name='ingestion_costs')
    op.drop_table('ingestion_costs')

    op.drop_index('ix_raw_page_blocks_company_type_hint', table_name='raw_page_blocks')
    op.drop_index('ix_raw_page_blocks_job_id', table_name='raw_page_blocks')
    op.drop_index('ix_raw_page_blocks_page_id', table_name='raw_page_blocks')
    op.drop_table('raw_page_blocks')

    op.drop_index('ix_company_pages_company_priority', table_name='company_pages')
    op.drop_index('ix_company_pages_company_render_status', table_name='company_pages')
    op.drop_index('ix_company_pages_job_id', table_name='company_pages')
    op.drop_index('uq_company_pages_company_url_normalized', table_name='company_pages')
    op.drop_table('company_pages')

    op.drop_index('ix_ingestion_jobs_active', table_name='ingestion_jobs')
    op.drop_index('ix_ingestion_jobs_company_status_created', table_name='ingestion_jobs')
    op.drop_table('ingestion_jobs')
