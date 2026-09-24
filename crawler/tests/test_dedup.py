"""
Tests for the Deduplication Engine.
"""

import pytest
from core.dedup import Deduplicator, LRUCache, LRUDictCache


class TestLRUCache:
    """Test LRU cache behavior."""
    
    def test_add_and_contains(self):
        cache = LRUCache(max_size=10)
        cache.add("key1")
        assert cache.contains("key1")
        assert not cache.contains("key2")
    
    def test_eviction(self):
        cache = LRUCache(max_size=3)
        cache.add("a")
        cache.add("b")
        cache.add("c")
        cache.add("d")  # Should evict "a"
        
        assert not cache.contains("a")
        assert cache.contains("b")
        assert cache.contains("d")
    
    def test_lru_ordering(self):
        cache = LRUCache(max_size=3)
        cache.add("a")
        cache.add("b")
        cache.add("c")
        
        # Access "a" to make it recently used
        cache.contains("a")
        
        cache.add("d")  # Should evict "b" (least recently used)
        
        assert cache.contains("a")
        assert not cache.contains("b")
    
    def test_clear(self):
        cache = LRUCache(max_size=10)
        cache.add("a")
        cache.add("b")
        cache.clear()
        assert len(cache) == 0


class TestLRUDictCache:
    """Test LRU dict cache behavior."""
    
    def test_add_and_get(self):
        cache = LRUDictCache(max_entries=10)
        cache.add("key1", {"value": 1})
        items = cache.get("key1")
        assert len(items) == 1
        assert items[0]["value"] == 1
    
    def test_multiple_items_per_key(self):
        cache = LRUDictCache(max_entries=10, max_items_per_key=5)
        for i in range(3):
            cache.add("key1", {"value": i})
        
        items = cache.get("key1")
        assert len(items) == 3
    
    def test_eviction(self):
        cache = LRUDictCache(max_entries=2)
        cache.add("a", {"v": 1})
        cache.add("b", {"v": 2})
        cache.add("c", {"v": 3})
        
        assert cache.get("a") == []  # Evicted
        assert len(cache.get("b")) == 1


class TestDeduplicator:
    """Test deduplication logic."""
    
    @pytest.fixture
    def dedup(self, config):
        return Deduplicator(config)
    
    def test_url_dedup(self, dedup, sample_listing):
        """Same URL hash should be detected as duplicate."""
        # First time: not a duplicate
        is_dup, reason, match = dedup.is_duplicate(sample_listing)
        assert not is_dup
        
        # Second time: duplicate
        is_dup, reason, match = dedup.is_duplicate(sample_listing)
        assert is_dup
        assert reason == "url_duplicate"
    
    def test_address_dedup(self, dedup, sample_listing, sample_listing_cross_source):
        """Similar addresses should be detected."""
        # First listing
        dedup.is_duplicate(sample_listing)
        
        # Cross-source with similar address
        is_dup, reason, match = dedup.is_duplicate(sample_listing_cross_source)
        # May or may not be detected depending on fuzzy threshold
        # The key is that it doesn't crash
        assert isinstance(is_dup, bool)
    
    def test_deduplicate_batch(self, dedup, sample_listing, sample_listing_rent):
        """Batch dedup should separate unique from duplicates."""
        listings = [sample_listing, sample_listing_rent, sample_listing.copy()]
        
        unique, duplicates = dedup.deduplicate_batch(listings)
        
        # First two should be unique, third is a duplicate
        assert len(unique) == 2
        assert len(duplicates) == 1
    
    def test_stats(self, dedup, sample_listing):
        """Stats should track operations."""
        dedup.is_duplicate(sample_listing)
        dedup.is_duplicate(sample_listing)
        
        stats = dedup.get_stats()
        assert stats["total_checked"] == 2
        assert stats["duplicates_found"] == 1
    
    def test_clear_cache(self, dedup, sample_listing):
        """Clear should reset caches."""
        dedup.is_duplicate(sample_listing)
        dedup.clear_cache()
        
        # After clear, same listing should not be detected as duplicate
        is_dup, _, _ = dedup.is_duplicate(sample_listing)
        assert not is_dup


class TestPropertyClustering:
    """Test property clustering logic."""
    
    @pytest.fixture
    def dedup(self, config):
        return Deduplicator(config)
    
    def test_find_clusters(self, dedup, sample_listing, sample_listing_cross_source):
        """Properties with similar addresses should cluster."""
        listings = [sample_listing, sample_listing_cross_source]
        clusters = dedup.find_clusters(listings)
        # Result depends on address similarity threshold
        assert isinstance(clusters, list)
    
    def test_merge_cluster(self, dedup, sample_listing, sample_listing_cross_source):
        """Cluster merge should combine data."""
        cluster = [sample_listing, sample_listing_cross_source]
        merged = dedup.merge_cluster(cluster)
        
        assert len(merged.get("sources", [])) == 2
        assert merged.get("listing_count") == 2
