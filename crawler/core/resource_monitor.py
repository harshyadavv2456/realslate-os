"""
RealSlate Core - System Resource Monitor
Monitors system resources (memory, disk, CPU) and triggers alerts/actions.
"""

import os
import gc
import time
import threading
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Callable
from dataclasses import dataclass, asdict
from pathlib import Path

from loguru import logger

from .utils import get_config

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False


@dataclass
class ResourceSnapshot:
    """Point-in-time resource snapshot."""
    timestamp: datetime
    
    # Memory
    memory_rss_mb: float = 0.0
    memory_vms_mb: float = 0.0
    memory_percent: float = 0.0
    system_memory_percent: float = 0.0
    
    # CPU
    cpu_percent: float = 0.0
    system_cpu_percent: float = 0.0
    
    # Disk
    disk_free_gb: float = 0.0
    disk_percent: float = 0.0
    
    # Process
    open_files: int = 0
    threads: int = 0
    connections: int = 0


@dataclass
class ResourceAlert:
    """Resource alert."""
    timestamp: datetime
    alert_type: str  # memory_high, disk_low, cpu_high
    severity: str  # warning, critical
    message: str
    value: float
    threshold: float


class ResourceMonitor:
    """
    Production-grade resource monitoring with:
    - Continuous background monitoring
    - Threshold-based alerts
    - Memory leak detection
    - Automatic cleanup actions
    - Resource history tracking
    """
    
    def __init__(self, config: Optional[dict] = None):
        self.config = config or get_config()
        self.monitor_config = self.config.get("monitoring", {})
        
        # Thresholds
        self._memory_warning_percent = self.monitor_config.get("memory_warning_percent", 80)
        self._memory_critical_percent = 90
        self._disk_warning_gb = self.monitor_config.get("disk_warning_gb", 2)
        self._disk_critical_gb = 1
        self._cpu_warning_percent = 90
        
        # For low-RAM VPS optimization
        self._memory_limit_mb = self.config.get("crawling", {}).get("memory_limit_mb", 1400)
        
        # State
        self._history: List[ResourceSnapshot] = []
        self._alerts: List[ResourceAlert] = []
        self._max_history_size = 1000
        self._baseline_memory: Optional[float] = None
        
        # Background monitoring
        self._monitoring = False
        self._monitor_thread: Optional[threading.Thread] = None
        self._monitor_interval = 60  # seconds
        
        # Callbacks
        self._alert_callbacks: List[Callable[[ResourceAlert], None]] = []
        self._action_callbacks: Dict[str, Callable[[], None]] = {}
    
    @property
    def is_available(self) -> bool:
        """Check if resource monitoring is available."""
        return PSUTIL_AVAILABLE
    
    def capture_snapshot(self) -> Optional[ResourceSnapshot]:
        """Capture current resource state."""
        if not PSUTIL_AVAILABLE:
            return None
        
        try:
            process = psutil.Process()
            memory_info = process.memory_info()
            
            # Get disk usage for app directory
            app_path = Path(__file__).parent.parent
            disk = psutil.disk_usage(str(app_path))
            
            snapshot = ResourceSnapshot(
                timestamp=datetime.utcnow(),
                memory_rss_mb=memory_info.rss / (1024 * 1024),
                memory_vms_mb=memory_info.vms / (1024 * 1024),
                memory_percent=process.memory_percent(),
                system_memory_percent=psutil.virtual_memory().percent,
                cpu_percent=process.cpu_percent(interval=0.1),
                system_cpu_percent=psutil.cpu_percent(interval=0.1),
                disk_free_gb=disk.free / (1024**3),
                disk_percent=disk.percent,
                open_files=len(process.open_files()),
                threads=process.num_threads(),
                connections=len(process.connections()),
            )
            
            # Store in history
            self._history.append(snapshot)
            if len(self._history) > self._max_history_size:
                self._history = self._history[-self._max_history_size:]
            
            # Set baseline on first capture
            if self._baseline_memory is None:
                self._baseline_memory = snapshot.memory_rss_mb
            
            return snapshot
            
        except Exception as e:
            logger.debug(f"Failed to capture resource snapshot: {e}")
            return None
    
    def check_thresholds(self, snapshot: ResourceSnapshot) -> List[ResourceAlert]:
        """Check snapshot against thresholds and generate alerts."""
        alerts = []
        
        # Memory checks
        if snapshot.memory_rss_mb > self._memory_limit_mb:
            alerts.append(ResourceAlert(
                timestamp=snapshot.timestamp,
                alert_type="memory_high",
                severity="critical",
                message=f"Process memory ({snapshot.memory_rss_mb:.1f}MB) exceeds limit ({self._memory_limit_mb}MB)",
                value=snapshot.memory_rss_mb,
                threshold=self._memory_limit_mb,
            ))
        elif snapshot.system_memory_percent > self._memory_warning_percent:
            alerts.append(ResourceAlert(
                timestamp=snapshot.timestamp,
                alert_type="memory_high",
                severity="warning",
                message=f"System memory usage at {snapshot.system_memory_percent:.1f}%",
                value=snapshot.system_memory_percent,
                threshold=self._memory_warning_percent,
            ))
        
        # Disk checks
        if snapshot.disk_free_gb < self._disk_critical_gb:
            alerts.append(ResourceAlert(
                timestamp=snapshot.timestamp,
                alert_type="disk_low",
                severity="critical",
                message=f"Disk space critically low: {snapshot.disk_free_gb:.2f}GB free",
                value=snapshot.disk_free_gb,
                threshold=self._disk_critical_gb,
            ))
        elif snapshot.disk_free_gb < self._disk_warning_gb:
            alerts.append(ResourceAlert(
                timestamp=snapshot.timestamp,
                alert_type="disk_low",
                severity="warning",
                message=f"Disk space low: {snapshot.disk_free_gb:.2f}GB free",
                value=snapshot.disk_free_gb,
                threshold=self._disk_warning_gb,
            ))
        
        # CPU checks
        if snapshot.cpu_percent > self._cpu_warning_percent:
            alerts.append(ResourceAlert(
                timestamp=snapshot.timestamp,
                alert_type="cpu_high",
                severity="warning",
                message=f"Process CPU usage high: {snapshot.cpu_percent:.1f}%",
                value=snapshot.cpu_percent,
                threshold=self._cpu_warning_percent,
            ))
        
        # Store alerts
        for alert in alerts:
            self._alerts.append(alert)
            
            # Trigger callbacks
            for callback in self._alert_callbacks:
                try:
                    callback(alert)
                except Exception as e:
                    logger.warning(f"Alert callback error: {e}")
            
            # Trigger automatic actions
            self._handle_alert(alert)
        
        return alerts
    
    def _handle_alert(self, alert: ResourceAlert) -> None:
        """Handle alert with automatic actions."""
        if alert.alert_type == "memory_high" and alert.severity == "critical":
            # Force garbage collection
            logger.warning("Critical memory alert - forcing garbage collection")
            gc.collect()
            
            # Trigger custom action if registered
            if "memory_cleanup" in self._action_callbacks:
                try:
                    self._action_callbacks["memory_cleanup"]()
                except Exception as e:
                    logger.error(f"Memory cleanup action failed: {e}")
    
    def detect_memory_leak(self, window_minutes: int = 30) -> Optional[Dict[str, Any]]:
        """
        Detect potential memory leaks by analyzing growth trend.
        """
        if len(self._history) < 10:
            return None
        
        cutoff = datetime.utcnow() - timedelta(minutes=window_minutes)
        recent = [s for s in self._history if s.timestamp > cutoff]
        
        if len(recent) < 5:
            return None
        
        # Calculate memory growth
        first_memory = recent[0].memory_rss_mb
        last_memory = recent[-1].memory_rss_mb
        growth = last_memory - first_memory
        
        # Calculate growth rate
        duration_minutes = (recent[-1].timestamp - recent[0].timestamp).total_seconds() / 60
        growth_rate_per_hour = (growth / duration_minutes) * 60 if duration_minutes > 0 else 0
        
        # Leak detection: consistent growth > 10MB/hour
        is_leak = growth_rate_per_hour > 10 and growth > 50
        
        return {
            "baseline_mb": self._baseline_memory,
            "current_mb": last_memory,
            "growth_mb": growth,
            "growth_rate_mb_per_hour": growth_rate_per_hour,
            "is_potential_leak": is_leak,
            "samples": len(recent),
            "window_minutes": window_minutes,
        }
    
    def register_alert_callback(self, callback: Callable[[ResourceAlert], None]) -> None:
        """Register callback for alerts."""
        self._alert_callbacks.append(callback)
    
    def register_action(self, action_name: str, callback: Callable[[], None]) -> None:
        """Register automatic action for specific conditions."""
        self._action_callbacks[action_name] = callback
    
    def start_monitoring(self, interval: int = 60) -> None:
        """Start background monitoring thread."""
        if self._monitoring:
            return
        
        if not PSUTIL_AVAILABLE:
            logger.warning("psutil not available, background monitoring disabled")
            return
        
        self._monitor_interval = interval
        self._monitoring = True
        self._monitor_thread = threading.Thread(
            target=self._monitoring_loop,
            daemon=True,
            name="ResourceMonitor"
        )
        self._monitor_thread.start()
        logger.info(f"Started background resource monitoring (interval: {interval}s)")
    
    def stop_monitoring(self) -> None:
        """Stop background monitoring."""
        self._monitoring = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=5)
        logger.info("Stopped background resource monitoring")
    
    def _monitoring_loop(self) -> None:
        """Background monitoring loop."""
        while self._monitoring:
            try:
                snapshot = self.capture_snapshot()
                if snapshot:
                    self.check_thresholds(snapshot)
                
                # Check for memory leaks periodically
                leak_info = self.detect_memory_leak()
                if leak_info and leak_info.get("is_potential_leak"):
                    logger.warning(
                        f"Potential memory leak detected: "
                        f"{leak_info['growth_rate_mb_per_hour']:.1f} MB/hour growth"
                    )
                
            except Exception as e:
                logger.debug(f"Monitoring loop error: {e}")
            
            time.sleep(self._monitor_interval)
    
    def get_current_status(self) -> Dict[str, Any]:
        """Get current resource status."""
        snapshot = self.capture_snapshot()
        
        if not snapshot:
            return {"available": False}
        
        return {
            "available": True,
            "timestamp": snapshot.timestamp.isoformat(),
            "memory": {
                "process_mb": snapshot.memory_rss_mb,
                "process_percent": snapshot.memory_percent,
                "system_percent": snapshot.system_memory_percent,
                "limit_mb": self._memory_limit_mb,
            },
            "disk": {
                "free_gb": snapshot.disk_free_gb,
                "used_percent": snapshot.disk_percent,
            },
            "cpu": {
                "process_percent": snapshot.cpu_percent,
                "system_percent": snapshot.system_cpu_percent,
            },
            "process": {
                "threads": snapshot.threads,
                "open_files": snapshot.open_files,
                "connections": snapshot.connections,
            },
        }
    
    def get_history_stats(self, minutes: int = 60) -> Dict[str, Any]:
        """Get statistics over recent history."""
        cutoff = datetime.utcnow() - timedelta(minutes=minutes)
        recent = [s for s in self._history if s.timestamp > cutoff]
        
        if not recent:
            return {"samples": 0}
        
        memory_values = [s.memory_rss_mb for s in recent]
        cpu_values = [s.cpu_percent for s in recent]
        
        return {
            "samples": len(recent),
            "window_minutes": minutes,
            "memory": {
                "min_mb": min(memory_values),
                "max_mb": max(memory_values),
                "avg_mb": sum(memory_values) / len(memory_values),
                "current_mb": memory_values[-1],
            },
            "cpu": {
                "min_percent": min(cpu_values),
                "max_percent": max(cpu_values),
                "avg_percent": sum(cpu_values) / len(cpu_values),
            },
        }
    
    def get_recent_alerts(self, count: int = 10) -> List[Dict[str, Any]]:
        """Get recent alerts."""
        return [asdict(a) for a in self._alerts[-count:]]
    
    def force_cleanup(self) -> Dict[str, Any]:
        """Force memory cleanup and return results."""
        before = self.capture_snapshot()
        
        # Force garbage collection
        gc.collect()
        
        after = self.capture_snapshot()
        
        freed = 0
        if before and after:
            freed = before.memory_rss_mb - after.memory_rss_mb
        
        return {
            "before_mb": before.memory_rss_mb if before else 0,
            "after_mb": after.memory_rss_mb if after else 0,
            "freed_mb": freed,
            "timestamp": datetime.utcnow().isoformat(),
        }


# Singleton instance
_resource_monitor: Optional[ResourceMonitor] = None


def get_resource_monitor() -> ResourceMonitor:
    """Get singleton resource monitor instance."""
    global _resource_monitor
    if _resource_monitor is None:
        _resource_monitor = ResourceMonitor()
    return _resource_monitor
