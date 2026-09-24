"""
Shared test fixtures for RealSlate test suite.
"""

import os
import sys
import pytest
from pathlib import Path
from datetime import datetime

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Use in-memory SQLite for tests
os.environ["DB_TYPE"] = "sqlite"
os.environ["DB_PATH"] = ":memory:"
os.environ["ENVIRONMENT"] = "test"
os.environ["PROXY_ENABLED"] = "false"
os.environ["TELEGRAM_ENABLED"] = "false"
os.environ["BROWSER_HEADLESS"] = "true"


@pytest.fixture
def sample_listing():
    """Return a sample listing dict."""
    return {
        "source": "99acres",
        "city": "Mumbai",
        "listing_type": "buy",
        "price": 7500000.0,
        "address": "Lodha Palava City, Dombivli East, Mumbai",
        "beds": 2,
        "bathrooms": 2,
        "area": 1050.0,
        "detail_link": "https://www.99acres.com/property/123456",
        "url_hash": "abc123def456",
        "address_hash": "xyz789",
        "property_uid": "uid_test_001",
        "property_type": "apartment",
        "builder_name": "Lodha Group",
        "description": "2 BHK Apartment in Dombivli East",
        "locality": "Dombivli East",
        "scraped_at": datetime.utcnow().isoformat(),
        "completeness_score": 0.85,
    }


@pytest.fixture
def sample_listing_rent():
    """Return a sample rent listing dict."""
    return {
        "source": "NoBroker",
        "city": "Bangalore",
        "listing_type": "rent",
        "price": 25000.0,
        "address": "Prestige Lakeside Habitat, Whitefield, Bangalore",
        "beds": 3,
        "bathrooms": 2,
        "area": 1400.0,
        "detail_link": "https://www.nobroker.in/property/rent/456789",
        "url_hash": "rent_hash_001",
        "address_hash": "rent_addr_001",
        "property_uid": "uid_rent_001",
        "property_type": "apartment",
        "locality": "Whitefield",
        "scraped_at": datetime.utcnow().isoformat(),
        "completeness_score": 0.80,
    }


@pytest.fixture
def sample_listing_updated(sample_listing):
    """Return a listing with a price change."""
    updated = sample_listing.copy()
    updated["price"] = 8200000.0  # Price increased
    return updated


@pytest.fixture
def sample_listing_cross_source(sample_listing):
    """Same property listed on MagicBricks."""
    cross = sample_listing.copy()
    cross["source"] = "MagicBricks"
    cross["detail_link"] = "https://www.magicbricks.com/property/789012"
    cross["url_hash"] = "mb_hash_001"
    cross["address"] = "Lodha Palava, Dombivali East, Mumbai"  # Slight variation
    cross["price"] = 7600000.0  # Slightly different price
    return cross


@pytest.fixture
def db_manager():
    """Create an in-memory database manager for testing."""
    from core.db import DatabaseManager, Base
    
    # Reset singleton for test isolation
    DatabaseManager._instance = None
    
    config = {
        "database": {
            "type": "sqlite",
            "path": ":memory:",
            "echo": False,
        }
    }
    
    mgr = DatabaseManager(config)
    mgr.initialize()
    mgr.create_tables()
    
    yield mgr
    
    mgr.close()
    DatabaseManager._instance = None


@pytest.fixture
def config():
    """Return test configuration."""
    return {
        "database": {
            "type": "sqlite",
            "path": ":memory:",
            "echo": False,
        },
        "deduplication": {
            "address_similarity_threshold": 0.85,
            "geo_clustering_radius_meters": 50,
            "lookback_days": 30,
            "max_url_cache_size": 10000,
            "max_address_cache_size": 5000,
        },
        "validation": {
            "min_price": 100000,
            "max_price": 10000000000,
            "min_area": 50,
            "max_area": 1000000,
            "required_fields": ["price", "address"],
            "min_completeness_score": 0.3,
        },
        "crawling": {
            "min_delay_seconds": 1,
            "max_delay_seconds": 2,
        },
    }
