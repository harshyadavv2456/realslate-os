"""
RealSlate Core - Deduplication Engine
Identifies and clusters duplicate property listings.
"""

import hashlib
import sys
from typing import Dict, Any, List, Optional, Set, Tuple
from datetime import datetime, timedelta
from collections import OrderedDict
from threading import Lock

from rapidfuzz import fuzz
from loguru import logger

from .utils import (
    get_config,
    generate_url_hash,
    generate_address_hash,
    normalize_address,
)


class LRUCache:
    """Thread-safe LRU cache with max size enforcement."""
    
    def __init__(self, max_size: int = 100000):
        self.max_size = max_size
        self._cache: OrderedDict = OrderedDict()
        self._lock = Lock()
    
    def add(self, key: str) -> None:
        """Add item to cache."""
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            else:
                self._cache[key] = True
                self._evict_if_needed()
    
    def contains(self, key: str) -> bool:
        """Check if item exists in cache."""
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return True
            return False
    
    def _evict_if_needed(self) -> None:
        """Evict oldest items if over max size."""
        while len(self._cache) > self.max_size:
            self._cache.popitem(last=False)
    
    def clear(self) -> None:
        """Clear the cache."""
        with self._lock:
            self._cache.clear()
    
    def __len__(self) -> int:
        return len(self._cache)
    
    def memory_size(self) -> int:
        """Estimate memory usage in bytes."""
        return sys.getsizeof(self._cache) + sum(
            sys.getsizeof(k) for k in self._cache.keys()
        )


class LRUDictCache:
    """Thread-safe LRU cache for dict values with max size enforcement."""
    
    def __init__(self, max_entries: int = 50000, max_items_per_key: int = 100):
        self.max_entries = max_entries
        self.max_items_per_key = max_items_per_key
        self._cache: OrderedDict = OrderedDict()
        self._total_items = 0
        self._lock = Lock()
    
    def add(self, key: str, item: Dict) -> None:
        """Add item to a key's list."""
        with self._lock:
            if key not in self._cache:
                self._cache[key] = []
            
            self._cache[key].append(item)
            self._total_items += 1
            
            # Trim items per key
            if len(self._cache[key]) > self.max_items_per_key:
                self._cache[key] = self._cache[key][-self.max_items_per_key:]
                self._total_items -= 1
            
            # Move to end (most recent)
            self._cache.move_to_end(key)
            
            # Evict oldest entries if over limit
            self._evict_if_needed()
    
    def get(self, key: str) -> List[Dict]:
        """Get items for a key."""
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
            return []
    
    def _evict_if_needed(self) -> None:
        """Evict oldest entries if over limit."""
        while len(self._cache) > self.max_entries:
            _, items = self._cache.popitem(last=False)
            self._total_items -= len(items)
    
    def clear(self) -> None:
        """Clear the cache."""
        with self._lock:
            self._cache.clear()
            self._total_items = 0
    
    def __len__(self) -> int:
        return len(self._cache)
    
    @property
    def total_items(self) -> int:
        return self._total_items
    
    def memory_size(self) -> int:
        """Estimate memory usage in bytes."""
        size = sys.getsizeof(self._cache)
        for key, items in self._cache.items():
            size += sys.getsizeof(key)
            size += sys.getsizeof(items)
            for item in items:
                size += sys.getsizeof(item)
        return size


class Deduplicator:
    """
    Production-grade deduplication with:
    - URL-based deduplication
    - Address similarity matching
    - Geo-clustering
    - Cross-source matching
    - LRU eviction for bounded memory
    """
    
    def __init__(self, config: Optional[dict] = None):
        self.config = config or get_config()
        self.dedup_config = self.config.get("deduplication", {})
        
        # Thresholds
        self.address_threshold = self.dedup_config.get(
            "address_similarity_threshold", 0.85
        )
        self.geo_radius = self.dedup_config.get(
            "geo_clustering_radius_meters", 50
        )
        
        # LRU Caches with size limits
        max_url_cache = self.dedup_config.get("max_url_cache_size", 100000)
        max_address_cache = self.dedup_config.get("max_address_cache_size", 50000)
        
        self._url_cache = LRUCache(max_size=max_url_cache)
        self._address_cache = LRUDictCache(
            max_entries=max_address_cache,
            max_items_per_key=100
        )
        
        # Stats
        self._total_checked = 0
        self._duplicates_found = 0
        self._url_duplicates = 0
        self._address_duplicates = 0
        self._lock = Lock()
    
    def is_duplicate(
        self,
        listing: Dict[str, Any],
        existing_listings: Optional[List[Dict[str, Any]]] = None
    ) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
        """
        Check if listing is a duplicate.
        Returns (is_duplicate, reason, matching_listing).
        """
        with self._lock:
            self._total_checked += 1
        
        # URL-based check (fastest)
        url_hash = listing.get("url_hash")
        if url_hash and self._check_url_duplicate(url_hash):
            with self._lock:
                self._duplicates_found += 1
                self._url_duplicates += 1
            return True, "url_duplicate", None
        
        # Address-based check
        address = listing.get("address")
        if address:
            is_dup, match = self._check_address_duplicate(listing, existing_listings)
            if is_dup:
                with self._lock:
                    self._duplicates_found += 1
                    self._address_duplicates += 1
                return True, "address_duplicate", match
        
        # Add to cache (LRU caches handle eviction automatically)
        if url_hash:
            self._url_cache.add(url_hash)
        
        if address:
            address_key = self._get_address_key(address)
            self._address_cache.add(address_key, {
                "listing": {
                    # Store only essential fields to save memory
                    "property_uid": listing.get("property_uid"),
                    "address": listing.get("address"),
                    "price": listing.get("price"),
                    "area": listing.get("area"),
                    "beds": listing.get("beds"),
                },
                "normalized_address": normalize_address(address),
            })
        
        return False, None, None
    
    def _check_url_duplicate(self, url_hash: str) -> bool:
        """Check if URL already seen."""
        return self._url_cache.contains(url_hash)
    
    def _check_address_duplicate(
        self,
        listing: Dict[str, Any],
        existing_listings: Optional[List[Dict[str, Any]]] = None
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """Check for address-based duplicates."""
        address = listing.get("address", "")
        normalized = normalize_address(address)
        
        if not normalized:
            return False, None
        
        address_key = self._get_address_key(address)
        
        # Check LRU cache
        cached_entries = self._address_cache.get(address_key)
        
        for entry in cached_entries:
            cached_normalized = entry.get("normalized_address", "")
            
            # Fuzzy match
            similarity = fuzz.ratio(normalized, cached_normalized) / 100.0
            
            if similarity >= self.address_threshold:
                cached_listing = entry.get("listing", {})
                
                # Additional checks for same property
                if self._additional_match_checks(listing, cached_listing):
                    return True, cached_listing
        
        # Check against provided existing listings
        if existing_listings:
            for existing in existing_listings:
                existing_address = existing.get("address", "")
                existing_normalized = normalize_address(existing_address)
                
                if existing_normalized:
                    similarity = fuzz.ratio(normalized, existing_normalized) / 100.0
                    
                    if similarity >= self.address_threshold:
                        if self._additional_match_checks(listing, existing):
                            return True, existing
        
        return False, None
    
    def _additional_match_checks(
        self,
        listing1: Dict[str, Any],
        listing2: Dict[str, Any]
    ) -> bool:
        """
        Additional checks to confirm two listings are the same property.
        """
        # Price similarity (within 10%)
        price1 = listing1.get("price")
        price2 = listing2.get("price")
        
        if price1 and price2:
            if price1 > 0 and price2 > 0:
                price_diff = abs(price1 - price2) / max(price1, price2)
                if price_diff > 0.10:
                    return False
        
        # Area similarity (within 5%)
        area1 = listing1.get("area")
        area2 = listing2.get("area")
        
        if area1 and area2:
            if area1 > 0 and area2 > 0:
                area_diff = abs(area1 - area2) / max(area1, area2)
                if area_diff > 0.05:
                    return False
        
        # Bedroom match
        beds1 = listing1.get("beds")
        beds2 = listing2.get("beds")
        
        if beds1 is not None and beds2 is not None:
            if beds1 != beds2:
                return False
        
        return True
    
    def _get_address_key(self, address: str) -> str:
        """
        Get a cache key for address.
        Uses first few characters for bucketing.
        """
        normalized = normalize_address(address)
        if not normalized:
            return ""
        
        # Use first 10 chars as bucket key
        return normalized[:10]
    
    def deduplicate_batch(
        self,
        listings: List[Dict[str, Any]],
        mark_duplicates: bool = True
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Deduplicate a batch of listings.
        Returns (unique_listings, duplicate_listings).
        """
        unique = []
        duplicates = []
        
        for listing in listings:
            is_dup, reason, match = self.is_duplicate(listing, unique)
            
            if is_dup:
                if mark_duplicates:
                    listing["is_duplicate"] = True
                    listing["duplicate_reason"] = reason
                    listing["matched_with"] = match.get("property_uid") if match else None
                duplicates.append(listing)
            else:
                if mark_duplicates:
                    listing["is_duplicate"] = False
                unique.append(listing)
        
        logger.info(
            f"Deduplication: {len(unique)} unique, {len(duplicates)} duplicates "
            f"out of {len(listings)} total"
        )
        
        return unique, duplicates
    
    def find_clusters(
        self,
        listings: List[Dict[str, Any]]
    ) -> List[List[Dict[str, Any]]]:
        """
        Find clusters of potentially same properties.
        """
        clusters = []
        processed = set()
        
        for i, listing in enumerate(listings):
            if i in processed:
                continue
            
            cluster = [listing]
            processed.add(i)
            
            for j, other in enumerate(listings[i + 1:], start=i + 1):
                if j in processed:
                    continue
                
                is_match = self._additional_match_checks(listing, other)
                
                if is_match:
                    # Check address similarity
                    addr1 = normalize_address(listing.get("address", ""))
                    addr2 = normalize_address(other.get("address", ""))
                    
                    if addr1 and addr2:
                        similarity = fuzz.ratio(addr1, addr2) / 100.0
                        if similarity >= self.address_threshold * 0.9:  # Slightly relaxed
                            cluster.append(other)
                            processed.add(j)
            
            if len(cluster) > 1:
                clusters.append(cluster)
        
        return clusters
    
    def merge_cluster(
        self,
        cluster: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Merge a cluster of duplicate listings into a canonical property.
        """
        if not cluster:
            return {}
        
        # Sort by completeness score (descending)
        sorted_cluster = sorted(
            cluster,
            key=lambda x: x.get("completeness_score", 0),
            reverse=True
        )
        
        # Use best listing as base
        merged = sorted_cluster[0].copy()
        
        # Collect all sources
        sources = set()
        source_urls = {}
        
        for listing in cluster:
            source = listing.get("source")
            if source:
                sources.add(source)
                url = listing.get("detail_link")
                if url:
                    source_urls[source] = url
        
        merged["sources"] = list(sources)
        merged["source_urls"] = source_urls
        merged["listing_count"] = len(cluster)
        
        # Fill in missing fields from other listings
        for field in ["price", "address", "beds", "area", "description", "builder_name"]:
            if not merged.get(field):
                for listing in sorted_cluster[1:]:
                    if listing.get(field):
                        merged[field] = listing.get(field)
                        break
        
        return merged
    
    def load_cache_from_db(self, db_session, lookback_days: int = None) -> None:
        """
        Load recent listings into dedup cache from database.
        Uses batched loading to prevent memory spikes.
        """
        from .db import Listing
        
        lookback_days = lookback_days or self.dedup_config.get("lookback_days", 30)
        cutoff = datetime.utcnow() - timedelta(days=lookback_days)
        
        try:
            # Count total for logging
            total_count = db_session.query(Listing).filter(
                Listing.scraped_at > cutoff
            ).count()
            
            if total_count == 0:
                logger.info("No listings to load into dedup cache")
                return
            
            # Load in batches to prevent memory issues
            batch_size = 5000
            loaded = 0
            
            query = db_session.query(
                Listing.url_hash,
                Listing.address,
                Listing.property_uid,
                Listing.price,
                Listing.area,
                Listing.beds
            ).filter(
                Listing.scraped_at > cutoff
            ).yield_per(batch_size)
            
            for row in query:
                if row.url_hash:
                    self._url_cache.add(row.url_hash)
                
                if row.address:
                    address_key = self._get_address_key(row.address)
                    self._address_cache.add(address_key, {
                        "listing": {
                            "property_uid": row.property_uid,
                            "address": row.address,
                            "price": row.price,
                            "area": row.area,
                            "beds": row.beds,
                        },
                        "normalized_address": normalize_address(row.address),
                    })
                
                loaded += 1
                
                if loaded % 10000 == 0:
                    logger.info(f"Loaded {loaded:,}/{total_count:,} listings into dedup cache")
            
            logger.info(
                f"Loaded {loaded:,} listings into dedup cache "
                f"(URLs: {len(self._url_cache):,}, Addresses: {len(self._address_cache):,})"
            )
            
        except Exception as e:
            logger.error(f"Failed to load dedup cache from DB: {e}")
    
    def clear_cache(self) -> None:
        """Clear deduplication caches."""
        self._url_cache.clear()
        self._address_cache.clear()
    
    def get_stats(self) -> Dict[str, Any]:
        """Get deduplication statistics with memory info."""
        return {
            "total_checked": self._total_checked,
            "duplicates_found": self._duplicates_found,
            "url_duplicates": self._url_duplicates,
            "address_duplicates": self._address_duplicates,
            "duplicate_rate": self._duplicates_found / max(1, self._total_checked),
            "cache_size": {
                "url_entries": len(self._url_cache),
                "address_keys": len(self._address_cache),
                "address_items": self._address_cache.total_items,
            },
            "memory_usage_bytes": {
                "url_cache": self._url_cache.memory_size(),
                "address_cache": self._address_cache.memory_size(),
            },
        }
    
    def reset_stats(self) -> None:
        """Reset deduplication statistics."""
        with self._lock:
            self._total_checked = 0
            self._duplicates_found = 0
            self._url_duplicates = 0
            self._address_duplicates = 0


class PropertyMatcher:
    """
    Match listings to canonical properties across sources.
    """
    
    def __init__(self, config: Optional[dict] = None):
        self.config = config or get_config()
        self.dedup_config = self.config.get("deduplication", {})
        
        self.address_threshold = self.dedup_config.get(
            "address_similarity_threshold", 0.85
        )
    
    def find_matching_property(
        self,
        listing: Dict[str, Any],
        properties: List[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """
        Find a matching canonical property for a listing.
        """
        listing_address = normalize_address(listing.get("address", ""))
        
        if not listing_address:
            return None
        
        best_match = None
        best_score = 0
        
        for prop in properties:
            prop_address = normalize_address(prop.get("canonical_address", ""))
            
            if not prop_address:
                continue
            
            # Address similarity
            similarity = fuzz.ratio(listing_address, prop_address) / 100.0
            
            if similarity < self.address_threshold:
                continue
            
            # Additional scoring
            score = similarity
            
            # Price proximity bonus
            listing_price = listing.get("price")
            prop_price = prop.get("current_price")
            
            if listing_price and prop_price:
                if prop_price > 0:
                    price_diff = abs(listing_price - prop_price) / prop_price
                    if price_diff < 0.1:
                        score += 0.1
            
            # Area proximity bonus
            listing_area = listing.get("area")
            prop_area = prop.get("current_area")
            
            if listing_area and prop_area:
                if prop_area > 0:
                    area_diff = abs(listing_area - prop_area) / prop_area
                    if area_diff < 0.05:
                        score += 0.05
            
            if score > best_score:
                best_score = score
                best_match = prop
        
        return best_match
    
    def match_batch(
        self,
        listings: List[Dict[str, Any]],
        properties: List[Dict[str, Any]]
    ) -> List[Tuple[Dict[str, Any], Optional[Dict[str, Any]]]]:
        """
        Match a batch of listings to properties.
        Returns list of (listing, matched_property) tuples.
        """
        results = []
        
        for listing in listings:
            matched = self.find_matching_property(listing, properties)
            results.append((listing, matched))
        
        return results
