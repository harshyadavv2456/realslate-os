"""Add versioning columns to listings and properties

Revision ID: 002_versioning
Revises: 001_initial
Create Date: 2026-02-04 10:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '002_versioning'
down_revision: Union[str, None] = '001_initial'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add versioning columns to support change tracking."""
    
    # Add versioning columns to listings table
    op.add_column('listings', sa.Column('version', sa.Integer(), server_default='1', nullable=False))
    op.add_column('listings', sa.Column('previous_version_id', sa.Integer(), sa.ForeignKey('listings.id'), nullable=True))
    op.add_column('listings', sa.Column('is_latest', sa.Boolean(), server_default='1', index=True))
    
    # Add index for faster latest version lookups
    op.create_index('idx_listings_url_hash_latest', 'listings', ['url_hash', 'is_latest'])
    
    # Add versioning columns to properties table
    op.add_column('properties', sa.Column('version', sa.Integer(), server_default='1', nullable=False))
    op.add_column('properties', sa.Column('data_hash', sa.String(32), nullable=True))
    op.add_column('properties', sa.Column('status', sa.String(20), server_default='active'))
    op.add_column('properties', sa.Column('status_changed_at', sa.DateTime(), nullable=True))
    
    # Add indexes for properties versioning
    op.create_index('idx_properties_data_hash', 'properties', ['data_hash'])
    op.create_index('idx_properties_status', 'properties', ['status'])


def downgrade() -> None:
    """Remove versioning columns."""
    
    # Drop indexes
    op.drop_index('idx_properties_status', 'properties')
    op.drop_index('idx_properties_data_hash', 'properties')
    op.drop_index('idx_listings_url_hash_latest', 'listings')
    
    # Drop columns from properties
    op.drop_column('properties', 'status_changed_at')
    op.drop_column('properties', 'status')
    op.drop_column('properties', 'data_hash')
    op.drop_column('properties', 'version')
    
    # Drop columns from listings
    op.drop_column('listings', 'is_latest')
    op.drop_column('listings', 'previous_version_id')
    op.drop_column('listings', 'version')
