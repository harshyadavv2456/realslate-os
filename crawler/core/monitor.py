"""
RealSlate Core - Monitoring and Alerting
Tracks crawl health, metrics, and sends alerts.
"""

import os
import json
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, asdict
from enum import Enum
import threading

from loguru import logger

try:
    from prometheus_client import Counter, Gauge, Histogram, start_http_server
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

from .utils import get_config


class AlertLevel(Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class CrawlMetrics:
    """Crawl session metrics."""
    session_id: str
    site: str
    city: str = None
    started_at: datetime = None
    ended_at: datetime = None
    
    pages_crawled: int = 0
    listings_found: int = 0
    listings_valid: int = 0
    listings_duplicate: int = 0
    errors: int = 0
    
    # Rates
    success_rate: float = 0.0
    completeness_rate: float = 0.0
    duplicate_rate: float = 0.0
    
    # Timing
    avg_page_time_seconds: float = 0.0
    total_time_seconds: float = 0.0


@dataclass
class HealthStatus:
    """System health status."""
    timestamp: datetime
    status: str  # healthy, degraded, unhealthy
    
    # Component status
    database_ok: bool = True
    browser_ok: bool = True
    scheduler_ok: bool = True
    
    # Metrics
    error_rate: float = 0.0
    completeness_rate: float = 0.0
    
    # Issues
    issues: List[str] = None
    
    def __post_init__(self):
        if self.issues is None:
            self.issues = []


class CrawlMonitor:
    """
    Production-grade monitoring with:
    - Real-time metrics collection
    - Prometheus metrics export
    - Health checks
    - Alerting (Telegram, Email)
    - Daily reports
    """
    
    def __init__(self, config: Optional[dict] = None):
        self.config = config or get_config()
        self.monitor_config = self.config.get("monitoring", {})
        
        # Metrics storage
        self._current_metrics: Dict[str, CrawlMetrics] = {}
        self._historical_metrics: List[CrawlMetrics] = []
        self._daily_summaries: Dict[str, Dict[str, Any]] = {}
        
        # Health tracking
        self._last_health_check: Optional[HealthStatus] = None
        self._health_history: List[HealthStatus] = []
        
        # Error tracking
        self._recent_errors: List[Dict[str, Any]] = []
        self._error_counts: Dict[str, int] = {}
        
        # Prometheus metrics
        self._prometheus_started = False
        self._setup_prometheus_metrics()
        
        # Alert state
        self._last_alert_time: Dict[str, datetime] = {}
        self._alert_cooldown_minutes = 30
    
    def _setup_prometheus_metrics(self) -> None:
        """Setup Prometheus metrics if available."""
        if not PROMETHEUS_AVAILABLE:
            return
        
        if not self.monitor_config.get("prometheus_enabled", True):
            return
        
        # Counters
        self._prom_pages_crawled = Counter(
            'realslate_pages_crawled_total',
            'Total pages crawled',
            ['site', 'city']
        )
        
        self._prom_listings_found = Counter(
            'realslate_listings_found_total',
            'Total listings found',
            ['site', 'city']
        )
        
        self._prom_errors = Counter(
            'realslate_errors_total',
            'Total errors',
            ['site', 'error_type']
        )
        
        # Gauges
        self._prom_active_crawls = Gauge(
            'realslate_active_crawls',
            'Number of active crawl sessions'
        )
        
        self._prom_success_rate = Gauge(
            'realslate_success_rate',
            'Current crawl success rate',
            ['site']
        )
        
        self._prom_completeness_rate = Gauge(
            'realslate_completeness_rate',
            'Data completeness rate',
            ['site']
        )
        
        # Histograms
        self._prom_page_duration = Histogram(
            'realslate_page_duration_seconds',
            'Time to crawl a page',
            ['site'],
            buckets=[1, 2, 5, 10, 20, 30, 60]
        )
    
    def start_prometheus_server(self) -> None:
        """Start Prometheus metrics server."""
        if not PROMETHEUS_AVAILABLE:
            logger.warning("Prometheus client not available")
            return
        
        if self._prometheus_started:
            return
        
        port = int(self.monitor_config.get("prometheus_port", 9090))
        
        try:
            start_http_server(port)
            self._prometheus_started = True
            logger.info(f"Prometheus metrics server started on port {port}")
        except Exception as e:
            logger.error(f"Failed to start Prometheus server: {e}")
    
    def start_session(
        self,
        session_id: str,
        site: str,
        city: str = None
    ) -> CrawlMetrics:
        """Start tracking a crawl session."""
        metrics = CrawlMetrics(
            session_id=session_id,
            site=site,
            city=city,
            started_at=datetime.utcnow(),
        )
        
        self._current_metrics[session_id] = metrics
        
        if PROMETHEUS_AVAILABLE:
            self._prom_active_crawls.inc()
        
        logger.info(f"Started monitoring session: {session_id}")
        
        return metrics
    
    def end_session(self, session_id: str) -> Optional[CrawlMetrics]:
        """End a crawl session and calculate final metrics."""
        metrics = self._current_metrics.pop(session_id, None)
        
        if not metrics:
            logger.warning(f"Session not found: {session_id}")
            return None
        
        metrics.ended_at = datetime.utcnow()
        
        if metrics.started_at:
            metrics.total_time_seconds = (
                metrics.ended_at - metrics.started_at
            ).total_seconds()
        
        if metrics.pages_crawled > 0:
            metrics.avg_page_time_seconds = (
                metrics.total_time_seconds / metrics.pages_crawled
            )
        
        # Calculate rates
        if metrics.pages_crawled > 0:
            metrics.success_rate = (
                (metrics.pages_crawled - metrics.errors) / metrics.pages_crawled
            )
        
        if metrics.listings_found > 0:
            metrics.completeness_rate = metrics.listings_valid / metrics.listings_found
            metrics.duplicate_rate = metrics.listings_duplicate / metrics.listings_found
        
        self._historical_metrics.append(metrics)
        
        # Update Prometheus
        if PROMETHEUS_AVAILABLE:
            self._prom_active_crawls.dec()
            self._prom_success_rate.labels(site=metrics.site).set(metrics.success_rate)
            self._prom_completeness_rate.labels(site=metrics.site).set(metrics.completeness_rate)
        
        logger.info(
            f"Session {session_id} completed: {metrics.pages_crawled} pages, "
            f"{metrics.listings_found} listings, {metrics.errors} errors"
        )
        
        # Check for alerts
        self._check_session_alerts(metrics)
        
        return metrics
    
    def record_page(
        self,
        session_id: str,
        success: bool = True,
        duration_seconds: float = 0,
        listings_count: int = 0,
        valid_count: int = 0,
        duplicate_count: int = 0,
        error: str = None
    ) -> None:
        """Record a page crawl."""
        metrics = self._current_metrics.get(session_id)
        
        if not metrics:
            return
        
        metrics.pages_crawled += 1
        metrics.listings_found += listings_count
        metrics.listings_valid += valid_count
        metrics.listings_duplicate += duplicate_count
        
        if not success:
            metrics.errors += 1
        
        # Update Prometheus
        if PROMETHEUS_AVAILABLE:
            self._prom_pages_crawled.labels(
                site=metrics.site,
                city=metrics.city or 'unknown'
            ).inc()
            
            self._prom_listings_found.labels(
                site=metrics.site,
                city=metrics.city or 'unknown'
            ).inc(listings_count)
            
            if duration_seconds > 0:
                self._prom_page_duration.labels(site=metrics.site).observe(duration_seconds)
            
            if error:
                error_type = error.split(":")[0] if ":" in error else "unknown"
                self._prom_errors.labels(site=metrics.site, error_type=error_type).inc()
        
        # Track errors
        if error:
            self._record_error(metrics.site, error)
    
    def _record_error(self, site: str, error: str) -> None:
        """Record an error."""
        error_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "site": site,
            "error": error,
        }
        
        self._recent_errors.append(error_entry)
        
        # Keep only last 100 errors
        if len(self._recent_errors) > 100:
            self._recent_errors = self._recent_errors[-100:]
        
        # Count error types
        error_type = error.split(":")[0] if ":" in error else error
        self._error_counts[error_type] = self._error_counts.get(error_type, 0) + 1
    
    def _check_session_alerts(self, metrics: CrawlMetrics) -> None:
        """Check if session metrics trigger alerts."""
        error_threshold = self.monitor_config.get("error_rate_threshold", 0.2)
        completeness_threshold = self.monitor_config.get("completeness_threshold", 0.6)
        
        if metrics.pages_crawled > 0 and metrics.success_rate < (1 - error_threshold):
            self.send_alert(
                AlertLevel.WARNING,
                f"High error rate for {metrics.site}",
                f"Error rate: {(1 - metrics.success_rate) * 100:.1f}%\n"
                f"Pages: {metrics.pages_crawled}, Errors: {metrics.errors}"
            )
        
        if metrics.listings_found > 0 and metrics.completeness_rate < completeness_threshold:
            self.send_alert(
                AlertLevel.WARNING,
                f"Low data completeness for {metrics.site}",
                f"Completeness: {metrics.completeness_rate * 100:.1f}%\n"
                f"Valid: {metrics.listings_valid}/{metrics.listings_found}"
            )
    
    def check_health(self, db_manager=None, browser_manager=None, scheduler=None) -> HealthStatus:
        """Perform system health check."""
        status = HealthStatus(
            timestamp=datetime.utcnow(),
            status="healthy",
        )
        
        # Check database
        if db_manager:
            try:
                with db_manager.session() as session:
                    from sqlalchemy import text
                    session.execute(text("SELECT 1"))
                status.database_ok = True
            except Exception as e:
                status.database_ok = False
                status.issues.append(f"Database error: {e}")
        
        # Check browser
        if browser_manager:
            status.browser_ok = browser_manager.is_initialized
            if not status.browser_ok:
                status.issues.append("Browser not initialized")
        
        # Check scheduler
        if scheduler:
            queue_stats = scheduler.get_queue_stats()
            status.scheduler_ok = queue_stats.get("running", 0) < 10
            if not status.scheduler_ok:
                status.issues.append("Too many running tasks")
        
        # Calculate overall metrics
        recent_sessions = [
            m for m in self._historical_metrics
            if m.ended_at and (datetime.utcnow() - m.ended_at).total_seconds() < 86400
        ]
        
        if recent_sessions:
            total_pages = sum(m.pages_crawled for m in recent_sessions)
            total_errors = sum(m.errors for m in recent_sessions)
            total_listings = sum(m.listings_found for m in recent_sessions)
            total_valid = sum(m.listings_valid for m in recent_sessions)
            
            if total_pages > 0:
                status.error_rate = total_errors / total_pages
            
            if total_listings > 0:
                status.completeness_rate = total_valid / total_listings
        
        # Determine overall status
        if not status.database_ok or not status.browser_ok:
            status.status = "unhealthy"
        elif status.issues or status.error_rate > 0.3:
            status.status = "degraded"
        
        self._last_health_check = status
        self._health_history.append(status)
        
        # Keep only last 100 health checks
        if len(self._health_history) > 100:
            self._health_history = self._health_history[-100:]
        
        return status
    
    def generate_daily_summary(self) -> Dict[str, Any]:
        """Generate daily summary report."""
        today = datetime.utcnow().date()
        today_str = today.isoformat()
        
        # Get today's sessions
        today_sessions = [
            m for m in self._historical_metrics
            if m.ended_at and m.ended_at.date() == today
        ]
        
        summary = {
            "date": today_str,
            "generated_at": datetime.utcnow().isoformat(),
            "sessions_count": len(today_sessions),
            "sites_crawled": list(set(m.site for m in today_sessions)),
            "totals": {
                "pages_crawled": sum(m.pages_crawled for m in today_sessions),
                "listings_found": sum(m.listings_found for m in today_sessions),
                "listings_valid": sum(m.listings_valid for m in today_sessions),
                "listings_duplicate": sum(m.listings_duplicate for m in today_sessions),
                "errors": sum(m.errors for m in today_sessions),
            },
            "rates": {},
            "by_site": {},
        }
        
        # Calculate rates
        totals = summary["totals"]
        if totals["pages_crawled"] > 0:
            summary["rates"]["success_rate"] = (
                (totals["pages_crawled"] - totals["errors"]) / totals["pages_crawled"]
            )
        
        if totals["listings_found"] > 0:
            summary["rates"]["completeness_rate"] = totals["listings_valid"] / totals["listings_found"]
            summary["rates"]["duplicate_rate"] = totals["listings_duplicate"] / totals["listings_found"]
        
        # By site breakdown
        sites = set(m.site for m in today_sessions)
        for site in sites:
            site_sessions = [m for m in today_sessions if m.site == site]
            summary["by_site"][site] = {
                "sessions": len(site_sessions),
                "pages": sum(m.pages_crawled for m in site_sessions),
                "listings": sum(m.listings_found for m in site_sessions),
                "errors": sum(m.errors for m in site_sessions),
            }
        
        # Health status
        if self._last_health_check:
            summary["health_status"] = self._last_health_check.status
            summary["health_issues"] = self._last_health_check.issues
        
        # Top errors
        summary["top_errors"] = dict(
            sorted(self._error_counts.items(), key=lambda x: x[1], reverse=True)[:10]
        )
        
        self._daily_summaries[today_str] = summary
        
        return summary
    
    def send_alert(
        self,
        level: AlertLevel,
        title: str,
        message: str,
        force: bool = False
    ) -> bool:
        """Send alert through configured channels."""
        if not self.monitor_config.get("alerts_enabled", True):
            return False
        
        # Check cooldown
        alert_key = f"{level.value}:{title}"
        last_time = self._last_alert_time.get(alert_key)
        
        if not force and last_time:
            elapsed = (datetime.utcnow() - last_time).total_seconds() / 60
            if elapsed < self._alert_cooldown_minutes:
                return False
        
        self._last_alert_time[alert_key] = datetime.utcnow()
        
        # Log alert
        log_method = {
            AlertLevel.INFO: logger.info,
            AlertLevel.WARNING: logger.warning,
            AlertLevel.ERROR: logger.error,
            AlertLevel.CRITICAL: logger.critical,
        }.get(level, logger.warning)
        
        log_method(f"ALERT [{level.value.upper()}] {title}: {message}")
        
        # Send to Telegram
        if self._send_telegram_alert(level, title, message):
            logger.debug("Telegram alert sent")
        
        # Send email
        if self._send_email_alert(level, title, message):
            logger.debug("Email alert sent")
        
        return True
    
    def _send_telegram_alert(
        self,
        level: AlertLevel,
        title: str,
        message: str
    ) -> bool:
        """Send alert via Telegram."""
        if not REQUESTS_AVAILABLE:
            return False
        
        telegram_config = self.monitor_config.get("alert_channels", {}).get("telegram", {})
        
        if str(telegram_config.get("enabled")).lower() not in ("true", "1", "yes"):
            return False
        
        bot_token = telegram_config.get("bot_token")
        chat_id = telegram_config.get("chat_id")
        
        if not bot_token or not chat_id:
            return False
        
        emoji = {
            AlertLevel.INFO: "ℹ️",
            AlertLevel.WARNING: "⚠️",
            AlertLevel.ERROR: "❌",
            AlertLevel.CRITICAL: "🚨",
        }.get(level, "📢")
        
        text = f"{emoji} *RealSlate Alert*\n\n*{title}*\n\n{message}"
        
        try:
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            response = requests.post(
                url,
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "Markdown",
                },
                timeout=10
            )
            return response.status_code == 200
        except Exception as e:
            logger.warning(f"Telegram alert failed: {e}")
            return False
    
    def _send_email_alert(
        self,
        level: AlertLevel,
        title: str,
        message: str
    ) -> bool:
        """Send alert via email (placeholder)."""
        email_config = self.monitor_config.get("alert_channels", {}).get("email", {})
        
        if str(email_config.get("enabled")).lower() not in ("true", "1", "yes"):
            return False
        
        host = email_config.get("smtp_host")
        sender = email_config.get("sender")
        recipients = email_config.get("recipients") or []
        if isinstance(recipients, str):
            recipients = [r.strip() for r in recipients.split(",") if r.strip()]
        if not host or not sender or not recipients:
            return False

        try:
            import smtplib
            from email.message import EmailMessage

            msg = EmailMessage()
            msg["Subject"] = f"[RealSlate {level.value.upper()}] {title}"
            msg["From"] = sender
            msg["To"] = ", ".join(recipients)
            msg.set_content(message)

            with smtplib.SMTP(host, int(email_config.get("smtp_port") or 587), timeout=15) as smtp:
                smtp.starttls()
                if email_config.get("smtp_user"):
                    smtp.login(email_config["smtp_user"], email_config.get("smtp_password", ""))
                smtp.send_message(msg)
            return True
        except Exception as e:
            logger.warning(f"Email alert failed: {e}")
            return False
    
    def get_metrics_summary(self) -> Dict[str, Any]:
        """Get current metrics summary."""
        return {
            "active_sessions": len(self._current_metrics),
            "completed_sessions": len(self._historical_metrics),
            "recent_errors": len(self._recent_errors),
            "error_types": dict(self._error_counts),
            "last_health_check": asdict(self._last_health_check) if self._last_health_check else None,
        }
    
    def export_metrics(self, filepath: str) -> None:
        """Export metrics to JSON file."""
        data = {
            "exported_at": datetime.utcnow().isoformat(),
            "historical_metrics": [asdict(m) for m in self._historical_metrics],
            "daily_summaries": self._daily_summaries,
            "error_counts": self._error_counts,
        }
        
        # Convert datetime objects to strings
        for m in data["historical_metrics"]:
            for key in ["started_at", "ended_at"]:
                if m.get(key) and isinstance(m[key], datetime):
                    m[key] = m[key].isoformat()
        
        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2, default=str)
        
        logger.info(f"Metrics exported to {filepath}")
    
    def reset_daily_stats(self) -> None:
        """Reset daily statistics."""
        self._error_counts.clear()
        self._recent_errors.clear()
