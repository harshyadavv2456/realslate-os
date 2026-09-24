"""
RealSlate Core - Crawl Scheduler
Manages crawl scheduling, URL queuing, and rate limiting.
"""

import asyncio
import random
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Callable
from dataclasses import dataclass
from enum import Enum

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger

from .utils import get_config, ConfigLoader, generate_url_hash, random_string


class CrawlStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"


@dataclass
class CrawlTask:
    """Represents a crawl task."""
    task_id: str
    site_name: str
    city: str
    url: str
    page_type: str = "listing"
    priority: int = 100
    status: CrawlStatus = CrawlStatus.PENDING
    attempts: int = 0
    max_attempts: int = 3
    created_at: datetime = None
    started_at: datetime = None
    completed_at: datetime = None
    error: str = None
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.utcnow()


class CrawlScheduler:
    """
    Production-grade crawl scheduler with:
    - Task queuing and prioritization
    - Site rotation
    - Rate limiting
    - Failure handling
    - Daily scheduling
    """
    
    def __init__(self, config: Optional[dict] = None):
        self.config = config or get_config()
        self.scheduler_config = self.config.get("scheduler", {})
        self.crawl_config = self.config.get("crawling", {})
        
        self._scheduler = AsyncIOScheduler(timezone="Asia/Kolkata")
        self._task_queue: List[CrawlTask] = []
        self._running_tasks: Dict[str, CrawlTask] = {}
        self._completed_tasks: List[CrawlTask] = []
        self._failed_tasks: List[CrawlTask] = []
        
        # Stats
        self._daily_stats: Dict[str, Dict[str, int]] = {}
        self._site_stats: Dict[str, Dict[str, Any]] = {}
        
        # Rate limiting
        self._last_request_time: Dict[str, datetime] = {}
        
        # Callbacks
        self._crawl_callback: Optional[Callable] = None
        
        self.config_loader = ConfigLoader()
    
    async def initialize(self) -> None:
        """Initialize the scheduler."""
        logger.info("Initializing crawl scheduler...")
        
        if self.scheduler_config.get("enabled", True):
            self._setup_daily_schedule()
        
        self._scheduler.start()
        logger.info("Crawl scheduler initialized")
    
    def _setup_daily_schedule(self) -> None:
        """Setup daily crawl schedule."""
        hour = self.scheduler_config.get("daily_run_hour", 2)
        minute = self.scheduler_config.get("daily_run_minute", 0)
        
        self._scheduler.add_job(
            self.run_daily_crawl,
            CronTrigger(hour=hour, minute=minute),
            id="daily_crawl",
            name="Daily Crawl Job",
            replace_existing=True,
        )
        
        logger.info(f"Daily crawl scheduled at {hour:02d}:{minute:02d} IST")
    
    def set_crawl_callback(self, callback: Callable) -> None:
        """Set the callback function for crawling."""
        self._crawl_callback = callback
    
    def build_crawl_queue(
        self,
        sites: Optional[List[str]] = None,
        cities: Optional[List[str]] = None,
        max_sites: Optional[int] = None
    ) -> List[CrawlTask]:
        """
        Build crawl queue from site configurations.
        """
        tasks = []
        
        # Get enabled sites
        if sites:
            enabled_sites = [
                {"name": s, "config": self.config_loader.load_site_config(s)}
                for s in sites
            ]
        else:
            enabled_sites = self.config_loader.get_enabled_sites()
        
        # Limit sites if configured
        max_sites = max_sites or self.scheduler_config.get("sites_per_day")
        if max_sites and len(enabled_sites) > max_sites:
            # Rotate sites based on day
            if self.scheduler_config.get("rotate_sites", True):
                day_of_year = datetime.utcnow().timetuple().tm_yday
                start_idx = (day_of_year * max_sites) % len(enabled_sites)
                enabled_sites = enabled_sites[start_idx:start_idx + max_sites]
                
                # Wrap around if needed
                if len(enabled_sites) < max_sites:
                    enabled_sites += enabled_sites[:max_sites - len(enabled_sites)]
            else:
                enabled_sites = enabled_sites[:max_sites]
        
        for site_info in enabled_sites:
            site_name = site_info["name"]
            site_config = site_info["config"]
            
            site_cities = site_config.get("cities", [])
            
            for city_config in site_cities:
                if not city_config.get("enabled", True):
                    continue
                
                city_name = city_config.get("name")
                
                # Filter cities if specified
                if cities and city_name not in cities:
                    continue
                
                # Build URL
                base_url = site_config.get("site", {}).get("base_url", "")
                url_pattern = city_config.get("url_pattern", "")
                full_url = base_url + url_pattern
                
                task = CrawlTask(
                    task_id=f"{site_name}_{city_config.get('slug')}_{random_string(8)}",
                    site_name=site_name,
                    city=city_name,
                    url=full_url,
                    page_type="listing",
                    priority=site_config.get("site", {}).get("priority", 50),
                )
                
                tasks.append(task)
        
        # Sort by priority
        tasks.sort(key=lambda x: x.priority)
        
        logger.info(f"Built crawl queue with {len(tasks)} tasks")
        
        return tasks
    
    def add_task(self, task: CrawlTask) -> None:
        """Add a task to the queue."""
        self._task_queue.append(task)
        self._task_queue.sort(key=lambda x: x.priority)
    
    def add_tasks(self, tasks: List[CrawlTask]) -> None:
        """Add multiple tasks to the queue."""
        self._task_queue.extend(tasks)
        self._task_queue.sort(key=lambda x: x.priority)
    
    def get_next_task(self, site_name: Optional[str] = None) -> Optional[CrawlTask]:
        """Get the next task to process."""
        for i, task in enumerate(self._task_queue):
            if task.status != CrawlStatus.PENDING:
                continue
            
            if site_name and task.site_name != site_name:
                continue
            
            # Check rate limiting
            if not self._can_crawl_site(task.site_name):
                continue
            
            # Check daily limits
            if self._is_site_limit_reached(task.site_name):
                continue
            
            # Remove from queue and return
            return self._task_queue.pop(i)
        
        return None
    
    def _can_crawl_site(self, site_name: str) -> bool:
        """Check if we can crawl a site based on rate limits."""
        last_time = self._last_request_time.get(site_name)
        
        if not last_time:
            return True
        
        # Get site-specific delay
        try:
            site_config = self.config_loader.load_site_config(site_name)
            min_delay = site_config.get("site", {}).get(
                "min_delay_seconds",
                self.crawl_config.get("min_delay_seconds", 5)
            )
        except Exception:
            min_delay = self.crawl_config.get("min_delay_seconds", 5)
        
        elapsed = (datetime.utcnow() - last_time).total_seconds()
        
        return elapsed >= min_delay
    
    def _is_site_limit_reached(self, site_name: str) -> bool:
        """Check if daily limit is reached for a site."""
        today = datetime.utcnow().date().isoformat()
        
        site_stats = self._daily_stats.get(today, {}).get(site_name, {})
        pages_crawled = site_stats.get("pages_crawled", 0)
        
        # Get site-specific limit
        try:
            site_config = self.config_loader.load_site_config(site_name)
            max_pages = site_config.get("site", {}).get(
                "max_pages_per_day",
                self.crawl_config.get("max_pages_per_site_per_day", 500)
            )
        except Exception:
            max_pages = self.crawl_config.get("max_pages_per_site_per_day", 500)
        
        return pages_crawled >= max_pages
    
    def record_crawl(
        self,
        site_name: str,
        success: bool = True,
        listings_count: int = 0
    ) -> None:
        """Record a crawl for rate limiting and stats."""
        self._last_request_time[site_name] = datetime.utcnow()
        
        today = datetime.utcnow().date().isoformat()
        
        if today not in self._daily_stats:
            self._daily_stats[today] = {}
        
        if site_name not in self._daily_stats[today]:
            self._daily_stats[today][site_name] = {
                "pages_crawled": 0,
                "listings_found": 0,
                "errors": 0,
            }
        
        stats = self._daily_stats[today][site_name]
        stats["pages_crawled"] += 1
        stats["listings_found"] += listings_count
        
        if not success:
            stats["errors"] += 1
    
    def mark_task_started(self, task: CrawlTask) -> None:
        """Mark a task as started."""
        task.status = CrawlStatus.RUNNING
        task.started_at = datetime.utcnow()
        task.attempts += 1
        self._running_tasks[task.task_id] = task
    
    def mark_task_completed(self, task: CrawlTask) -> None:
        """Mark a task as completed."""
        task.status = CrawlStatus.COMPLETED
        task.completed_at = datetime.utcnow()
        
        if task.task_id in self._running_tasks:
            del self._running_tasks[task.task_id]
        
        self._completed_tasks.append(task)
    
    def mark_task_failed(self, task: CrawlTask, error: str = None) -> None:
        """Mark a task as failed."""
        task.error = error
        
        if task.task_id in self._running_tasks:
            del self._running_tasks[task.task_id]
        
        if task.attempts < task.max_attempts:
            # Retry
            task.status = CrawlStatus.PENDING
            task.priority += 10  # Lower priority for retry
            self._task_queue.append(task)
            logger.warning(f"Task {task.task_id} failed, will retry ({task.attempts}/{task.max_attempts})")
        else:
            task.status = CrawlStatus.FAILED
            self._failed_tasks.append(task)
            logger.error(f"Task {task.task_id} failed permanently: {error}")
    
    async def run_daily_crawl(self) -> Dict[str, Any]:
        """
        Execute daily crawl routine.
        """
        logger.info("Starting daily crawl...")
        
        start_time = datetime.utcnow()
        session_id = f"daily_{start_time.strftime('%Y%m%d_%H%M%S')}"
        
        results = {
            "session_id": session_id,
            "started_at": start_time.isoformat(),
            "sites_crawled": 0,
            "pages_crawled": 0,
            "listings_found": 0,
            "errors": 0,
            "site_results": {},
        }
        
        try:
            # Build queue
            tasks = self.build_crawl_queue()
            self.add_tasks(tasks)
            
            # Process tasks
            while True:
                task = self.get_next_task()
                
                if not task:
                    break
                
                self.mark_task_started(task)
                
                try:
                    if self._crawl_callback:
                        # Execute crawl
                        crawl_result = await self._crawl_callback(task)
                        
                        # Record results
                        listings_count = crawl_result.get("listings_count", 0)
                        self.record_crawl(task.site_name, True, listings_count)
                        
                        results["pages_crawled"] += crawl_result.get("pages_crawled", 1)
                        results["listings_found"] += listings_count
                        
                        self.mark_task_completed(task)
                    else:
                        logger.warning("No crawl callback set")
                        self.mark_task_failed(task, "No crawl callback")
                    
                except Exception as e:
                    self.record_crawl(task.site_name, False)
                    self.mark_task_failed(task, str(e))
                    results["errors"] += 1
                
                # Small delay between tasks
                await asyncio.sleep(random.uniform(1, 3))
            
            results["sites_crawled"] = len(set(t.site_name for t in self._completed_tasks))
            
        except Exception as e:
            logger.error(f"Daily crawl error: {e}")
            results["error"] = str(e)
        
        finally:
            results["ended_at"] = datetime.utcnow().isoformat()
            results["duration_seconds"] = (
                datetime.utcnow() - start_time
            ).total_seconds()
            
            logger.info(
                f"Daily crawl completed: {results['pages_crawled']} pages, "
                f"{results['listings_found']} listings, {results['errors']} errors"
            )
        
        return results
    
    async def run_site_crawl(
        self,
        site_name: str,
        cities: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Run crawl for a specific site."""
        logger.info(f"Starting crawl for {site_name}...")
        
        tasks = self.build_crawl_queue(sites=[site_name], cities=cities)
        self.add_tasks(tasks)
        
        results = {
            "site": site_name,
            "pages_crawled": 0,
            "listings_found": 0,
            "errors": 0,
        }
        
        while True:
            task = self.get_next_task(site_name=site_name)
            
            if not task:
                break
            
            self.mark_task_started(task)
            
            try:
                if self._crawl_callback:
                    crawl_result = await self._crawl_callback(task)
                    
                    listings_count = crawl_result.get("listings_count", 0)
                    self.record_crawl(site_name, True, listings_count)
                    
                    results["pages_crawled"] += crawl_result.get("pages_crawled", 1)
                    results["listings_found"] += listings_count
                    
                    self.mark_task_completed(task)
                    
            except Exception as e:
                self.record_crawl(site_name, False)
                self.mark_task_failed(task, str(e))
                results["errors"] += 1
            
            await asyncio.sleep(random.uniform(1, 3))
        
        return results
    
    def get_queue_stats(self) -> Dict[str, Any]:
        """Get queue statistics."""
        return {
            "pending": len([t for t in self._task_queue if t.status == CrawlStatus.PENDING]),
            "running": len(self._running_tasks),
            "completed": len(self._completed_tasks),
            "failed": len(self._failed_tasks),
            "total": len(self._task_queue) + len(self._running_tasks) + len(self._completed_tasks) + len(self._failed_tasks),
        }
    
    def get_daily_stats(self) -> Dict[str, Any]:
        """Get daily statistics."""
        today = datetime.utcnow().date().isoformat()
        return self._daily_stats.get(today, {})
    
    def clear_completed(self) -> None:
        """Clear completed and failed task lists."""
        self._completed_tasks.clear()
        self._failed_tasks.clear()
    
    def pause(self) -> None:
        """Pause the scheduler."""
        self._scheduler.pause()
        logger.info("Scheduler paused")
    
    def resume(self) -> None:
        """Resume the scheduler."""
        self._scheduler.resume()
        logger.info("Scheduler resumed")
    
    def shutdown(self) -> None:
        """Shutdown the scheduler."""
        self._scheduler.shutdown()
        logger.info("Scheduler shutdown")
