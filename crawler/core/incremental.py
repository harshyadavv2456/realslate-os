"""
RealSlate Core - Incremental Crawl Engine
Implements change detection, last_seen tracking, event generation,
and skip-unchanged logic for efficient daily crawls.
"""

import hashlib
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple
from threading import Lock

from loguru import logger
from sqlalchemy.orm import Session
from sqlalchemy import and_

from .utils import get_config
from .db import DatabaseManager, Listing, Property, PropertyEvent, CrawlState
from .identity import normalize_city, normalize_property_type


class IncrementalResult:
    """Result of incremental processing for a single listing."""
    
    __slots__ = [
        "action", "listing_data", "existing_listing",
        "changes", "events",
    ]
    
    ACTION_NEW = "new"
    ACTION_UPDATED = "updated"
    ACTION_UNCHANGED = "unchanged"
    ACTION_RELISTED = "relisted"
    ACTION_SKIPPED = "skipped"
    
    def __init__(self, action: str, listing_data: Dict[str, Any]):
        self.action = action
        self.listing_data = listing_data
        self.existing_listing = None
        self.changes: List[Dict[str, Any]] = []
        self.events: List[Dict[str, Any]] = []


class IncrementalEngine:
    """
    Production-grade incremental crawl engine.
    
    Core logic:
        if listing is new:
            insert + create 'new_listing' event
        elif listing has changed:
            create new version + create change events
            update property record
        else:
            update last_seen timestamp only (skip full insert)
    
    Features:
    - Change detection on key fields (price, area, beds, status)
    - Event generation for price_change, data_update, relisted, delisted
    - Priority recrawl scheduling for active listings
    - Stale listing detection (mark inactive after N days)
    """
    
    # Fields checked for meaningful changes
    CHANGE_FIELDS = ["price", "beds", "bathrooms", "area", "address", "property_type"]
    
    def __init__(self, config: Optional[dict] = None):
        self.config = config or get_config()
        self.db_manager = DatabaseManager(self.config)
        
        # Incremental settings
        self._stale_days = 7  # Mark as inactive after 7 days unseen
        self._relisted_days = 7  # Consider relisted if unseen > 7 days
        
        # Stats for current run
        self._new_count = 0
        self._updated_count = 0
        self._unchanged_count = 0
        self._relisted_count = 0
        self._events_created = 0
        self._lock = Lock()
    
    def process_listing(
        self,
        session: Session,
        listing_data: Dict[str, Any]
    ) -> IncrementalResult:
        """
        Process a single listing incrementally.
        
        Returns an IncrementalResult indicating what action was taken.
        """
        url_hash = listing_data.get("url_hash")
        source = listing_data.get("source")
        
        if not url_hash or not source:
            return IncrementalResult(IncrementalResult.ACTION_SKIPPED, listing_data)
        
        # Normalize fields
        listing_data["city"] = normalize_city(listing_data.get("city", ""))
        if listing_data.get("property_type"):
            listing_data["property_type"] = normalize_property_type(
                listing_data["property_type"]
            )
        
        # Look up existing listing
        existing = self.db_manager.get_active_listing_for_incremental(
            session, url_hash, source
        )
        
        if existing is None:
            # New listing
            return self._handle_new_listing(session, listing_data)
        
        # Check if listing was previously inactive (relisted)
        if not existing.is_active:
            return self._handle_relisted(session, listing_data, existing)
        
        # Check for changes
        changes = self._detect_changes(existing, listing_data)
        
        if changes:
            return self._handle_updated(session, listing_data, existing, changes)
        else:
            return self._handle_unchanged(session, listing_data, existing)
    
    def _handle_new_listing(
        self,
        session: Session,
        listing_data: Dict[str, Any]
    ) -> IncrementalResult:
        """Handle a completely new listing."""
        result = IncrementalResult(IncrementalResult.ACTION_NEW, listing_data)
        
        # Create new_listing event
        result.events.append({
            "event_type": "new_listing",
            "new_value": str(listing_data.get("price", "")),
            "source": listing_data.get("source"),
            "metadata": {
                "city": listing_data.get("city"),
                "listing_type": listing_data.get("listing_type", "buy"),
                "beds": listing_data.get("beds"),
                "area": listing_data.get("area"),
            },
        })
        
        with self._lock:
            self._new_count += 1
            self._events_created += 1
        
        return result
    
    def _handle_relisted(
        self,
        session: Session,
        listing_data: Dict[str, Any],
        existing: Listing
    ) -> IncrementalResult:
        """Handle a listing that reappears after being inactive."""
        result = IncrementalResult(IncrementalResult.ACTION_RELISTED, listing_data)
        result.existing_listing = existing
        
        # Reactivate
        existing.is_active = True
        existing.last_seen = datetime.utcnow()
        
        # Create relisted event
        result.events.append({
            "event_type": "relisted",
            "old_value": str(existing.last_seen) if existing.last_seen else None,
            "new_value": datetime.utcnow().isoformat(),
            "source": listing_data.get("source"),
        })
        
        # Also check for price change while it was away
        old_price = existing.price
        new_price = listing_data.get("price")
        if old_price and new_price and abs(old_price - new_price) > 0.01:
            result.events.append({
                "event_type": "price_change",
                "old_value": str(old_price),
                "new_value": str(new_price),
                "value_numeric": new_price,
                "source": listing_data.get("source"),
            })
            result.changes.append({
                "field": "price",
                "old": old_price,
                "new": new_price,
            })
        
        with self._lock:
            self._relisted_count += 1
            self._events_created += len(result.events)
        
        return result
    
    def _handle_updated(
        self,
        session: Session,
        listing_data: Dict[str, Any],
        existing: Listing,
        changes: List[Dict[str, Any]]
    ) -> IncrementalResult:
        """Handle a listing with detected changes."""
        result = IncrementalResult(IncrementalResult.ACTION_UPDATED, listing_data)
        result.existing_listing = existing
        result.changes = changes
        
        # Update last_seen
        existing.last_seen = datetime.utcnow()
        
        # Generate events for each change
        for change in changes:
            field = change["field"]
            
            if field == "price":
                result.events.append({
                    "event_type": "price_change",
                    "old_value": str(change["old"]),
                    "new_value": str(change["new"]),
                    "value_numeric": change["new"] if isinstance(change["new"], (int, float)) else None,
                    "source": listing_data.get("source"),
                })
            else:
                result.events.append({
                    "event_type": "data_update",
                    "old_value": str(change["old"]) if change["old"] is not None else None,
                    "new_value": str(change["new"]) if change["new"] is not None else None,
                    "source": listing_data.get("source"),
                    "metadata": {"field": field},
                })
        
        with self._lock:
            self._updated_count += 1
            self._events_created += len(result.events)
        
        return result
    
    def _handle_unchanged(
        self,
        session: Session,
        listing_data: Dict[str, Any],
        existing: Listing
    ) -> IncrementalResult:
        """Handle an unchanged listing - only update last_seen."""
        result = IncrementalResult(IncrementalResult.ACTION_UNCHANGED, listing_data)
        result.existing_listing = existing
        
        # Only update the timestamp
        existing.last_seen = datetime.utcnow()
        
        with self._lock:
            self._unchanged_count += 1
        
        return result
    
    def _detect_changes(
        self,
        existing: Listing,
        incoming: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Detect meaningful changes between existing and incoming data.
        Returns list of change dicts: [{"field": ..., "old": ..., "new": ...}]
        """
        changes = []
        
        field_mapping = {
            "price": ("price", "price"),
            "beds": ("beds", "beds"),
            "bathrooms": ("bathrooms", "bathrooms"),
            "area": ("area", "area"),
            "address": ("address", "address"),
            "property_type": ("property_type", "property_type"),
        }
        
        for field_name, (db_field, data_field) in field_mapping.items():
            old_val = getattr(existing, db_field, None)
            new_val = incoming.get(data_field)
            
            if new_val is None:
                continue
            
            if old_val is None and new_val is not None:
                changes.append({"field": field_name, "old": old_val, "new": new_val})
                continue
            
            # Numeric comparison with tolerance
            if isinstance(old_val, (int, float)) and isinstance(new_val, (int, float)):
                if old_val == 0 and new_val == 0:
                    continue
                if old_val > 0:
                    pct_change = abs(old_val - new_val) / old_val
                    if pct_change > 0.001:  # 0.1% threshold
                        changes.append({"field": field_name, "old": old_val, "new": new_val})
            elif str(old_val).strip() != str(new_val).strip():
                changes.append({"field": field_name, "old": old_val, "new": new_val})
        
        return changes
    
    def process_batch(
        self,
        session: Session,
        listings: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Process a batch of listings incrementally.
        
        Returns summary dict with counts.
        """
        results = {
            "new": [],
            "updated": [],
            "unchanged": [],
            "relisted": [],
            "skipped": [],
            "events": [],
        }
        
        for listing_data in listings:
            try:
                result = self.process_listing(session, listing_data)
                
                if result.action == IncrementalResult.ACTION_NEW:
                    results["new"].append(result)
                elif result.action == IncrementalResult.ACTION_UPDATED:
                    results["updated"].append(result)
                elif result.action == IncrementalResult.ACTION_UNCHANGED:
                    results["unchanged"].append(result)
                elif result.action == IncrementalResult.ACTION_RELISTED:
                    results["relisted"].append(result)
                else:
                    results["skipped"].append(result)
                
                results["events"].extend(result.events)
                
            except Exception as e:
                logger.warning(f"Incremental processing error: {e}")
                results["skipped"].append(
                    IncrementalResult(IncrementalResult.ACTION_SKIPPED, listing_data)
                )
        
        return results
    
    def apply_incremental_results(
        self,
        session: Session,
        results: Dict[str, Any],
        source: str
    ) -> Dict[str, int]:
        """
        Apply incremental results to the database.
        
        - New listings: bulk insert
        - Updated listings: create new version, update existing
        - Unchanged: already handled (last_seen updated in process_listing)
        - Events: bulk insert all events
        
        Returns counts dict.
        """
        counts = {
            "inserted": 0,
            "updated": 0,
            "unchanged": len(results.get("unchanged", [])),
            "relisted": 0,
            "events": 0,
        }
        
        # 1. Insert new listings
        new_results = results.get("new", [])
        if new_results:
            new_listings = [r.listing_data for r in new_results]
            saved, failed = self.db_manager.save_listings_batch(
                session, new_listings, batch_size=100
            )
            counts["inserted"] = saved
        
        # 2. Handle updated listings (create new versions)
        for result in results.get("updated", []):
            try:
                existing = result.existing_listing
                if existing:
                    # Mark old as not latest
                    existing.is_latest = False
                    
                    # Insert new version
                    result.listing_data["version"] = existing.version + 1
                    result.listing_data["previous_version_id"] = existing.id
                    new_listing = self.db_manager.save_listing(session, result.listing_data)
                    counts["updated"] += 1
            except Exception as e:
                logger.warning(f"Failed to update listing: {e}")
        
        # 3. Handle relisted
        for result in results.get("relisted", []):
            try:
                existing = result.existing_listing
                if existing:
                    existing.is_active = True
                    existing.last_seen = datetime.utcnow()
                    
                    # Update price if changed
                    new_price = result.listing_data.get("price")
                    if new_price and existing.price != new_price:
                        existing.is_latest = False
                        result.listing_data["version"] = existing.version + 1
                        self.db_manager.save_listing(session, result.listing_data)
                    
                    counts["relisted"] += 1
            except Exception as e:
                logger.warning(f"Failed to handle relisted: {e}")
        
        # 4. Bulk insert events
        all_events = results.get("events", [])
        if all_events:
            # Need property_id for events - look up or skip
            event_objects = []
            for evt in all_events:
                property_uid = evt.get("property_uid")
                if not property_uid:
                    continue
                
                event_objects.append({
                    "property_id": evt.get("property_id", 0),
                    "property_uid": property_uid,
                    "event_type": evt.get("event_type"),
                    "old_value": evt.get("old_value"),
                    "new_value": evt.get("new_value"),
                    "value_numeric": evt.get("value_numeric"),
                    "source": evt.get("source"),
                    "event_metadata": evt.get("metadata"),
                    "timestamp": datetime.utcnow(),
                })
            
            if event_objects:
                try:
                    session.bulk_insert_mappings(PropertyEvent, event_objects)
                    counts["events"] = len(event_objects)
                except Exception as e:
                    logger.warning(f"Failed to bulk insert events: {e}")
        
        return counts
    
    def detect_delisted(
        self,
        session: Session,
        source: str,
        city: str,
        listing_type: str = "buy"
    ) -> int:
        """
        Detect and mark delisted properties.
        Properties not seen for stale_days are marked inactive + delisted event.
        Returns count of newly delisted.
        """
        count = self.db_manager.mark_listings_inactive(
            session, source, city, listing_type, self._stale_days
        )
        
        if count > 0:
            logger.info(
                f"Marked {count} listings as inactive "
                f"({source}/{city}/{listing_type}, unseen > {self._stale_days}d)"
            )
        
        return count
    
    def get_stats(self) -> Dict[str, Any]:
        """Get incremental engine statistics for current run."""
        total = self._new_count + self._updated_count + self._unchanged_count + self._relisted_count
        return {
            "total_processed": total,
            "new": self._new_count,
            "updated": self._updated_count,
            "unchanged": self._unchanged_count,
            "relisted": self._relisted_count,
            "events_created": self._events_created,
            "change_rate": (
                (self._new_count + self._updated_count) / max(1, total)
            ),
        }
    
    def reset_stats(self) -> None:
        """Reset run statistics."""
        with self._lock:
            self._new_count = 0
            self._updated_count = 0
            self._unchanged_count = 0
            self._relisted_count = 0
            self._events_created = 0


# Module-level singleton
_incremental_engine: Optional[IncrementalEngine] = None


def get_incremental_engine(config: Optional[dict] = None) -> IncrementalEngine:
    """Get or create the singleton IncrementalEngine."""
    global _incremental_engine
    if _incremental_engine is None:
        _incremental_engine = IncrementalEngine(config)
    return _incremental_engine
