"""
Tests for site configuration loading and validation.
"""

import pytest
from pathlib import Path
from core.utils import ConfigLoader


TARGET_SITES = ["99acres", "magicbricks", "nobroker", "housing"]


class TestSiteConfigs:
    """Validate all 4 target site configurations."""
    
    @pytest.fixture
    def loader(self):
        return ConfigLoader()
    
    @pytest.mark.parametrize("site_name", TARGET_SITES)
    def test_config_loads(self, loader, site_name):
        """Each site config should load without errors."""
        config = loader.load_site_config(site_name)
        assert config is not None
        assert "site" in config
        assert "selectors" in config
    
    @pytest.mark.parametrize("site_name", TARGET_SITES)
    def test_site_enabled(self, loader, site_name):
        """Each target site should be enabled."""
        config = loader.load_site_config(site_name)
        assert config["site"]["enabled"] is True
    
    @pytest.mark.parametrize("site_name", TARGET_SITES)
    def test_has_cities(self, loader, site_name):
        """Each site should have at least 5 cities."""
        config = loader.load_site_config(site_name)
        cities = config.get("cities", [])
        assert len(cities) >= 5, f"{site_name} has only {len(cities)} cities"
    
    @pytest.mark.parametrize("site_name", TARGET_SITES)
    def test_has_listing_types(self, loader, site_name):
        """Each site should support buy and rent."""
        config = loader.load_site_config(site_name)
        listing_types = config.get("listing_types", [])
        assert "buy" in listing_types
        assert "rent" in listing_types
    
    @pytest.mark.parametrize("site_name", TARGET_SITES)
    def test_cities_have_url_patterns(self, loader, site_name):
        """Each city should have url_patterns for buy and rent."""
        config = loader.load_site_config(site_name)
        for city in config.get("cities", []):
            if not city.get("enabled", True):
                continue
            
            url_patterns = city.get("url_patterns", {})
            # At least buy pattern should exist
            assert "buy" in url_patterns or city.get("url_pattern"), \
                f"{site_name}/{city['name']}: missing url_patterns"
    
    @pytest.mark.parametrize("site_name", TARGET_SITES)
    def test_has_listing_card_selector(self, loader, site_name):
        """Each site must have listing_card selectors."""
        config = loader.load_site_config(site_name)
        selectors = config.get("selectors", {})
        assert "listing_card" in selectors
        assert len(selectors["listing_card"]) > 0
    
    @pytest.mark.parametrize("site_name", TARGET_SITES)
    def test_has_price_selector(self, loader, site_name):
        """Each site must have price selectors."""
        config = loader.load_site_config(site_name)
        selectors = config.get("selectors", {})
        assert "price" in selectors
        assert len(selectors["price"]) > 0
    
    @pytest.mark.parametrize("site_name", TARGET_SITES)
    def test_has_address_selector(self, loader, site_name):
        """Each site must have address selectors."""
        config = loader.load_site_config(site_name)
        selectors = config.get("selectors", {})
        assert "address" in selectors
        assert len(selectors["address"]) > 0
    
    @pytest.mark.parametrize("site_name", TARGET_SITES)
    def test_has_detail_link_selector(self, loader, site_name):
        """Each site must have detail_link selectors."""
        config = loader.load_site_config(site_name)
        selectors = config.get("selectors", {})
        assert "detail_link" in selectors
        assert len(selectors["detail_link"]) > 0
    
    @pytest.mark.parametrize("site_name", TARGET_SITES)
    def test_has_beds_selector(self, loader, site_name):
        """Each site must have beds selectors."""
        config = loader.load_site_config(site_name)
        selectors = config.get("selectors", {})
        assert "beds" in selectors
        assert len(selectors["beds"]) > 0
    
    @pytest.mark.parametrize("site_name", TARGET_SITES)
    def test_has_area_selector(self, loader, site_name):
        """Each site must have area selectors."""
        config = loader.load_site_config(site_name)
        selectors = config.get("selectors", {})
        assert "area" in selectors
        assert len(selectors["area"]) > 0
    
    @pytest.mark.parametrize("site_name", TARGET_SITES)
    def test_has_locality_selector(self, loader, site_name):
        """Each site must have locality selectors for yield."""
        config = loader.load_site_config(site_name)
        selectors = config.get("selectors", {})
        assert "locality" in selectors, f"{site_name} missing locality selector"
        assert len(selectors["locality"]) > 0
    
    @pytest.mark.parametrize("site_name", TARGET_SITES)
    def test_has_bathrooms_selector(self, loader, site_name):
        """Each site must have bathrooms selectors for yield."""
        config = loader.load_site_config(site_name)
        selectors = config.get("selectors", {})
        assert "bathrooms" in selectors, f"{site_name} missing bathrooms selector"
    
    @pytest.mark.parametrize("site_name", TARGET_SITES)
    def test_has_property_type_selector(self, loader, site_name):
        """Each site must have property_type selectors for yield."""
        config = loader.load_site_config(site_name)
        selectors = config.get("selectors", {})
        assert "property_type" in selectors, f"{site_name} missing property_type selector"
    
    @pytest.mark.parametrize("site_name", TARGET_SITES)
    def test_selector_count_minimum(self, loader, site_name):
        """Each site should have multiple fallback selectors per field."""
        config = loader.load_site_config(site_name)
        selectors = config.get("selectors", {})
        critical_fields = ["price", "address", "detail_link", "listing_card"]
        for field in critical_fields:
            assert len(selectors.get(field, [])) >= 2, \
                f"{site_name}/{field}: needs at least 2 fallback selectors"
    
    @pytest.mark.parametrize("site_name", TARGET_SITES)
    def test_has_pagination(self, loader, site_name):
        """Each site must have pagination configuration."""
        config = loader.load_site_config(site_name)
        pagination = config.get("pagination", {})
        assert "type" in pagination
        assert pagination["type"] in ["url_param", "infinite_scroll", "click"]
    
    @pytest.mark.parametrize("site_name", TARGET_SITES)
    def test_rate_limits_configured(self, loader, site_name):
        """Each site should have rate limit settings."""
        config = loader.load_site_config(site_name)
        site_config = config["site"]
        assert "min_delay_seconds" in site_config
        assert site_config["min_delay_seconds"] >= 5
    
    def test_no_deprecated_sites(self, loader):
        """Deprecated sites should not exist."""
        deprecated = [
            "commonfloor", "indiaproperty", "makaan",
            "proptiger", "quikr", "roofandfloor", "squareyards"
        ]
        available = loader.list_available_sites()
        for site in deprecated:
            assert site not in available, f"Deprecated site {site} still exists"
    
    def test_only_target_sites_exist(self, loader):
        """Only the 4 target sites should exist."""
        available = set(loader.list_available_sites())
        expected = set(TARGET_SITES)
        assert available == expected, f"Unexpected sites: {available - expected}"
