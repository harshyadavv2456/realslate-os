"""Add incremental engine tables and listing_type

Revision ID: 20260206_000000
Revises: 20260204_001000
Create Date: 2026-02-06

Adds:
- crawl_state table for incremental tracking
- sources table for source registry
- listing_type column on listings
- last_seen column on listings
- is_active column on listings
- locality column on listings
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic
revision = '20260206_000000'
down_revision = '20260204_001000'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- Create crawl_state table ---
    op.create_table(
        'crawl_state',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('source', sa.String(100), nullable=False),
        sa.Column('city', sa.String(100), nullable=False),
        sa.Column('listing_type', sa.String(10), nullable=False, server_default='buy'),
        sa.Column('last_crawl_at', sa.DateTime(), nullable=True),
        sa.Column('last_successful_crawl_at', sa.DateTime(), nullable=True),
        sa.Column('last_page_reached', sa.Integer(), server_default='0'),
        sa.Column('listings_found_last_run', sa.Integer(), server_default='0'),
        sa.Column('listings_new_last_run', sa.Integer(), server_default='0'),
        sa.Column('listings_updated_last_run', sa.Integer(), server_default='0'),
        sa.Column('listings_unchanged_last_run', sa.Integer(), server_default='0'),
        sa.Column('total_crawls', sa.Integer(), server_default='0'),
        sa.Column('total_listings_found', sa.Integer(), server_default='0'),
        sa.Column('total_errors', sa.Integer(), server_default='0'),
        sa.Column('recrawl_priority', sa.Integer(), server_default='100'),
        sa.Column('next_crawl_after', sa.DateTime(), nullable=True),
        sa.Column('consecutive_errors', sa.Integer(), server_default='0'),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('last_error_at', sa.DateTime(), nullable=True),
    )
    
    op.create_index(
        'idx_crawl_state_source_city_type',
        'crawl_state',
        ['source', 'city', 'listing_type'],
        unique=True,
    )
    op.create_index('idx_crawl_state_next_crawl', 'crawl_state', ['next_crawl_after'])
    op.create_index('idx_crawl_state_priority', 'crawl_state', ['recrawl_priority'])
    
    # --- Create sources table ---
    op.create_table(
        'sources',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('name', sa.String(100), unique=True, nullable=False),
        sa.Column('base_url', sa.String(500), nullable=True),
        sa.Column('enabled', sa.Boolean(), server_default=sa.text('true')),
        sa.Column('priority', sa.Integer(), server_default='50'),
        sa.Column('total_listings', sa.Integer(), server_default='0'),
        sa.Column('total_properties', sa.Integer(), server_default='0'),
        sa.Column('total_crawls', sa.Integer(), server_default='0'),
        sa.Column('last_crawl_at', sa.DateTime(), nullable=True),
        sa.Column('last_success_at', sa.DateTime(), nullable=True),
        sa.Column('success_rate', sa.Float(), server_default='1.0'),
        sa.Column('avg_listings_per_crawl', sa.Float(), server_default='0.0'),
        sa.Column('is_banned', sa.Boolean(), server_default=sa.text('false')),
        sa.Column('ban_detected_at', sa.DateTime(), nullable=True),
        sa.Column('ban_lifted_at', sa.DateTime(), nullable=True),
        sa.Column('config_hash', sa.String(32), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
    )
    
    op.create_index('idx_sources_name', 'sources', ['name'])
    
    # --- Add new columns to listings ---
    op.add_column('listings', sa.Column(
        'listing_type', sa.String(10), nullable=True, server_default='buy'
    ))
    op.add_column('listings', sa.Column(
        'last_seen', sa.DateTime(), nullable=True
    ))
    op.add_column('listings', sa.Column(
        'is_active', sa.Boolean(), nullable=True, server_default=sa.text('true')
    ))
    op.add_column('listings', sa.Column(
        'locality', sa.String(200), nullable=True
    ))
    
    # Backfill existing listings
    op.execute("UPDATE listings SET listing_type = 'buy' WHERE listing_type IS NULL")
    op.execute("UPDATE listings SET last_seen = scraped_at WHERE last_seen IS NULL")
    op.execute("UPDATE listings SET is_active = true WHERE is_active IS NULL")
    
    # Create indexes on new columns
    op.create_index('idx_listings_listing_type', 'listings', ['listing_type'])
    op.create_index('idx_listings_last_seen', 'listings', ['last_seen'])
    op.create_index('idx_listings_active_source', 'listings', ['is_active', 'source'])


def downgrade() -> None:
    # Drop new indexes
    op.drop_index('idx_listings_active_source', 'listings')
    op.drop_index('idx_listings_last_seen', 'listings')
    op.drop_index('idx_listings_listing_type', 'listings')
    
    # Remove new columns from listings
    op.drop_column('listings', 'locality')
    op.drop_column('listings', 'is_active')
    op.drop_column('listings', 'last_seen')
    op.drop_column('listings', 'listing_type')
    
    # Drop new tables
    op.drop_table('sources')
    op.drop_table('crawl_state')
