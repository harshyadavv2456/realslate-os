"""
Tests for the Incremental Crawl Engine.
"""

import pytest
from datetime import datetime, timedelta
from core.incremental import IncrementalEngine, IncrementalResult
from core.db import DatabaseManager, Listing


class TestIncrementalEngine:
    """Test incremental processing logic."""
    
    @pytest.fixture
    def engine(self, config):
        # Reset singleton
        import core.incremental
        core.incremental._incremental_engine = None
        
        DatabaseManager._instance = None
        db = DatabaseManager(config)
        db.initialize()
        db.create_tables()
        
        eng = IncrementalEngine(config)
        eng.db_manager = db
        
        yield eng
        
        db.close()
        DatabaseManager._instance = None
    
    def test_new_listing(self, engine, sample_listing):
        """New listing should produce ACTION_NEW."""
        with engine.db_manager.session() as session:
            result = engine.process_listing(session, sample_listing)
        
        assert result.action == IncrementalResult.ACTION_NEW
        assert len(result.events) == 1
        assert result.events[0]["event_type"] == "new_listing"
    
    def test_unchanged_listing(self, engine, sample_listing):
        """Identical listing on second crawl should be UNCHANGED."""
        # First: insert the listing
        with engine.db_manager.session() as session:
            engine.db_manager.save_listing(session, sample_listing)
        
        # Second: process same listing
        with engine.db_manager.session() as session:
            result = engine.process_listing(session, sample_listing)
        
        assert result.action == IncrementalResult.ACTION_UNCHANGED
        assert len(result.events) == 0
    
    def test_updated_listing_price_change(self, engine, sample_listing, sample_listing_updated):
        """Price change should produce ACTION_UPDATED with price_change event."""
        # First: insert original
        with engine.db_manager.session() as session:
            engine.db_manager.save_listing(session, sample_listing)
        
        # Second: process with changed price
        with engine.db_manager.session() as session:
            result = engine.process_listing(session, sample_listing_updated)
        
        assert result.action == IncrementalResult.ACTION_UPDATED
        assert len(result.changes) > 0
        
        price_changes = [c for c in result.changes if c["field"] == "price"]
        assert len(price_changes) == 1
        assert price_changes[0]["old"] == 7500000.0
        assert price_changes[0]["new"] == 8200000.0
        
        price_events = [e for e in result.events if e["event_type"] == "price_change"]
        assert len(price_events) == 1
    
    def test_batch_processing(self, engine, sample_listing, sample_listing_rent):
        """Batch processing should handle multiple listings."""
        with engine.db_manager.session() as session:
            results = engine.process_batch(session, [sample_listing, sample_listing_rent])
        
        assert len(results["new"]) == 2
        assert len(results["unchanged"]) == 0
    
    def test_stats_tracking(self, engine, sample_listing):
        """Stats should track processing counts."""
        with engine.db_manager.session() as session:
            engine.process_listing(session, sample_listing)
        
        stats = engine.get_stats()
        assert stats["new"] == 1
        assert stats["total_processed"] == 1
    
    def test_detect_changes_no_change(self, engine):
        """No changes should produce empty list."""
        # Create a mock existing listing
        existing = Listing(
            price=5000000.0,
            beds=2,
            bathrooms=2,
            area=1000.0,
            address="Test Address",
            property_type="apartment",
        )
        
        incoming = {
            "price": 5000000.0,
            "beds": 2,
            "bathrooms": 2,
            "area": 1000.0,
            "address": "Test Address",
            "property_type": "apartment",
        }
        
        changes = engine._detect_changes(existing, incoming)
        assert len(changes) == 0
    
    def test_detect_changes_multiple_fields(self, engine):
        """Multiple field changes should all be detected."""
        existing = Listing(
            price=5000000.0,
            beds=2,
            area=1000.0,
        )
        
        incoming = {
            "price": 5500000.0,
            "beds": 3,
            "area": 1200.0,
        }
        
        changes = engine._detect_changes(existing, incoming)
        changed_fields = {c["field"] for c in changes}
        assert "price" in changed_fields
        assert "beds" in changed_fields
        assert "area" in changed_fields
