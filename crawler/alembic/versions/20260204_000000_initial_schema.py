"""Initial schema

Revision ID: 001_initial
Revises:
Create Date: 2026-02-04 00:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '001_initial'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create raw_pages table
    op.create_table(
        'raw_pages',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('url', sa.String(2000), nullable=False),
        sa.Column('url_hash', sa.String(32), nullable=False),
        sa.Column('html', sa.Text(), nullable=True),
        sa.Column('html_compressed', sa.Text(), nullable=True),
        sa.Column('fetched_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('source', sa.String(100), nullable=False),
        sa.Column('city', sa.String(100), nullable=True),
        sa.Column('page_type', sa.String(50), server_default='listing'),
        sa.Column('status_code', sa.Integer(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
    )
    op.create_index('idx_raw_pages_url', 'raw_pages', ['url'])
    op.create_index('idx_raw_pages_url_hash', 'raw_pages', ['url_hash'])
    op.create_index('idx_raw_pages_fetched_at', 'raw_pages', ['fetched_at'])
    op.create_index('idx_raw_pages_source', 'raw_pages', ['source'])
    op.create_index('idx_raw_pages_city', 'raw_pages', ['city'])
    op.create_index('idx_raw_pages_source_fetched', 'raw_pages', ['source', 'fetched_at'])

    # Create properties table
    op.create_table(
        'properties',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('property_uid', sa.String(32), unique=True, nullable=False),
        sa.Column('canonical_address', sa.String(500), nullable=True),
        sa.Column('address_hash', sa.String(32), nullable=True),
        sa.Column('lat', sa.Float(), nullable=True),
        sa.Column('lng', sa.Float(), nullable=True),
        sa.Column('city', sa.String(100), nullable=True),
        sa.Column('locality', sa.String(200), nullable=True),
        sa.Column('current_price', sa.Float(), nullable=True),
        sa.Column('current_area', sa.Float(), nullable=True),
        sa.Column('beds', sa.Integer(), nullable=True),
        sa.Column('bathrooms', sa.Integer(), nullable=True),
        sa.Column('property_type', sa.String(100), nullable=True),
        sa.Column('first_seen', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('last_seen', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('last_updated', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('sources', sa.JSON(), server_default='[]'),
        sa.Column('source_urls', sa.JSON(), server_default='{}'),
        sa.Column('price_history_count', sa.Integer(), server_default='0'),
        sa.Column('listing_count', sa.Integer(), server_default='0'),
    )
    op.create_index('idx_properties_property_uid', 'properties', ['property_uid'])
    op.create_index('idx_properties_address_hash', 'properties', ['address_hash'])
    op.create_index('idx_properties_city', 'properties', ['city'])
    op.create_index('idx_properties_last_seen', 'properties', ['last_seen'])
    op.create_index('idx_properties_city_last_seen', 'properties', ['city', 'last_seen'])
    op.create_index('idx_properties_lat_lng', 'properties', ['lat', 'lng'])

    # Create listings table
    op.create_table(
        'listings',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('property_uid', sa.String(32), nullable=False),
        sa.Column('source', sa.String(100), nullable=False),
        sa.Column('city', sa.String(100), nullable=True),
        sa.Column('price', sa.Float(), nullable=True),
        sa.Column('address', sa.String(500), nullable=True),
        sa.Column('address_hash', sa.String(32), nullable=True),
        sa.Column('beds', sa.Integer(), nullable=True),
        sa.Column('bathrooms', sa.Integer(), nullable=True),
        sa.Column('area', sa.Float(), nullable=True),
        sa.Column('url', sa.String(2000), nullable=True),
        sa.Column('url_hash', sa.String(32), nullable=False),
        sa.Column('property_type', sa.String(100), nullable=True),
        sa.Column('builder_name', sa.String(200), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('amenities', sa.JSON(), nullable=True),
        sa.Column('posted_date', sa.DateTime(), nullable=True),
        sa.Column('scraped_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('raw_data', sa.JSON(), nullable=True),
        sa.Column('is_valid', sa.Boolean(), server_default='1'),
        sa.Column('is_duplicate', sa.Boolean(), server_default='0'),
        sa.Column('completeness_score', sa.Float(), nullable=True),
        sa.Column('property_id', sa.Integer(), sa.ForeignKey('properties.id'), nullable=True),
    )
    op.create_index('idx_listings_property_uid', 'listings', ['property_uid'])
    op.create_index('idx_listings_source', 'listings', ['source'])
    op.create_index('idx_listings_city', 'listings', ['city'])
    op.create_index('idx_listings_price', 'listings', ['price'])
    op.create_index('idx_listings_url_hash', 'listings', ['url_hash'])
    op.create_index('idx_listings_address_hash', 'listings', ['address_hash'])
    op.create_index('idx_listings_scraped_at', 'listings', ['scraped_at'])
    op.create_index('idx_listings_property_id', 'listings', ['property_id'])
    op.create_index('idx_listings_source_city_scraped', 'listings', ['source', 'city', 'scraped_at'])
    op.create_index('idx_listings_property_uid_source', 'listings', ['property_uid', 'source'])

    # Create property_events table
    op.create_table(
        'property_events',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('property_id', sa.Integer(), sa.ForeignKey('properties.id'), nullable=False),
        sa.Column('property_uid', sa.String(32), nullable=False),
        sa.Column('event_type', sa.String(50), nullable=False),
        sa.Column('old_value', sa.Text(), nullable=True),
        sa.Column('new_value', sa.Text(), nullable=True),
        sa.Column('value_numeric', sa.Float(), nullable=True),
        sa.Column('source', sa.String(100), nullable=True),
        sa.Column('timestamp', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('event_metadata', sa.JSON(), nullable=True),
    )
    op.create_index('idx_events_property_id', 'property_events', ['property_id'])
    op.create_index('idx_events_property_uid', 'property_events', ['property_uid'])
    op.create_index('idx_events_event_type', 'property_events', ['event_type'])
    op.create_index('idx_events_timestamp', 'property_events', ['timestamp'])
    op.create_index('idx_events_property_timestamp', 'property_events', ['property_id', 'timestamp'])
    op.create_index('idx_events_type_timestamp', 'property_events', ['event_type', 'timestamp'])

    # Create crawl_sessions table
    op.create_table(
        'crawl_sessions',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('session_id', sa.String(50), unique=True, nullable=False),
        sa.Column('source', sa.String(100), nullable=False),
        sa.Column('city', sa.String(100), nullable=True),
        sa.Column('started_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('ended_at', sa.DateTime(), nullable=True),
        sa.Column('status', sa.String(20), server_default='running'),
        sa.Column('pages_crawled', sa.Integer(), server_default='0'),
        sa.Column('listings_found', sa.Integer(), server_default='0'),
        sa.Column('listings_new', sa.Integer(), server_default='0'),
        sa.Column('listings_updated', sa.Integer(), server_default='0'),
        sa.Column('errors', sa.Integer(), server_default='0'),
        sa.Column('error_messages', sa.JSON(), server_default='[]'),
        sa.Column('config_snapshot', sa.JSON(), nullable=True),
    )
    op.create_index('idx_sessions_source', 'crawl_sessions', ['source'])
    op.create_index('idx_sessions_source_started', 'crawl_sessions', ['source', 'started_at'])

    # Create crawl_urls table
    op.create_table(
        'crawl_urls',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('url', sa.String(2000), nullable=False),
        sa.Column('url_hash', sa.String(32), nullable=False),
        sa.Column('source', sa.String(100), nullable=False),
        sa.Column('city', sa.String(100), nullable=True),
        sa.Column('page_type', sa.String(50), server_default='listing'),
        sa.Column('status', sa.String(20), server_default='pending'),
        sa.Column('attempts', sa.Integer(), server_default='0'),
        sa.Column('last_attempt', sa.DateTime(), nullable=True),
        sa.Column('last_success', sa.DateTime(), nullable=True),
        sa.Column('priority', sa.Integer(), server_default='100'),
        sa.Column('last_error', sa.Text(), nullable=True),
    )
    op.create_index('idx_crawl_urls_url_hash', 'crawl_urls', ['url_hash'])
    op.create_index('idx_crawl_urls_source', 'crawl_urls', ['source'])
    op.create_index('idx_crawl_urls_status_priority', 'crawl_urls', ['status', 'priority'])
    op.create_index('idx_crawl_urls_source_status', 'crawl_urls', ['source', 'status'])


def downgrade() -> None:
    op.drop_table('crawl_urls')
    op.drop_table('crawl_sessions')
    op.drop_table('property_events')
    op.drop_table('listings')
    op.drop_table('properties')
    op.drop_table('raw_pages')
