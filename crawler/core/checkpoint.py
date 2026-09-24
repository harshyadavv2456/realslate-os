"""
RealSlate Core - Checkpoint Manager
Crash recovery with page-level and city-level checkpointing.
"""

import os
import json
import time
import atexit
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, asdict, field
from threading import Lock

from loguru import logger

from .utils import get_config


@dataclass
class CrawlCheckpoint:
    """Checkpoint state for a crawl operation."""
    site_name: str
    city_name: str
    city_slug: str
    
    # Progress tracking
    current_page: int = 1
    pages_completed: int = 0
    listings_extracted: int = 0
    listings_saved: int = 0
    
    # Timing
    started_at: str = ""
    last_updated_at: str = ""
    
    # State
    last_url: str = ""
    last_error: Optional[str] = None
    retry_count: int = 0
    
    # Collected data (for recovery)
    pending_listings: List[Dict[str, Any]] = field(default_factory=list)
    
    def update(self, **kwargs) -> None:
        """Update checkpoint fields."""
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
        self.last_updated_at = datetime.utcnow().isoformat()


class CheckpointManager:
    """
    Production-grade checkpoint manager for crash recovery.
    
    Features:
    - Page-level checkpointing
    - City-level progress tracking
    - Automatic resume on restart
    - Safe atomic writes
    - Cleanup of stale checkpoints
    """
    
    def __init__(self, config: Optional[dict] = None):
        self.config = config or get_config()
        
        self.checkpoint_dir = Path(
            self.config.get("checkpoint", {}).get("directory", ".checkpoints")
        )
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        
        self._checkpoints: Dict[str, CrawlCheckpoint] = {}
        self._lock = Lock()
        
        # Configuration
        self._max_pending_listings = self.config.get("checkpoint", {}).get(
            "max_pending_listings", 100
        )
        self._stale_hours = self.config.get("checkpoint", {}).get(
            "stale_hours", 24
        )
        
        # Register cleanup on exit
        atexit.register(self._cleanup_on_exit)
        
        # Load existing checkpoints
        self._load_checkpoints()
    
    def _get_checkpoint_key(self, site_name: str, city_slug: str) -> str:
        """Generate unique checkpoint key."""
        return f"{site_name}_{city_slug}"
    
    def _get_checkpoint_path(self, key: str) -> Path:
        """Get path for checkpoint file."""
        return self.checkpoint_dir / f"{key}.checkpoint.json"
    
    def _load_checkpoints(self) -> None:
        """Load all existing checkpoints from disk."""
        for checkpoint_file in self.checkpoint_dir.glob("*.checkpoint.json"):
            try:
                with open(checkpoint_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                checkpoint = CrawlCheckpoint(**data)
                key = self._get_checkpoint_key(checkpoint.site_name, checkpoint.city_slug)
                self._checkpoints[key] = checkpoint
                
                logger.debug(f"Loaded checkpoint: {key}")
                
            except Exception as e:
                logger.warning(f"Failed to load checkpoint {checkpoint_file}: {e}")
    
    def start_checkpoint(self, 
                        site_name: str, 
                        city_name: str, 
                        city_slug: str,
                        start_url: str = "") -> CrawlCheckpoint:
        """
        Start a new checkpoint or resume existing one.
        
        Returns existing checkpoint if found, otherwise creates new.
        """
        key = self._get_checkpoint_key(site_name, city_slug)
        
        with self._lock:
            # Check for existing checkpoint
            if key in self._checkpoints:
                checkpoint = self._checkpoints[key]
                logger.info(
                    f"Resuming checkpoint: {key} "
                    f"(page {checkpoint.current_page}, "
                    f"{checkpoint.listings_extracted} listings)"
                )
                return checkpoint
            
            # Create new checkpoint
            checkpoint = CrawlCheckpoint(
                site_name=site_name,
                city_name=city_name,
                city_slug=city_slug,
                started_at=datetime.utcnow().isoformat(),
                last_updated_at=datetime.utcnow().isoformat(),
                last_url=start_url,
            )
            
            self._checkpoints[key] = checkpoint
            self._save_checkpoint(checkpoint)
            
            logger.info(f"Created new checkpoint: {key}")
            return checkpoint
    
    def update_checkpoint(self, checkpoint: CrawlCheckpoint, **kwargs) -> None:
        """Update and persist checkpoint."""
        with self._lock:
            checkpoint.update(**kwargs)
            self._save_checkpoint(checkpoint)
    
    def add_pending_listings(self, 
                            checkpoint: CrawlCheckpoint, 
                            listings: List[Dict[str, Any]]) -> None:
        """Add listings to pending buffer for recovery."""
        with self._lock:
            checkpoint.pending_listings.extend(listings)
            
            # Trim if too many
            if len(checkpoint.pending_listings) > self._max_pending_listings:
                checkpoint.pending_listings = checkpoint.pending_listings[-self._max_pending_listings:]
            
            checkpoint.listings_extracted += len(listings)
            checkpoint.last_updated_at = datetime.utcnow().isoformat()
            self._save_checkpoint(checkpoint)
    
    def clear_pending_listings(self, checkpoint: CrawlCheckpoint) -> None:
        """Clear pending listings after successful save."""
        with self._lock:
            checkpoint.pending_listings = []
            self._save_checkpoint(checkpoint)
    
    def mark_page_complete(self, checkpoint: CrawlCheckpoint, page_num: int) -> None:
        """Mark a page as completed."""
        with self._lock:
            checkpoint.current_page = page_num + 1
            checkpoint.pages_completed = page_num
            checkpoint.retry_count = 0
            checkpoint.last_error = None
            checkpoint.last_updated_at = datetime.utcnow().isoformat()
            self._save_checkpoint(checkpoint)
    
    def mark_error(self, checkpoint: CrawlCheckpoint, error: str) -> None:
        """Record an error in checkpoint."""
        with self._lock:
            checkpoint.last_error = error
            checkpoint.retry_count += 1
            checkpoint.last_updated_at = datetime.utcnow().isoformat()
            self._save_checkpoint(checkpoint)
    
    def complete_checkpoint(self, checkpoint: CrawlCheckpoint, saved_count: int = 0) -> None:
        """Mark checkpoint as complete and remove it."""
        key = self._get_checkpoint_key(checkpoint.site_name, checkpoint.city_slug)
        
        with self._lock:
            checkpoint.listings_saved = saved_count
            
            # Remove from memory
            if key in self._checkpoints:
                del self._checkpoints[key]
            
            # Remove file
            checkpoint_path = self._get_checkpoint_path(key)
            if checkpoint_path.exists():
                checkpoint_path.unlink()
            
            logger.info(
                f"Completed checkpoint: {key} "
                f"({checkpoint.pages_completed} pages, "
                f"{checkpoint.listings_saved} saved)"
            )
    
    def _save_checkpoint(self, checkpoint: CrawlCheckpoint) -> None:
        """Atomically save checkpoint to disk."""
        key = self._get_checkpoint_key(checkpoint.site_name, checkpoint.city_slug)
        checkpoint_path = self._get_checkpoint_path(key)
        temp_path = checkpoint_path.with_suffix('.tmp')
        
        try:
            # Write to temp file first
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(asdict(checkpoint), f, indent=2, default=str)
            
            # Atomic rename
            temp_path.replace(checkpoint_path)
            
        except Exception as e:
            logger.error(f"Failed to save checkpoint {key}: {e}")
            if temp_path.exists():
                temp_path.unlink()
    
    def get_pending_checkpoints(self, site_name: Optional[str] = None) -> List[CrawlCheckpoint]:
        """Get list of incomplete checkpoints for resumption."""
        checkpoints = []
        
        for key, checkpoint in self._checkpoints.items():
            if site_name and checkpoint.site_name != site_name:
                continue
            checkpoints.append(checkpoint)
        
        # Sort by last updated (oldest first)
        checkpoints.sort(key=lambda c: c.last_updated_at)
        
        return checkpoints
    
    def cleanup_stale_checkpoints(self) -> int:
        """Remove checkpoints older than stale_hours."""
        removed = 0
        stale_cutoff = datetime.utcnow().timestamp() - (self._stale_hours * 3600)
        
        with self._lock:
            keys_to_remove = []
            
            for key, checkpoint in self._checkpoints.items():
                try:
                    last_updated = datetime.fromisoformat(checkpoint.last_updated_at)
                    if last_updated.timestamp() < stale_cutoff:
                        keys_to_remove.append(key)
                except Exception:
                    continue
            
            for key in keys_to_remove:
                del self._checkpoints[key]
                checkpoint_path = self._get_checkpoint_path(key)
                if checkpoint_path.exists():
                    checkpoint_path.unlink()
                removed += 1
                logger.info(f"Removed stale checkpoint: {key}")
        
        return removed
    
    def _cleanup_on_exit(self) -> None:
        """Save all checkpoints on program exit."""
        with self._lock:
            for checkpoint in self._checkpoints.values():
                try:
                    self._save_checkpoint(checkpoint)
                except Exception:
                    pass
    
    def get_stats(self) -> Dict[str, Any]:
        """Get checkpoint statistics."""
        return {
            "active_checkpoints": len(self._checkpoints),
            "checkpoints": [
                {
                    "site": c.site_name,
                    "city": c.city_name,
                    "page": c.current_page,
                    "listings": c.listings_extracted,
                    "pending": len(c.pending_listings),
                    "last_updated": c.last_updated_at,
                }
                for c in self._checkpoints.values()
            ],
        }


class BrowserWatchdog:
    """
    Watchdog to detect and recover from browser freezes.
    """
    
    def __init__(self, timeout_seconds: int = 120):
        self.timeout_seconds = timeout_seconds
        self._last_activity = time.time()
        self._is_frozen = False
        self._lock = Lock()
    
    def heartbeat(self) -> None:
        """Record activity heartbeat."""
        with self._lock:
            self._last_activity = time.time()
            self._is_frozen = False
    
    def check(self) -> bool:
        """
        Check if browser appears frozen.
        Returns True if frozen.
        """
        with self._lock:
            elapsed = time.time() - self._last_activity
            
            if elapsed > self.timeout_seconds:
                self._is_frozen = True
                return True
            
            return False
    
    @property
    def is_frozen(self) -> bool:
        return self._is_frozen
    
    @property
    def seconds_since_activity(self) -> float:
        return time.time() - self._last_activity
    
    def reset(self) -> None:
        """Reset watchdog state."""
        with self._lock:
            self._last_activity = time.time()
            self._is_frozen = False


# Singleton instance
_checkpoint_manager: Optional[CheckpointManager] = None


def get_checkpoint_manager() -> CheckpointManager:
    """Get singleton checkpoint manager instance."""
    global _checkpoint_manager
    if _checkpoint_manager is None:
        _checkpoint_manager = CheckpointManager()
    return _checkpoint_manager
