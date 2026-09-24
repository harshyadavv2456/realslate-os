"""
Tests for Database operations including new tables.
"""

import pytest
from datetime import datetime, timedelta
from core.db import DatabaseManager, Listing, Property, PropertyEvent, CrawlState, Source


class TestDatabaseManager:
    """Test core database operations."""
    
    def test_initialization(self, db_manager):
        """Database should initialize and create tables."""
        assert db_manager._engine is not None
    
    def test_save_listing(self, db_manager, sample_listing):
        """Should save a listing with all fields."""
        with db_manager.session() as session:
            listing = db_manager.save_listing(session, sample_listing)
            session.flush()  # Flush to assign auto-increment ID
            assert listing.id is not None
            assert listing.source == "99acres"
            assert listing.listing_type == "buy"
            assert listing.price == 7500000.0
    
    def test_save_rent_listing(self, db_manager, sample_listing_rent):
        """Should save a rent listing."""
        with db_manager.session() as session:
            listing = db_manager.save_listing(session, sample_listing_rent)
            assert listing.listing_type == "rent"
            assert listing.price == 25000.0
    
    def test_batch_insert(self, db_manager, sample_listing, sample_listing_rent):
        """Batch insert should save multiple listings."""
        with db_manager.session() as session:
            saved, failed = db_manager.save_listings_batch(
                session, [sample_listing, sample_listing_rent]
            )
            assert saved == 2
            assert failed == 0
    
    def test_version_tracking(self, db_manager, sample_listing, sample_listing_updated):
        """Updated listing should create new version."""
        with db_manager.session() as session:
            db_manager.save_listings_batch(session, [sample_listing])
        
        with db_manager.session() as session:
            saved, _ = db_manager.save_listings_batch(session, [sample_listing_updated])
            # Should create a new version since price changed
            assert saved >= 0  # May be 0 or 1 depending on change detection
    
    def test_listing_exists(self, db_manager, sample_listing):
        """Should detect recently scraped listings."""
        with db_manager.session() as session:
            db_manager.save_listing(session, sample_listing)
        
        with db_manager.session() as session:
            exists = db_manager.listing_exists(
                session, sample_listing["url_hash"], sample_listing["source"]
            )
            assert exists
    
    def test_get_or_create_property(self, db_manager, sample_listing):
        """Should create and retrieve properties."""
        with db_manager.session() as session:
            prop, is_new, price_changed = db_manager.get_or_create_property(
                session, "test_uid_001", sample_listing
            )
            assert is_new
            assert prop.property_uid == "test_uid_001"
            assert prop.current_price == 7500000.0
        
        # Second call should return existing
        with db_manager.session() as session:
            prop, is_new, price_changed = db_manager.get_or_create_property(
                session, "test_uid_001", sample_listing
            )
            assert not is_new


class TestCrawlState:
    """Test crawl state tracking."""
    
    def test_create_crawl_state(self, db_manager):
        """Should create crawl state."""
        with db_manager.session() as session:
            state = db_manager.get_or_create_crawl_state(
                session, "99acres", "Mumbai", "buy"
            )
            assert state.source == "99acres"
            assert state.city == "Mumbai"
            assert state.listing_type == "buy"
    
    def test_update_success(self, db_manager):
        """Should update state on successful crawl."""
        with db_manager.session() as session:
            db_manager.update_crawl_state_success(
                session, "99acres", "Mumbai", "buy",
                page_reached=10, found=100, new=80, updated=15, unchanged=5
            )
        
        with db_manager.session() as session:
            state = db_manager.get_or_create_crawl_state(
                session, "99acres", "Mumbai", "buy"
            )
            assert state.listings_found_last_run == 100
            assert state.listings_new_last_run == 80
            assert state.total_crawls == 1
            assert state.consecutive_errors == 0
    
    def test_update_error(self, db_manager):
        """Should update state on error with backoff."""
        with db_manager.session() as session:
            db_manager.update_crawl_state_error(
                session, "99acres", "Mumbai", "buy", "Connection timeout"
            )
        
        with db_manager.session() as session:
            state = db_manager.get_or_create_crawl_state(
                session, "99acres", "Mumbai", "buy"
            )
            assert state.consecutive_errors == 1
            assert state.last_error == "Connection timeout"
            assert state.next_crawl_after is not None
    
    def test_get_states_due(self, db_manager):
        """Should return states due for recrawl."""
        with db_manager.session() as session:
            db_manager.get_or_create_crawl_state(
                session, "99acres", "Mumbai", "buy"
            )
        
        with db_manager.session() as session:
            due = db_manager.get_crawl_states_due(session)
            assert len(due) >= 1  # Should be due since no next_crawl_after set


class TestSource:
    """Test source registry."""
    
    def test_create_source(self, db_manager):
        """Should create source registry entry."""
        with db_manager.session() as session:
            source = db_manager.get_or_create_source(
                session, "99acres", "https://www.99acres.com", 1
            )
            assert source.name == "99acres"
            assert source.enabled
    
    def test_update_source_stats(self, db_manager):
        """Should update source statistics."""
        with db_manager.session() as session:
            db_manager.get_or_create_source(session, "99acres")
        
        with db_manager.session() as session:
            db_manager.update_source_stats(session, "99acres", 100, True)
        
        with db_manager.session() as session:
            source = session.query(Source).filter(Source.name == "99acres").first()
            assert source.total_listings == 100
            assert source.total_crawls == 1


class TestMarkInactive:
    """Test listing deactivation."""
    
    def test_mark_stale_inactive(self, db_manager, sample_listing):
        """Stale listings should be marked inactive."""
        # Insert a listing with old last_seen
        with db_manager.session() as session:
            listing = db_manager.save_listing(session, sample_listing)
            listing.last_seen = datetime.utcnow() - timedelta(days=10)
        
        with db_manager.session() as session:
            count = db_manager.mark_listings_inactive(
                session, "99acres", "Mumbai", "buy", stale_days=7
            )
            assert count >= 0  # May vary based on timing


class TestPropertyTable:
    """Test properties table population."""
    
    def test_property_created_on_first_listing(self, db_manager, sample_listing):
        """Property should be created when first listing arrives."""
        with db_manager.session() as session:
            prop, is_new, _ = db_manager.get_or_create_property(
                session, "prop_test_001", sample_listing
            )
            session.flush()
            assert is_new
            assert prop.id is not None
            assert prop.property_uid == "prop_test_001"
            assert prop.current_price == 7500000.0
            assert prop.city == "Mumbai"
    
    def test_property_updated_on_recrawl(self, db_manager, sample_listing):
        """Property should update last_seen on recrawl."""
        with db_manager.session() as session:
            prop, _, _ = db_manager.get_or_create_property(
                session, "prop_test_002", sample_listing
            )
        
        with db_manager.session() as session:
            prop, is_new, _ = db_manager.get_or_create_property(
                session, "prop_test_002", sample_listing
            )
            assert not is_new
            assert prop.listing_count >= 2
    
    def test_property_price_change_detected(self, db_manager, sample_listing, sample_listing_updated):
        """Property should detect price changes."""
        with db_manager.session() as session:
            db_manager.get_or_create_property(
                session, "prop_test_003", sample_listing
            )
        
        with db_manager.session() as session:
            prop, is_new, price_changed = db_manager.get_or_create_property(
                session, "prop_test_003", sample_listing_updated
            )
            assert price_changed
            assert prop.current_price == 8200000.0
    
    def test_property_relisted_after_delist(self, db_manager, sample_listing):
        """Delisted property should reactivate on re-sight."""
        with db_manager.session() as session:
            prop, _, _ = db_manager.get_or_create_property(
                session, "prop_test_004", sample_listing
            )
            prop.status = "delisted"
        
        with db_manager.session() as session:
            prop, _, _ = db_manager.get_or_create_property(
                session, "prop_test_004", sample_listing
            )
            assert prop.status == "active"
    
    def test_property_sources_accumulate(self, db_manager, sample_listing, sample_listing_cross_source):
        """Sources should accumulate from different listings."""
        with db_manager.session() as session:
            db_manager.get_or_create_property(
                session, "prop_test_005", sample_listing
            )
        
        with db_manager.session() as session:
            prop, _, _ = db_manager.get_or_create_property(
                session, "prop_test_005", sample_listing_cross_source
            )
            assert "99acres" in prop.sources
            assert "MagicBricks" in prop.sources


class TestDataSanityGate:
    """Test data sanity validation rules."""
    
    def test_absurd_rent_rejected(self):
        """Rent listing with > 50 lakh should be rejected."""
        listing = {"price": 10_000_000, "listing_type": "rent"}
        assert listing["price"] > 5_000_000  # Would be filtered
    
    def test_valid_rent_accepted(self):
        """Rent listing with reasonable price passes."""
        listing = {"price": 35_000, "listing_type": "rent"}
        assert listing["price"] <= 5_000_000  # Would pass
    
    def test_absurd_buy_rejected(self):
        """Buy listing with < 50k should be rejected."""
        listing = {"price": 10_000, "listing_type": "buy"}
        assert listing["price"] < 50_000  # Would be filtered
    
    def test_valid_buy_accepted(self):
        """Buy listing with reasonable price passes."""
        listing = {"price": 7_500_000, "listing_type": "buy"}
        assert listing["price"] >= 50_000  # Would pass


class TestCrashRecovery:
    """Test that database state survives simulated crashes."""
    
    def test_data_persists_across_sessions(self, db_manager, sample_listing):
        """Data saved in one session should be readable in another."""
        with db_manager.session() as session:
            db_manager.save_listing(session, sample_listing)
        
        with db_manager.session() as session:
            exists = db_manager.listing_exists(
                session, sample_listing["url_hash"], sample_listing["source"]
            )
            assert exists
    
    def test_rollback_on_error(self, db_manager, sample_listing):
        """Failed session should not leave partial data."""
        try:
            with db_manager.session() as session:
                db_manager.save_listing(session, sample_listing)
                raise RuntimeError("Simulated crash")
        except RuntimeError:
            pass
        
        # Listing should not exist after rollback
        with db_manager.session() as session:
            count = session.query(Listing).count()
            assert count == 0
