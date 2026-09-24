"""
RealSlate Core - Cross-Source Property Identity Engine
Produces stable property_uid values by normalizing address + geo + building + locality.
Supports fuzzy matching with confidence scoring for cross-source deduplication.
"""

import hashlib
import re
from typing import Optional, List, Dict, Any, Tuple
from threading import Lock

from rapidfuzz import fuzz
from loguru import logger
from unidecode import unidecode

from .utils import get_config, normalize_address


# Standard city name mappings (aliases -> canonical)
CITY_ALIASES: Dict[str, str] = {
    "bengaluru": "bangalore",
    "bengalore": "bangalore",
    "mumbai": "mumbai",
    "bombay": "mumbai",
    "new delhi": "delhi ncr",
    "delhi": "delhi ncr",
    "delhi ncr": "delhi ncr",
    "gurgaon": "gurgaon",
    "gurugram": "gurgaon",
    "noida": "noida",
    "greater noida": "noida",
    "kolkata": "kolkata",
    "calcutta": "kolkata",
    "chennai": "chennai",
    "madras": "chennai",
    "hyderabad": "hyderabad",
    "secunderabad": "hyderabad",
    "pune": "pune",
    "puna": "pune",
    "ahmedabad": "ahmedabad",
    "amdavad": "ahmedabad",
    "thane": "thane",
    "navi mumbai": "navi mumbai",
    "ghaziabad": "ghaziabad",
    "lucknow": "lucknow",
    "jaipur": "jaipur",
    "chandigarh": "chandigarh",
    "indore": "indore",
    "bhopal": "bhopal",
    "nagpur": "nagpur",
    "coimbatore": "coimbatore",
    "kochi": "kochi",
    "cochin": "kochi",
    "visakhapatnam": "visakhapatnam",
    "vizag": "visakhapatnam",
    "vadodara": "vadodara",
    "baroda": "vadodara",
    "surat": "surat",
    "mysore": "mysore",
    "mysuru": "mysore",
}

# Property type normalization
PROPERTY_TYPE_MAP: Dict[str, str] = {
    "apartment": "apartment",
    "flat": "apartment",
    "builder floor": "builder_floor",
    "independent floor": "builder_floor",
    "independent house": "independent_house",
    "villa": "villa",
    "house": "independent_house",
    "plot": "plot",
    "land": "plot",
    "penthouse": "penthouse",
    "studio": "studio",
    "1 rk": "studio",
    "pg": "pg",
    "farm house": "farmhouse",
    "farmhouse": "farmhouse",
}


def normalize_city(city: str) -> str:
    """Normalize city name to canonical form."""
    if not city:
        return ""
    cleaned = unidecode(city).lower().strip()
    cleaned = re.sub(r'[^\w\s]', '', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return CITY_ALIASES.get(cleaned, cleaned)


def normalize_property_type(ptype: str) -> str:
    """Normalize property type to enum value."""
    if not ptype:
        return "unknown"
    cleaned = ptype.lower().strip()
    for pattern, canonical in PROPERTY_TYPE_MAP.items():
        if pattern in cleaned:
            return canonical
    return "other"


def extract_building_name(address: str) -> str:
    """Extract probable building/society name from address."""
    if not address:
        return ""
    
    addr = unidecode(address).lower().strip()
    
    # Common patterns: "XYZ Society, Locality" or "ABC Towers, Area"
    suffixes = [
        "society", "towers", "tower", "heights", "residency",
        "apartments", "apartment", "complex", "enclave", "estate",
        "gardens", "garden", "park", "plaza", "villa", "villas",
        "nagar", "vihar", "colony", "heritage", "palace", "court",
        "avenue", "residences", "homes", "house",
    ]
    
    # Try to extract building name (before first comma typically)
    parts = re.split(r'[,;|]', addr)
    if parts:
        first_part = parts[0].strip()
        for suffix in suffixes:
            if suffix in first_part:
                return re.sub(r'[^\w\s]', '', first_part).strip()
    
    return ""


def extract_locality(address: str) -> str:
    """Extract locality from address string."""
    if not address:
        return ""
    
    addr = unidecode(address).lower().strip()
    
    # Locality is usually after first comma
    parts = re.split(r'[,;|]', addr)
    if len(parts) >= 2:
        return re.sub(r'\s+', ' ', parts[1]).strip()
    
    return ""


class PropertyIdentityEngine:
    """
    Generates stable cross-source property identifiers.
    
    Identity is built from:
    - Normalized address
    - Building name
    - Locality
    - City (canonical)
    - Geo coordinates (when available)
    
    Produces:
    - property_uid: Stable hash for canonical identity
    - match_confidence: 0.0-1.0 score for cross-source matches
    """
    
    def __init__(self, config: Optional[dict] = None):
        self.config = config or get_config()
        dedup_config = self.config.get("deduplication", {})
        
        self.address_threshold = dedup_config.get(
            "address_similarity_threshold", 0.85
        )
        self.geo_radius_meters = dedup_config.get(
            "geo_clustering_radius_meters", 50
        )
        
        # Stats
        self._matches_attempted = 0
        self._matches_found = 0
        self._lock = Lock()
    
    def generate_property_uid(self, listing: Dict[str, Any]) -> str:
        """
        Generate a stable property UID from listing data.
        
        Uses a hierarchy of available data:
        1. URL hash (source-specific, most stable)
        2. Address + City hash (cross-source)
        3. Building + Locality + City hash (fallback)
        """
        address = listing.get("address", "")
        city = normalize_city(listing.get("city", ""))
        source = listing.get("source", "")
        
        # Normalize address for hashing
        norm_addr = normalize_address(address)
        building = extract_building_name(address)
        locality = listing.get("locality", "") or extract_locality(address)
        
        # Build identity string from best available data
        identity_parts = []
        
        if norm_addr:
            identity_parts.append(norm_addr)
        elif building and locality:
            identity_parts.append(building)
            identity_parts.append(locality)
        
        if city:
            identity_parts.append(city)
        
        # Add beds/area for disambiguation (same building, different unit)
        beds = listing.get("beds")
        area = listing.get("area")
        if beds is not None:
            identity_parts.append(f"beds_{beds}")
        if area is not None:
            # Round to nearest 10 sqft for tolerance
            identity_parts.append(f"area_{round(area / 10) * 10}")
        
        if not identity_parts:
            # Fallback to URL-based identity
            url = listing.get("detail_link", "") or listing.get("url", "")
            if url:
                identity_parts.append(url.lower().strip())
            else:
                # Last resort: use raw data hash
                identity_parts.append(str(listing.get("price", "")))
                identity_parts.append(source)
                identity_parts.append(str(listing.get("scraped_at", "")))
        
        combined = "|".join(str(p) for p in identity_parts if p)
        return hashlib.sha256(combined.encode()).hexdigest()[:32]
    
    def compute_match_confidence(
        self,
        listing_a: Dict[str, Any],
        listing_b: Dict[str, Any]
    ) -> float:
        """
        Compute confidence score that two listings refer to the same property.
        Returns 0.0 (no match) to 1.0 (certain match).
        
        Scoring:
        - Address similarity: up to 0.40
        - Building name match: up to 0.15
        - Locality match: up to 0.10
        - Beds match: up to 0.10
        - Area proximity: up to 0.10
        - Price proximity: up to 0.10
        - City match: 0.05
        """
        score = 0.0
        
        # 1. Address similarity (max 0.40)
        addr_a = normalize_address(listing_a.get("address", ""))
        addr_b = normalize_address(listing_b.get("address", ""))
        
        if addr_a and addr_b:
            addr_sim = fuzz.ratio(addr_a, addr_b) / 100.0
            score += addr_sim * 0.40
        
        # 2. Building name match (max 0.15)
        bldg_a = extract_building_name(listing_a.get("address", ""))
        bldg_b = extract_building_name(listing_b.get("address", ""))
        
        if bldg_a and bldg_b:
            bldg_sim = fuzz.ratio(bldg_a, bldg_b) / 100.0
            score += bldg_sim * 0.15
        
        # 3. Locality match (max 0.10)
        loc_a = listing_a.get("locality", "") or extract_locality(listing_a.get("address", ""))
        loc_b = listing_b.get("locality", "") or extract_locality(listing_b.get("address", ""))
        
        if loc_a and loc_b:
            loc_sim = fuzz.ratio(loc_a.lower(), loc_b.lower()) / 100.0
            score += loc_sim * 0.10
        
        # 4. Beds match (max 0.10)
        beds_a = listing_a.get("beds")
        beds_b = listing_b.get("beds")
        
        if beds_a is not None and beds_b is not None:
            if beds_a == beds_b:
                score += 0.10
        
        # 5. Area proximity (max 0.10)
        area_a = listing_a.get("area")
        area_b = listing_b.get("area")
        
        if area_a and area_b and area_a > 0 and area_b > 0:
            area_diff = abs(area_a - area_b) / max(area_a, area_b)
            if area_diff <= 0.05:
                score += 0.10
            elif area_diff <= 0.10:
                score += 0.05
        
        # 6. Price proximity (max 0.10)
        price_a = listing_a.get("price")
        price_b = listing_b.get("price")
        
        if price_a and price_b and price_a > 0 and price_b > 0:
            price_diff = abs(price_a - price_b) / max(price_a, price_b)
            if price_diff <= 0.05:
                score += 0.10
            elif price_diff <= 0.15:
                score += 0.05
        
        # 7. City match (max 0.05)
        city_a = normalize_city(listing_a.get("city", ""))
        city_b = normalize_city(listing_b.get("city", ""))
        
        if city_a and city_b and city_a == city_b:
            score += 0.05
        
        return min(1.0, score)
    
    def find_best_match(
        self,
        listing: Dict[str, Any],
        candidates: List[Dict[str, Any]],
        min_confidence: float = 0.60
    ) -> Tuple[Optional[Dict[str, Any]], float]:
        """
        Find the best matching property from candidates.
        Returns (best_match, confidence) or (None, 0.0).
        """
        with self._lock:
            self._matches_attempted += 1
        
        best_match = None
        best_confidence = 0.0
        
        for candidate in candidates:
            confidence = self.compute_match_confidence(listing, candidate)
            
            if confidence > best_confidence and confidence >= min_confidence:
                best_confidence = confidence
                best_match = candidate
        
        if best_match:
            with self._lock:
                self._matches_found += 1
        
        return best_match, best_confidence
    
    def merge_listing_data(
        self,
        existing: Dict[str, Any],
        incoming: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Merge incoming listing data into existing property record.
        Prefers non-null and more complete values.
        """
        merged = existing.copy()
        
        # Fields to merge (prefer incoming if existing is empty)
        merge_fields = [
            "address", "locality", "beds", "bathrooms", "area",
            "property_type", "builder_name", "description",
        ]
        
        for field in merge_fields:
            existing_val = existing.get(field)
            incoming_val = incoming.get(field)
            
            if incoming_val and not existing_val:
                merged[field] = incoming_val
        
        # Always update price to latest
        if incoming.get("price"):
            merged["current_price"] = incoming["price"]
        
        # Track sources — capture both "sources" list and singular "source" field
        sources = set(existing.get("sources", []))
        existing_source = existing.get("source")
        if existing_source:
            sources.add(existing_source)
        incoming_source = incoming.get("source")
        if incoming_source:
            sources.add(incoming_source)
        merged["sources"] = list(sources)
        
        # Track source URLs
        source_urls = dict(existing.get("source_urls", {}))
        if incoming_source and incoming.get("detail_link"):
            source_urls[incoming_source] = incoming["detail_link"]
        merged["source_urls"] = source_urls
        
        return merged
    
    def get_stats(self) -> Dict[str, Any]:
        """Get identity engine statistics."""
        return {
            "matches_attempted": self._matches_attempted,
            "matches_found": self._matches_found,
            "match_rate": (
                self._matches_found / max(1, self._matches_attempted)
            ),
        }
    
    def reset_stats(self) -> None:
        """Reset statistics."""
        with self._lock:
            self._matches_attempted = 0
            self._matches_found = 0


# Module-level singleton
_identity_engine: Optional[PropertyIdentityEngine] = None


def get_identity_engine(config: Optional[dict] = None) -> PropertyIdentityEngine:
    """Get or create the singleton PropertyIdentityEngine."""
    global _identity_engine
    if _identity_engine is None:
        _identity_engine = PropertyIdentityEngine(config)
    return _identity_engine
