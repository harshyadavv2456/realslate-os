"""
RealSlate Core - Proxy Manager
Production-grade rotating proxy pool with health tracking and automatic failover.
"""

import os
import random
import time
import asyncio
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass, field
from collections import defaultdict
from enum import Enum

from loguru import logger

from .utils import get_config


class ProxyProtocol(Enum):
    HTTP = "http"
    HTTPS = "https"
    SOCKS5 = "socks5"


@dataclass
class ProxyStats:
    """Track proxy performance statistics."""
    success_count: int = 0
    failure_count: int = 0
    total_requests: int = 0
    last_used: Optional[datetime] = None
    last_success: Optional[datetime] = None
    last_failure: Optional[datetime] = None
    consecutive_failures: int = 0
    avg_response_time: float = 0.0
    is_banned: bool = False
    ban_until: Optional[datetime] = None
    
    @property
    def success_rate(self) -> float:
        if self.total_requests == 0:
            return 1.0
        return self.success_count / self.total_requests
    
    @property
    def is_healthy(self) -> bool:
        if self.is_banned:
            if self.ban_until and datetime.utcnow() > self.ban_until:
                return True  # Ban expired
            return False
        return self.consecutive_failures < 5


@dataclass
class Proxy:
    """Proxy configuration."""
    server: str
    port: int
    protocol: ProxyProtocol = ProxyProtocol.HTTP
    username: Optional[str] = None
    password: Optional[str] = None
    country: Optional[str] = None
    city: Optional[str] = None
    stats: ProxyStats = field(default_factory=ProxyStats)
    
    @property
    def url(self) -> str:
        """Get proxy URL for Playwright."""
        auth = ""
        if self.username and self.password:
            auth = f"{self.username}:{self.password}@"
        return f"{self.protocol.value}://{auth}{self.server}:{self.port}"
    
    @property
    def playwright_config(self) -> Dict[str, Any]:
        """Get Playwright proxy configuration."""
        config = {"server": f"{self.protocol.value}://{self.server}:{self.port}"}
        if self.username:
            config["username"] = self.username
        if self.password:
            config["password"] = self.password
        return config
    
    def __hash__(self):
        return hash(f"{self.server}:{self.port}")
    
    def __eq__(self, other):
        if not isinstance(other, Proxy):
            return False
        return self.server == other.server and self.port == other.port


class ProxyManager:
    """
    Production-grade proxy pool manager with:
    - Automatic health tracking
    - Rotation strategies
    - Ban detection and recovery
    - Geographic distribution
    """
    
    def __init__(self, config: Optional[dict] = None):
        self.config = config or get_config()
        self.proxy_config = self.config.get("proxy", {})
        
        self._proxies: List[Proxy] = []
        self._proxy_stats: Dict[str, ProxyStats] = {}
        self._current_index = 0
        self._lock = asyncio.Lock()
        
        # Configuration
        self._max_consecutive_failures = self.proxy_config.get("max_failures", 5)
        self._ban_duration_minutes = self.proxy_config.get("ban_duration_minutes", 30)
        self._min_success_rate = self.proxy_config.get("min_success_rate", 0.5)
        self._rotation_strategy = self.proxy_config.get("rotation_strategy", "round_robin")
        
        # Load proxies
        self._load_proxies()
    
    def _load_proxies(self) -> None:
        """Load proxies from configuration and environment."""
        # Load from config
        proxy_list = self.proxy_config.get("proxies", [])
        
        # Load from environment variable (comma-separated)
        env_proxies = os.environ.get("PROXY_LIST", "")
        if env_proxies:
            for proxy_str in env_proxies.split(","):
                proxy_list.append({"url": proxy_str.strip()})
        
        # Load from file
        proxy_file = self.proxy_config.get("proxy_file")
        if proxy_file and os.path.exists(proxy_file):
            with open(proxy_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        proxy_list.append({"url": line})
        
        # Parse proxy configurations
        for proxy_conf in proxy_list:
            proxy = self._parse_proxy(proxy_conf)
            if proxy:
                self._proxies.append(proxy)
                self._proxy_stats[f"{proxy.server}:{proxy.port}"] = proxy.stats
        
        logger.info(f"Loaded {len(self._proxies)} proxies")
    
    def _parse_proxy(self, config: Dict[str, Any]) -> Optional[Proxy]:
        """Parse proxy configuration."""
        try:
            if "url" in config:
                # Parse URL format: protocol://user:pass@host:port
                url = config["url"]
                
                # Extract protocol
                protocol = ProxyProtocol.HTTP
                for p in ProxyProtocol:
                    if url.startswith(f"{p.value}://"):
                        protocol = p
                        url = url[len(f"{p.value}://"):]
                        break
                
                # Extract auth
                username, password = None, None
                if "@" in url:
                    auth, url = url.rsplit("@", 1)
                    if ":" in auth:
                        username, password = auth.split(":", 1)
                
                # Extract host:port
                if ":" in url:
                    server, port = url.rsplit(":", 1)
                    port = int(port)
                else:
                    server = url
                    port = 8080
                
                return Proxy(
                    server=server,
                    port=port,
                    protocol=protocol,
                    username=username,
                    password=password,
                    country=config.get("country"),
                    city=config.get("city"),
                )
            
            # Direct configuration
            return Proxy(
                server=config["server"],
                port=config.get("port", 8080),
                protocol=ProxyProtocol(config.get("protocol", "http")),
                username=config.get("username"),
                password=config.get("password"),
                country=config.get("country"),
                city=config.get("city"),
            )
            
        except Exception as e:
            logger.warning(f"Failed to parse proxy config: {e}")
            return None
    
    @property
    def is_enabled(self) -> bool:
        """Check if proxy rotation is enabled and proxies are available."""
        return self.proxy_config.get("enabled", False) and len(self._proxies) > 0
    
    @property
    def healthy_proxies(self) -> List[Proxy]:
        """Get list of healthy proxies."""
        return [p for p in self._proxies if p.stats.is_healthy]
    
    async def get_proxy(self, 
                        country: Optional[str] = None,
                        exclude: Optional[List[Proxy]] = None) -> Optional[Proxy]:
        """
        Get next proxy based on rotation strategy.
        
        Args:
            country: Prefer proxies from specific country
            exclude: List of proxies to exclude
        """
        if not self.is_enabled:
            return None
        
        async with self._lock:
            candidates = self.healthy_proxies
            
            if not candidates:
                # Reset all bans if no healthy proxies
                self._reset_all_bans()
                candidates = self._proxies
            
            if not candidates:
                return None
            
            # Filter by country if specified
            if country:
                country_proxies = [p for p in candidates if p.country == country]
                if country_proxies:
                    candidates = country_proxies
            
            # Exclude specified proxies
            if exclude:
                candidates = [p for p in candidates if p not in exclude]
            
            if not candidates:
                return None
            
            # Select based on strategy
            if self._rotation_strategy == "round_robin":
                proxy = self._round_robin_select(candidates)
            elif self._rotation_strategy == "random":
                proxy = random.choice(candidates)
            elif self._rotation_strategy == "least_used":
                proxy = self._least_used_select(candidates)
            elif self._rotation_strategy == "best_performance":
                proxy = self._best_performance_select(candidates)
            else:
                proxy = random.choice(candidates)
            
            proxy.stats.last_used = datetime.utcnow()
            return proxy
    
    def _round_robin_select(self, candidates: List[Proxy]) -> Proxy:
        """Round-robin proxy selection."""
        self._current_index = (self._current_index + 1) % len(candidates)
        return candidates[self._current_index]
    
    def _least_used_select(self, candidates: List[Proxy]) -> Proxy:
        """Select least recently used proxy."""
        return min(candidates, key=lambda p: p.stats.total_requests)
    
    def _best_performance_select(self, candidates: List[Proxy]) -> Proxy:
        """Select proxy with best success rate."""
        return max(candidates, key=lambda p: p.stats.success_rate)
    
    def record_success(self, proxy: Proxy, response_time: float = 0) -> None:
        """Record successful request through proxy."""
        if not proxy:
            return
        
        proxy.stats.success_count += 1
        proxy.stats.total_requests += 1
        proxy.stats.last_success = datetime.utcnow()
        proxy.stats.consecutive_failures = 0
        
        # Update average response time
        if response_time > 0:
            n = proxy.stats.total_requests
            proxy.stats.avg_response_time = (
                (proxy.stats.avg_response_time * (n - 1) + response_time) / n
            )
        
        logger.debug(f"Proxy success: {proxy.server}:{proxy.port} ({proxy.stats.success_rate:.1%})")
    
    def record_failure(self, proxy: Proxy, error: str = None) -> None:
        """Record failed request through proxy."""
        if not proxy:
            return
        
        proxy.stats.failure_count += 1
        proxy.stats.total_requests += 1
        proxy.stats.last_failure = datetime.utcnow()
        proxy.stats.consecutive_failures += 1
        
        # Check if should ban
        if proxy.stats.consecutive_failures >= self._max_consecutive_failures:
            self._ban_proxy(proxy)
        
        logger.warning(
            f"Proxy failure: {proxy.server}:{proxy.port} "
            f"({proxy.stats.consecutive_failures} consecutive)"
        )
    
    def _ban_proxy(self, proxy: Proxy) -> None:
        """Ban proxy temporarily."""
        proxy.stats.is_banned = True
        proxy.stats.ban_until = datetime.utcnow() + timedelta(
            minutes=self._ban_duration_minutes
        )
        logger.warning(
            f"Proxy banned: {proxy.server}:{proxy.port} "
            f"until {proxy.stats.ban_until}"
        )
    
    def _reset_all_bans(self) -> None:
        """Reset all proxy bans (emergency recovery)."""
        for proxy in self._proxies:
            proxy.stats.is_banned = False
            proxy.stats.ban_until = None
            proxy.stats.consecutive_failures = 0
        logger.info("Reset all proxy bans (emergency recovery)")
    
    def unban_proxy(self, proxy: Proxy) -> None:
        """Manually unban a proxy."""
        proxy.stats.is_banned = False
        proxy.stats.ban_until = None
        proxy.stats.consecutive_failures = 0
    
    def add_proxy(self, proxy: Proxy) -> None:
        """Add a proxy to the pool."""
        if proxy not in self._proxies:
            self._proxies.append(proxy)
            self._proxy_stats[f"{proxy.server}:{proxy.port}"] = proxy.stats
            logger.info(f"Added proxy: {proxy.server}:{proxy.port}")
    
    def remove_proxy(self, proxy: Proxy) -> None:
        """Remove a proxy from the pool."""
        if proxy in self._proxies:
            self._proxies.remove(proxy)
            del self._proxy_stats[f"{proxy.server}:{proxy.port}"]
            logger.info(f"Removed proxy: {proxy.server}:{proxy.port}")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get proxy pool statistics."""
        healthy = self.healthy_proxies
        banned = [p for p in self._proxies if p.stats.is_banned]
        
        total_success = sum(p.stats.success_count for p in self._proxies)
        total_requests = sum(p.stats.total_requests for p in self._proxies)
        
        return {
            "total_proxies": len(self._proxies),
            "healthy_proxies": len(healthy),
            "banned_proxies": len(banned),
            "total_requests": total_requests,
            "total_success": total_success,
            "overall_success_rate": total_success / max(1, total_requests),
            "proxies": [
                {
                    "server": f"{p.server}:{p.port}",
                    "success_rate": p.stats.success_rate,
                    "total_requests": p.stats.total_requests,
                    "is_healthy": p.stats.is_healthy,
                    "is_banned": p.stats.is_banned,
                }
                for p in self._proxies
            ],
        }
    
    async def health_check(self, test_url: str = "https://httpbin.org/ip") -> Dict[str, Any]:
        """Test all proxies and return health report."""
        import httpx
        
        results = {}
        
        for proxy in self._proxies:
            key = f"{proxy.server}:{proxy.port}"
            
            try:
                start = time.time()
                async with httpx.AsyncClient(
                    proxies={"all://": proxy.url},
                    timeout=10.0
                ) as client:
                    response = await client.get(test_url)
                    elapsed = time.time() - start
                    
                    results[key] = {
                        "status": "ok",
                        "response_time": elapsed,
                        "status_code": response.status_code,
                    }
                    self.record_success(proxy, elapsed)
                    
            except Exception as e:
                results[key] = {
                    "status": "error",
                    "error": str(e),
                }
                self.record_failure(proxy, str(e))
        
        return results


class DynamicThrottler:
    """
    Dynamic request throttling based on error rates.
    Automatically increases delays when errors spike.
    """
    
    def __init__(self, 
                 base_delay: float = 5.0,
                 max_delay: float = 60.0,
                 error_threshold: float = 0.2):
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.error_threshold = error_threshold
        
        self._recent_results: List[Tuple[datetime, bool]] = []
        self._window_size = 20  # Number of requests to consider
        self._current_multiplier = 1.0
    
    def record_result(self, success: bool) -> None:
        """Record request result."""
        self._recent_results.append((datetime.utcnow(), success))
        
        # Keep only recent results
        if len(self._recent_results) > self._window_size * 2:
            self._recent_results = self._recent_results[-self._window_size:]
        
        # Adjust multiplier based on error rate
        self._adjust_multiplier()
    
    def _adjust_multiplier(self) -> None:
        """Adjust delay multiplier based on recent error rate."""
        if len(self._recent_results) < 5:
            return
        
        recent = self._recent_results[-self._window_size:]
        error_rate = sum(1 for _, success in recent if not success) / len(recent)
        
        if error_rate > self.error_threshold:
            # Increase delay
            self._current_multiplier = min(
                self._current_multiplier * 1.5,
                self.max_delay / self.base_delay
            )
            logger.warning(f"Error rate {error_rate:.1%} - increasing delay multiplier to {self._current_multiplier:.1f}")
        elif error_rate < self.error_threshold / 2:
            # Decrease delay
            self._current_multiplier = max(
                self._current_multiplier * 0.9,
                1.0
            )
    
    def get_delay(self) -> float:
        """Get current recommended delay."""
        delay = self.base_delay * self._current_multiplier
        # Add some randomization
        delay *= random.uniform(0.8, 1.2)
        return min(delay, self.max_delay)
    
    @property
    def current_multiplier(self) -> float:
        return self._current_multiplier
    
    def reset(self) -> None:
        """Reset throttler state."""
        self._recent_results.clear()
        self._current_multiplier = 1.0


# Singleton instances
_proxy_manager: Optional[ProxyManager] = None
_throttler: Optional[DynamicThrottler] = None


def get_proxy_manager() -> ProxyManager:
    """Get singleton proxy manager instance."""
    global _proxy_manager
    if _proxy_manager is None:
        _proxy_manager = ProxyManager()
    return _proxy_manager


def get_throttler() -> DynamicThrottler:
    """Get singleton throttler instance."""
    global _throttler
    if _throttler is None:
        config = get_config()
        crawl_config = config.get("crawling", {})
        _throttler = DynamicThrottler(
            base_delay=crawl_config.get("min_delay_seconds", 5),
            max_delay=crawl_config.get("max_delay_seconds", 60),
        )
    return _throttler
