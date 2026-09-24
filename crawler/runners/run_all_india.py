#!/usr/bin/env python3
"""
RealSlate Core - Unified Pan-India Crawler Runner
==================================================

MAIN ENTRY POINT for the RealSlate data engine.

Usage:
    python runners/run_all_india.py --mode full
    python runners/run_all_india.py --mode incremental
    python runners/run_all_india.py --mode incremental --site 99acres
    python runners/run_all_india.py --mode incremental --city Mumbai
    python runners/run_all_india.py --report
    python runners/run_all_india.py --dry-run

Modes:
    full         - Full crawl of all sites/cities (first run or rebuild)
    incremental  - Incremental crawl: skip unchanged, detect delisted, generate events

Targets:
    99acres, MagicBricks, NoBroker, Housing.com
"""

import asyncio
import gc
import json
import os
import signal
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from loguru import logger

from core.browser import BrowserManager
from core.extractor import DataExtractor
from sqlalchemy import and_
from core.db import (
    DatabaseManager, Base, Listing, Property, PropertyEvent,
    CrawlSession, CrawlState, Source,
)
from core.scheduler import CrawlScheduler
from core.validator import DataValidator
from core.dedup import Deduplicator
from core.monitor import CrawlMonitor, AlertLevel
from core.checkpoint import get_checkpoint_manager
from core.identity import (
    PropertyIdentityEngine, get_identity_engine,
    normalize_city, normalize_property_type,
)
from core.incremental import IncrementalEngine, get_incremental_engine
from core.utils import (
    ConfigLoader, setup_logging, get_config,
    generate_url_hash, random_string,
)

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False


# =============================================================================
# Target sites (only these four)
# =============================================================================

TARGET_SITES = ["99acres", "magicbricks", "nobroker", "housing"]


class PanIndiaCrawler:
    """
    Unified pan-India crawler engine.
    
    Orchestrates:
    1. Config loading for all 4 target sites
    2. Database initialization + migration
    3. Incremental crawl scheduling per site/city/listing_type
    4. Change detection + event generation
    5. Cross-source deduplication + property identity resolution
    6. Unified export generation
    7. Daily summary report
    """
    
    def __init__(self, config: Optional[dict] = None):
        self.config = config or get_config()
        self.config_loader = ConfigLoader()
        
        # Core components
        self.browser_manager = BrowserManager(self.config)
        self.db_manager = DatabaseManager(self.config)
        self.validator = DataValidator(self.config)
        self.deduplicator = Deduplicator(self.config)
        self.monitor = CrawlMonitor(self.config)
        self.checkpoint_manager = get_checkpoint_manager()
        self.identity_engine = get_identity_engine(self.config)
        self.incremental_engine = get_incremental_engine(self.config)
        
        # State
        self._initialized = False
        self._running = False
        self._shutdown_requested = False
        self._mode = "incremental"
        
        # Resource management
        self._memory_limit_mb = self.config.get("crawling", {}).get("memory_limit_mb", 1400)
        self._batch_size = self.config.get("database", {}).get("batch_size", 50)
        self._pages_since_gc = 0
        self._gc_interval = 10
        
        # Run stats
        self._run_start_time: Optional[float] = None
        self._run_stats: Dict[str, Any] = {}
    
    async def initialize(self) -> None:
        """Initialize all components."""
        if self._initialized:
            return
        
        logger.info("=" * 60)
        logger.info("RealSlate Pan-India Crawler - Initializing")
        logger.info("=" * 60)
        
        # Initialize database
        self.db_manager.initialize()
        self.db_manager.create_tables()
        
        # Register sources
        with self.db_manager.session() as session:
            for site_name in TARGET_SITES:
                try:
                    site_config = self.config_loader.load_site_config(site_name)
                    self.db_manager.get_or_create_source(
                        session,
                        name=site_config.get("site", {}).get("name", site_name),
                        base_url=site_config.get("site", {}).get("base_url", ""),
                        priority=site_config.get("site", {}).get("priority", 50),
                    )
                except Exception as e:
                    logger.warning(f"Failed to register source {site_name}: {e}")
        
        # Initialize browser
        await self.browser_manager.initialize()
        
        # NOTE: Do NOT pre-load dedup cache from DB.
        # The dedup layer only catches within-batch duplicates (same page).
        # The incremental engine handles historical dedup (last_seen, changes).
        self.deduplicator.clear_cache()
        logger.info("Dedup cache cleared — incremental engine handles historical comparison")
        
        # Cleanup stale checkpoints
        removed = self.checkpoint_manager.cleanup_stale_checkpoints()
        if removed > 0:
            logger.info(f"Cleaned up {removed} stale checkpoints")
        
        # Start Prometheus if configured
        try:
            self.monitor.start_prometheus_server()
        except Exception:
            pass
        
        self._initialized = True
        self._running = True
        
        self._log_memory("Post-initialization")
        logger.info("Initialization complete")
    
    async def shutdown(self) -> None:
        """Gracefully shutdown all components."""
        logger.info("Shutting down Pan-India Crawler...")
        self._running = False
        
        await self.browser_manager.close()
        self.db_manager.close()
        
        gc.collect()
        logger.info("Shutdown complete")
    
    async def run(
        self,
        mode: str = "incremental",
        sites: Optional[List[str]] = None,
        cities: Optional[List[str]] = None,
        listing_types: Optional[List[str]] = None,
        max_pages: Optional[int] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Main run method. Orchestrates the entire crawl pipeline.
        
        Args:
            mode: 'full' or 'incremental'
            sites: Filter to specific sites
            cities: Filter to specific cities
            listing_types: Filter to 'buy', 'rent', or both
            max_pages: Override max pages per city
            dry_run: If True, only log what would be done
        """
        self._mode = mode
        self._run_start_time = time.time()
        
        if not self._initialized:
            await self.initialize()
        
        target_sites = sites or TARGET_SITES
        target_listing_types = listing_types or ["buy", "rent"]
        
        # Validate target sites
        target_sites = [s for s in target_sites if s in TARGET_SITES]
        if not target_sites:
            logger.error(f"No valid target sites. Must be one of: {TARGET_SITES}")
            return {"error": "No valid target sites"}
        
        logger.info(f"Starting {mode.upper()} crawl")
        logger.info(f"Sites: {target_sites}")
        logger.info(f"Listing types: {target_listing_types}")
        if cities:
            logger.info(f"Cities filter: {cities}")
        if dry_run:
            logger.info("DRY RUN - no data will be saved")
        
        results = {
            "mode": mode,
            "started_at": datetime.utcnow().isoformat(),
            "sites": {},
            "totals": {
                "pages_crawled": 0,
                "listings_found": 0,
                "listings_new": 0,
                "listings_updated": 0,
                "listings_unchanged": 0,
                "events_created": 0,
                "errors": 0,
            },
        }
        
        for site_name in target_sites:
            if self._shutdown_requested:
                logger.info("Shutdown requested, stopping")
                break
            
            try:
                site_result = await self._crawl_site(
                    site_name,
                    cities=cities,
                    listing_types=target_listing_types,
                    max_pages=max_pages,
                    dry_run=dry_run,
                )
                results["sites"][site_name] = site_result
                
                # Accumulate totals
                for key in results["totals"]:
                    results["totals"][key] += site_result.get(key, 0)
                
            except Exception as e:
                logger.error(f"Site {site_name} failed: {e}")
                logger.debug(traceback.format_exc())
                results["sites"][site_name] = {"error": str(e)}
                results["totals"]["errors"] += 1
        
        # Post-crawl: dedup, exports, report
        results["ended_at"] = datetime.utcnow().isoformat()
        results["duration_seconds"] = time.time() - self._run_start_time
        
        if not dry_run:
            # Run global deduplication
            await self._global_deduplication()
            
            # Detect delisted listings
            await self._detect_delisted(target_sites, target_listing_types)
            
            # Generate exports
            export_paths = self._generate_exports()
            results["exports"] = export_paths
            
            # Generate report
            report = self._generate_report(results)
            results["report"] = report
            
            # Update source stats
            self._update_source_stats(results)
        
        # Log final summary
        self._log_final_summary(results)
        
        self._run_stats = results
        return results
    
    async def _crawl_site(
        self,
        site_name: str,
        cities: Optional[List[str]] = None,
        listing_types: Optional[List[str]] = None,
        max_pages: Optional[int] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Crawl a single site across all configured cities and listing types."""
        site_config = self.config_loader.load_site_config(site_name)
        
        if not site_config.get("site", {}).get("enabled", False):
            logger.warning(f"Site {site_name} is disabled, skipping")
            return {"skipped": True, "reason": "disabled"}
        
        display_name = site_config.get("site", {}).get("name", site_name)
        base_url = site_config.get("site", {}).get("base_url", "")
        config_listing_types = site_config.get("listing_types", ["buy"])
        
        active_types = listing_types or config_listing_types
        active_types = [t for t in active_types if t in config_listing_types]
        
        site_cities = site_config.get("cities", [])
        if cities:
            site_cities = [
                c for c in site_cities
                if c.get("name") in cities or c.get("slug") in [
                    ci.lower().replace(" ", "-") for ci in cities
                ]
            ]
        
        logger.info(f"\n{'='*50}")
        logger.info(f"Crawling {display_name}: {len(site_cities)} cities x {len(active_types)} types")
        logger.info(f"{'='*50}")
        
        session_id = f"{site_name}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
        self.monitor.start_session(session_id, display_name)
        
        site_result = {
            "pages_crawled": 0,
            "listings_found": 0,
            "listings_new": 0,
            "listings_updated": 0,
            "listings_unchanged": 0,
            "events_created": 0,
            "errors": 0,
            "cities_processed": 0,
        }
        
        extractor = DataExtractor(site_config)
        
        for city_config in site_cities:
            if self._shutdown_requested or not self._running:
                break
            
            if not city_config.get("enabled", True):
                continue
            
            city_name = city_config.get("name")
            city_slug = city_config.get("slug", city_name.lower().replace(" ", "-"))
            url_patterns = city_config.get("url_patterns", {})
            
            # Backwards compatibility: support old single url_pattern
            if not url_patterns and city_config.get("url_pattern"):
                url_patterns = {"buy": city_config["url_pattern"]}
            
            for listing_type in active_types:
                if self._shutdown_requested:
                    break
                
                url_pattern = url_patterns.get(listing_type)
                if not url_pattern:
                    continue
                
                full_url = base_url + url_pattern
                
                logger.info(f"  [{display_name}] {city_name} / {listing_type.upper()}")
                
                if dry_run:
                    logger.info(f"    DRY RUN: would crawl {full_url}")
                    continue
                
                try:
                    await self._check_memory()
                    
                    city_result = await self._crawl_city(
                        site_name=site_name,
                        site_config=site_config,
                        city_name=city_name,
                        city_slug=city_slug,
                        listing_type=listing_type,
                        start_url=full_url,
                        base_url=base_url,
                        extractor=extractor,
                        max_pages=max_pages,
                        session_id=session_id,
                    )
                    
                    for key in ["pages_crawled", "listings_found", "listings_new",
                                "listings_updated", "listings_unchanged", "events_created"]:
                        site_result[key] += city_result.get(key, 0)
                    
                    site_result["cities_processed"] += 1
                    
                    # Update crawl state
                    with self.db_manager.session() as session:
                        self.db_manager.update_crawl_state_success(
                            session, site_name, city_name, listing_type,
                            page_reached=city_result.get("pages_crawled", 0),
                            found=city_result.get("listings_found", 0),
                            new=city_result.get("listings_new", 0),
                            updated=city_result.get("listings_updated", 0),
                            unchanged=city_result.get("listings_unchanged", 0),
                        )
                    
                except Exception as e:
                    logger.error(f"    Error: {e}")
                    logger.debug(traceback.format_exc())
                    site_result["errors"] += 1
                    
                    with self.db_manager.session() as session:
                        self.db_manager.update_crawl_state_error(
                            session, site_name, city_name, listing_type, str(e)
                        )
        
        self.monitor.end_session(session_id)
        gc.collect()
        
        return site_result
    
    async def _crawl_city(
        self,
        site_name: str,
        site_config: dict,
        city_name: str,
        city_slug: str,
        listing_type: str,
        start_url: str,
        base_url: str,
        extractor: DataExtractor,
        max_pages: Optional[int] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Crawl a single city/listing_type combination."""
        result = {
            "pages_crawled": 0,
            "listings_found": 0,
            "listings_new": 0,
            "listings_updated": 0,
            "listings_unchanged": 0,
            "events_created": 0,
        }
        
        pagination_config = site_config.get("pagination", {})
        pagination_type = pagination_config.get("type", "url_param")
        max_site_pages = max_pages or pagination_config.get("max_pages", 50)
        
        # Create checkpoint
        checkpoint = self.checkpoint_manager.start_checkpoint(
            site_name=site_name,
            city_name=city_name,
            city_slug=city_slug,
            start_url=start_url,
        )
        
        start_page = checkpoint.current_page
        
        async with self.browser_manager.session(site_name) as page:
            current_page = start_page
            consecutive_errors = 0
            
            while current_page <= max_site_pages:
                if self._shutdown_requested or not self._running:
                    break
                
                # Build page URL
                if pagination_type == "url_param":
                    param_name = pagination_config.get("url_param", "page")
                    if "?" in start_url:
                        page_url = f"{start_url}&{param_name}={current_page}"
                    else:
                        page_url = f"{start_url}?{param_name}={current_page}"
                else:
                    page_url = start_url
                
                page_start = time.time()
                
                try:
                    # Navigate
                    await self.browser_manager.navigate(page_url, page)
                    
                    # Get content
                    html_content = await self.browser_manager.get_page_content(page)
                    
                    # Handle infinite scroll
                    if pagination_type == "infinite_scroll":
                        max_scrolls = pagination_config.get("max_scrolls", 50)
                        scroll_wait = pagination_config.get("scroll_wait_seconds", 2)
                        await self.browser_manager.infinite_scroll(
                            page,
                            max_scrolls=max_scrolls,
                            scroll_wait=scroll_wait,
                        )
                        html_content = await self.browser_manager.get_page_content(page)
                    
                    # Extract listings
                    raw_listings = extractor.extract_listings(html_content, base_url)
                    
                    if not raw_listings:
                        logger.info(f"    No listings found at page {current_page}")
                        break
                    
                    # Add metadata to each listing
                    for listing in raw_listings:
                        listing["source"] = site_name
                        listing["city"] = city_name
                        listing["listing_type"] = listing_type
                    
                    # Validate
                    valid, invalid = self.validator.validate_batch(raw_listings)
                    
                    # Data sanity gate: reject absurd price/type combos
                    sane = []
                    for listing in valid:
                        price = listing.get("price")
                        lt = listing.get("listing_type", listing_type)
                        if price and lt == "rent" and price > 5_000_000:
                            logger.debug(f"    Sanity reject: rent price {price} too high")
                            continue
                        if price and lt == "buy" and price < 50_000:
                            logger.debug(f"    Sanity reject: buy price {price} too low")
                            continue
                        sane.append(listing)
                    valid = sane
                    
                    # Deduplicate within batch
                    unique, dupes = self.deduplicator.deduplicate_batch(valid)
                    
                    # FIX: Update last_seen for dedup-matched listings
                    if dupes:
                        with self.db_manager.session() as session:
                            now = datetime.utcnow()
                            for dup in dupes:
                                uh = dup.get("url_hash")
                                src = dup.get("source", site_name)
                                if uh:
                                    existing = self.db_manager.get_active_listing_for_incremental(
                                        session, uh, src
                                    )
                                    if existing:
                                        existing.last_seen = now
                                        existing.is_active = True
                    
                    # Apply identity UIDs
                    for listing in unique:
                        listing["property_uid"] = self.identity_engine.generate_property_uid(listing)
                    
                    # Incremental processing
                    with self.db_manager.session() as session:
                        inc_results = self.incremental_engine.process_batch(session, unique)
                        counts = self.incremental_engine.apply_incremental_results(
                            session, inc_results, site_name
                        )
                        
                        # Populate properties table for new + updated listings
                        for inc_result in (
                            inc_results.get("new", []) + inc_results.get("updated", [])
                            + inc_results.get("relisted", [])
                        ):
                            ld = inc_result.listing_data
                            puid = ld.get("property_uid")
                            if puid:
                                try:
                                    prop, is_new_prop, price_changed = (
                                        self.db_manager.get_or_create_property(
                                            session, puid, ld
                                        )
                                    )
                                    session.flush()
                                    # Link any listing with this uid to the property
                                    session.query(Listing).filter(
                                        and_(
                                            Listing.property_uid == puid,
                                            Listing.property_id.is_(None),
                                        )
                                    ).update(
                                        {"property_id": prop.id},
                                        synchronize_session=False,
                                    )
                                except Exception as prop_err:
                                    logger.debug(f"Property upsert: {prop_err}")
                    
                    page_duration = time.time() - page_start
                    
                    result["pages_crawled"] += 1
                    result["listings_found"] += len(raw_listings)
                    result["listings_new"] += counts.get("inserted", 0)
                    result["listings_updated"] += counts.get("updated", 0)
                    result["listings_unchanged"] += counts.get("unchanged", 0)
                    result["events_created"] += counts.get("events", 0)
                    
                    # Update monitoring counters
                    self.monitor.record_page(
                        session_id,
                        success=True,
                        duration_seconds=page_duration,
                        listings_count=len(raw_listings),
                        valid_count=len(valid),
                        duplicate_count=len(dupes),
                    )
                    
                    # Checkpoint
                    self.checkpoint_manager.mark_page_complete(checkpoint, current_page)
                    
                    logger.info(
                        f"    Page {current_page}: {len(raw_listings)} found, "
                        f"{counts.get('inserted', 0)} new, "
                        f"{counts.get('updated', 0)} updated, "
                        f"{counts.get('unchanged', 0)} unchanged "
                        f"({page_duration:.1f}s)"
                    )
                    
                    consecutive_errors = 0
                    
                    # Memory management
                    self._pages_since_gc += 1
                    if self._pages_since_gc >= self._gc_interval:
                        gc.collect()
                        self._pages_since_gc = 0
                    
                    # Pagination handling
                    if pagination_type == "infinite_scroll":
                        break
                    
                    current_page += 1
                    
                except Exception as e:
                    consecutive_errors += 1
                    logger.warning(f"    Page {current_page} error: {e}")
                    
                    if consecutive_errors >= 3:
                        try:
                            await self.browser_manager.safe_restart(new_proxy=True)
                            consecutive_errors = 0
                            continue
                        except Exception:
                            logger.error("    Browser restart failed, aborting city")
                            break
                    
                    current_page += 1
        
        self.checkpoint_manager.complete_checkpoint(
            checkpoint, saved_count=result["listings_new"]
        )
        
        return result
    
    async def _check_memory(self) -> None:
        """Check memory and take action if needed."""
        if not PSUTIL_AVAILABLE:
            return
        try:
            mem_mb = psutil.Process().memory_info().rss / (1024 * 1024)
            if mem_mb > self._memory_limit_mb:
                logger.warning(f"Memory high ({mem_mb:.0f}MB), recycling browser")
                gc.collect()
                await self.browser_manager.safe_restart(new_proxy=True)
                gc.collect()
        except Exception:
            pass
    
    async def _global_deduplication(self) -> None:
        """Log dedup stats from the current run."""
        logger.info("Dedup stats for this run:")
        logger.info(f"  {self.deduplicator.get_stats()}")
    
    async def _detect_delisted(
        self,
        sites: List[str],
        listing_types: List[str]
    ) -> None:
        """Detect and mark delisted listings across all sites/cities."""
        logger.info("Detecting delisted listings...")
        total_delisted = 0
        
        try:
            with self.db_manager.session() as session:
                for site_name in sites:
                    site_config = self.config_loader.load_site_config(site_name)
                    for city_config in site_config.get("cities", []):
                        city_name = city_config.get("name")
                        for lt in listing_types:
                            count = self.incremental_engine.detect_delisted(
                                session, site_name, city_name, lt
                            )
                            total_delisted += count
            
            if total_delisted > 0:
                logger.info(f"Marked {total_delisted} listings as delisted")
        except Exception as e:
            logger.warning(f"Delist detection error: {e}")
    
    def _generate_exports(self) -> Dict[str, str]:
        """Generate unified exports — ALL data, one persistent file per format."""
        logger.info("Generating exports...")
        export_dir = PROJECT_ROOT / "exports"
        export_dir.mkdir(exist_ok=True)
        
        paths = {}
        
        try:
            import csv as csv_mod
            
            csv_path = str(export_dir / "realslate_data.csv")
            
            with self.db_manager.session() as session:
                from core.db import Listing
                listings = session.query(Listing).filter(
                    Listing.is_latest == True
                ).order_by(Listing.scraped_at.desc()).all()
                
                rows = []
                for l in listings:
                    rows.append({
                        "id": l.id,
                        "property_uid": l.property_uid,
                        "source": l.source,
                        "city": l.city,
                        "listing_type": l.listing_type,
                        "price": l.price,
                        "address": l.address,
                        "locality": l.locality,
                        "beds": l.beds,
                        "bathrooms": l.bathrooms,
                        "area": l.area,
                        "property_type": l.property_type,
                        "builder_name": l.builder_name,
                        "url": l.url,
                        "is_active": l.is_active,
                        "last_seen": l.last_seen.isoformat() if l.last_seen else "",
                        "scraped_at": l.scraped_at.isoformat() if l.scraped_at else "",
                        "version": l.version,
                    })
            
            # Write CSV
            if rows:
                fields = list(rows[0].keys())
                with open(csv_path, "w", newline="", encoding="utf-8") as f:
                    writer = csv_mod.DictWriter(f, fieldnames=fields)
                    writer.writeheader()
                    writer.writerows(rows)
                paths["csv"] = csv_path
                logger.info(f"Exported {len(rows)} listings to {csv_path}")
            else:
                logger.info("No listings to export")
            
            # JSON
            json_path = str(export_dir / "realslate_data.json")
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(rows, f, indent=2, ensure_ascii=False, default=str)
            paths["json"] = json_path
            
        except Exception as e:
            logger.warning(f"Export generation error: {e}")
            import traceback
            logger.debug(traceback.format_exc())
        
        return paths
    
    def _generate_report(self, results: Dict[str, Any]) -> Dict[str, Any]:
        """Generate daily summary report."""
        report = {
            "date": datetime.utcnow().date().isoformat(),
            "mode": results.get("mode"),
            "duration_seconds": results.get("duration_seconds"),
            "sites_crawled": len(results.get("sites", {})),
            "totals": results.get("totals", {}),
            "per_site": {},
            "incremental_stats": self.incremental_engine.get_stats(),
            "dedup_stats": self.deduplicator.get_stats(),
            "identity_stats": self.identity_engine.get_stats(),
        }
        
        for site_name, site_result in results.get("sites", {}).items():
            if isinstance(site_result, dict) and "error" not in site_result:
                report["per_site"][site_name] = {
                    "pages": site_result.get("pages_crawled", 0),
                    "found": site_result.get("listings_found", 0),
                    "new": site_result.get("listings_new", 0),
                    "updated": site_result.get("listings_updated", 0),
                    "unchanged": site_result.get("listings_unchanged", 0),
                    "cities": site_result.get("cities_processed", 0),
                }
        
        # Write report to file
        report_dir = PROJECT_ROOT / "logs"
        report_dir.mkdir(exist_ok=True)
        report_path = report_dir / f"report_{datetime.utcnow().strftime('%Y%m%d')}.json"
        
        try:
            with open(report_path, "w") as f:
                json.dump(report, f, indent=2, default=str)
            logger.info(f"Report written to {report_path}")
        except Exception as e:
            logger.warning(f"Failed to write report: {e}")
        
        return report
    
    def _update_source_stats(self, results: Dict[str, Any]) -> None:
        """Update source registry with run results."""
        try:
            with self.db_manager.session() as session:
                for site_name, site_result in results.get("sites", {}).items():
                    if isinstance(site_result, dict):
                        success = "error" not in site_result
                        listings = site_result.get("listings_found", 0) if success else 0
                        
                        # Map config name to DB source name
                        try:
                            site_config = self.config_loader.load_site_config(site_name)
                            db_name = site_config.get("site", {}).get("name", site_name)
                        except Exception:
                            db_name = site_name
                        
                        self.db_manager.update_source_stats(
                            session, db_name, listings, success
                        )
        except Exception as e:
            logger.warning(f"Source stats update error: {e}")
    
    def _log_memory(self, label: str) -> None:
        """Log current memory usage."""
        if PSUTIL_AVAILABLE:
            try:
                mem_mb = psutil.Process().memory_info().rss / (1024 * 1024)
                logger.info(f"Memory [{label}]: {mem_mb:.1f} MB")
            except Exception:
                pass
    
    def _log_final_summary(self, results: Dict[str, Any]) -> None:
        """Print final summary to console."""
        totals = results.get("totals", {})
        duration = results.get("duration_seconds", 0)
        
        logger.info("\n" + "=" * 60)
        logger.info("CRAWL COMPLETE - SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Mode:            {results.get('mode', 'unknown')}")
        logger.info(f"Duration:        {duration:.0f}s ({duration/60:.1f}m)")
        logger.info(f"Pages crawled:   {totals.get('pages_crawled', 0)}")
        logger.info(f"Listings found:  {totals.get('listings_found', 0)}")
        logger.info(f"  New:           {totals.get('listings_new', 0)}")
        logger.info(f"  Updated:       {totals.get('listings_updated', 0)}")
        logger.info(f"  Unchanged:     {totals.get('listings_unchanged', 0)}")
        logger.info(f"Events created:  {totals.get('events_created', 0)}")
        logger.info(f"Errors:          {totals.get('errors', 0)}")
        logger.info("=" * 60)
        
        self._log_memory("Final")
    
    def request_shutdown(self) -> None:
        """Signal the crawler to stop gracefully."""
        self._shutdown_requested = True
        self._running = False
        logger.info("Graceful shutdown requested")


# =============================================================================
# CLI Entry Point
# =============================================================================

async def async_main():
    """Async main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="RealSlate Pan-India Crawler - Unified Data Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python runners/run_all_india.py --mode full
  python runners/run_all_india.py --mode incremental
  python runners/run_all_india.py --mode incremental --site 99acres
  python runners/run_all_india.py --mode incremental --city Mumbai --city Delhi
  python runners/run_all_india.py --dry-run
  python runners/run_all_india.py --report
        """
    )
    
    parser.add_argument(
        "--mode", "-m",
        choices=["full", "incremental"],
        default="incremental",
        help="Crawl mode (default: incremental)"
    )
    parser.add_argument(
        "--site", "-s",
        action="append",
        choices=TARGET_SITES,
        help="Specific site(s) to crawl (can specify multiple)"
    )
    parser.add_argument(
        "--city", "-c",
        action="append",
        help="Specific city/cities to crawl (can specify multiple)"
    )
    parser.add_argument(
        "--type", "-t",
        action="append",
        choices=["buy", "rent"],
        help="Listing type(s) to crawl (default: both)"
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        help="Max pages per city override"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be crawled without actually crawling"
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="Generate report from last run data and exit"
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log level (default: INFO)"
    )
    
    args = parser.parse_args()
    
    # Setup logging
    setup_logging(
        log_dir=str(PROJECT_ROOT / "logs"),
        level=args.log_level,
    )
    
    crawler = PanIndiaCrawler()
    
    # Setup signal handlers for graceful shutdown
    def signal_handler(sig, frame):
        logger.info(f"Received signal {sig}, requesting shutdown...")
        crawler.request_shutdown()
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        if args.report:
            # Report-only mode
            await crawler.initialize()
            report = crawler._generate_report({"sites": {}, "totals": {}, "mode": "report"})
            print(json.dumps(report, indent=2, default=str))
            return
        
        results = await crawler.run(
            mode=args.mode,
            sites=args.site,
            cities=args.city,
            listing_types=args.type,
            max_pages=args.max_pages,
            dry_run=args.dry_run,
        )
        
        # Exit with error code if there were errors
        errors = results.get("totals", {}).get("errors", 0)
        if errors > 0:
            sys.exit(1)
        
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.critical(f"Fatal error: {e}")
        logger.debug(traceback.format_exc())
        sys.exit(2)
    finally:
        await crawler.shutdown()


def main():
    """Synchronous entry point."""
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
