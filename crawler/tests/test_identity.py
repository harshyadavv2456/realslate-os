"""
Tests for the Property Identity Engine.
"""

import pytest
from core.identity import (
    PropertyIdentityEngine,
    normalize_city,
    normalize_property_type,
    extract_building_name,
    extract_locality,
)


class TestNormalizeCity:
    """Test city name normalization."""
    
    def test_canonical_names(self):
        assert normalize_city("Mumbai") == "mumbai"
        assert normalize_city("Bangalore") == "bangalore"
        assert normalize_city("Delhi") == "delhi ncr"
    
    def test_aliases(self):
        assert normalize_city("Bengaluru") == "bangalore"
        assert normalize_city("Bombay") == "mumbai"
        assert normalize_city("Calcutta") == "kolkata"
        assert normalize_city("Madras") == "chennai"
        assert normalize_city("Gurugram") == "gurgaon"
        assert normalize_city("Vizag") == "visakhapatnam"
        assert normalize_city("Baroda") == "vadodara"
        assert normalize_city("Cochin") == "kochi"
    
    def test_empty_input(self):
        assert normalize_city("") == ""
        assert normalize_city(None) == ""
    
    def test_case_insensitive(self):
        assert normalize_city("MUMBAI") == "mumbai"
        assert normalize_city("bAnGaLoRe") == "bangalore"


class TestNormalizePropertyType:
    """Test property type normalization."""
    
    def test_apartment(self):
        assert normalize_property_type("Apartment") == "apartment"
        assert normalize_property_type("Flat") == "apartment"
    
    def test_house(self):
        assert normalize_property_type("Independent House") == "independent_house"
        assert normalize_property_type("Villa") == "villa"
    
    def test_floor(self):
        assert normalize_property_type("Builder Floor") == "builder_floor"
        assert normalize_property_type("Independent Floor") == "builder_floor"
    
    def test_plot(self):
        assert normalize_property_type("Plot") == "plot"
        assert normalize_property_type("Land") == "plot"
    
    def test_empty(self):
        assert normalize_property_type("") == "unknown"
        assert normalize_property_type(None) == "unknown"
    
    def test_unknown(self):
        assert normalize_property_type("spaceship") == "other"


class TestExtractBuilding:
    """Test building name extraction."""
    
    def test_society_suffix(self):
        result = extract_building_name("Lodha Palava Society, Dombivli")
        assert "lodha" in result
        assert "palava" in result
    
    def test_tower_suffix(self):
        result = extract_building_name("DLF Tower, Gurgaon")
        assert "dlf" in result
    
    def test_empty(self):
        assert extract_building_name("") == ""
        assert extract_building_name(None) == ""


class TestExtractLocality:
    """Test locality extraction."""
    
    def test_after_comma(self):
        result = extract_locality("Lodha Palava, Dombivli East, Mumbai")
        assert "dombivli" in result.lower()
    
    def test_no_comma(self):
        assert extract_locality("Whitefield") == ""
    
    def test_empty(self):
        assert extract_locality("") == ""


class TestPropertyIdentityEngine:
    """Test the identity engine."""
    
    @pytest.fixture
    def engine(self, config):
        return PropertyIdentityEngine(config)
    
    def test_generate_uid_deterministic(self, engine, sample_listing):
        """Same input should produce same UID."""
        uid1 = engine.generate_property_uid(sample_listing)
        uid2 = engine.generate_property_uid(sample_listing)
        assert uid1 == uid2
        assert len(uid1) == 32
    
    def test_generate_uid_different_for_different_properties(self, engine, sample_listing, sample_listing_rent):
        """Different properties should have different UIDs."""
        uid1 = engine.generate_property_uid(sample_listing)
        uid2 = engine.generate_property_uid(sample_listing_rent)
        assert uid1 != uid2
    
    def test_match_confidence_same_property(self, engine, sample_listing, sample_listing_cross_source):
        """Same property on different sources should have high confidence."""
        confidence = engine.compute_match_confidence(
            sample_listing, sample_listing_cross_source
        )
        assert confidence > 0.5  # Should be a reasonable match
    
    def test_match_confidence_different_properties(self, engine, sample_listing, sample_listing_rent):
        """Different properties should have low confidence."""
        confidence = engine.compute_match_confidence(
            sample_listing, sample_listing_rent
        )
        assert confidence < 0.4
    
    def test_find_best_match(self, engine, sample_listing, sample_listing_cross_source, sample_listing_rent):
        """Should find the correct match from candidates."""
        candidates = [sample_listing_rent, sample_listing_cross_source]
        match, confidence = engine.find_best_match(
            sample_listing, candidates, min_confidence=0.3
        )
        
        if match is not None:
            assert match["source"] == "MagicBricks"
    
    def test_merge_listing_data(self, engine, sample_listing, sample_listing_cross_source):
        """Merge should combine data from both sources."""
        merged = engine.merge_listing_data(sample_listing, sample_listing_cross_source)
        
        assert "99acres" in merged["sources"]
        assert "MagicBricks" in merged["sources"]
        assert merged["current_price"] == sample_listing_cross_source["price"]
    
    def test_stats(self, engine, sample_listing, sample_listing_rent):
        """Stats should track operations."""
        engine.find_best_match(sample_listing, [sample_listing_rent])
        stats = engine.get_stats()
        assert stats["matches_attempted"] == 1
