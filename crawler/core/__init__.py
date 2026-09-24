"""
RealSlate Core - Production-grade Pan-India Real Estate Data Engine
Focused on: 99acres, MagicBricks, NoBroker, Housing.com
"""

__version__ = "1.0.0"
__author__ = "RealSlate Team"

from .browser import BrowserManager
from .extractor import DataExtractor
from .db import DatabaseManager, get_db_session
from .scheduler import CrawlScheduler
from .validator import DataValidator
from .dedup import Deduplicator
from .monitor import CrawlMonitor
from .checkpoint import CheckpointManager, get_checkpoint_manager, BrowserWatchdog
from .resource_monitor import ResourceMonitor, get_resource_monitor
from .identity import PropertyIdentityEngine, get_identity_engine
from .incremental import IncrementalEngine, get_incremental_engine
from .utils import ConfigLoader, setup_logging

__all__ = [
    "BrowserManager",
    "DataExtractor",
    "DatabaseManager",
    "get_db_session",
    "CrawlScheduler",
    "DataValidator",
    "Deduplicator",
    "CrawlMonitor",
    "CheckpointManager",
    "get_checkpoint_manager",
    "BrowserWatchdog",
    "ResourceMonitor",
    "get_resource_monitor",
    "PropertyIdentityEngine",
    "get_identity_engine",
    "IncrementalEngine",
    "get_incremental_engine",
    "ConfigLoader",
    "setup_logging",
]
