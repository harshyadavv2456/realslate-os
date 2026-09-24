"""
RealSlate Core - Utility Functions and Configuration Loader
"""

import os
import re
import sys
import hashlib
import random
import string
from pathlib import Path
from typing import Any, Optional, Union
from datetime import datetime, timedelta

import yaml
from loguru import logger
from dotenv import load_dotenv
from unidecode import unidecode


# Load environment variables
load_dotenv()


class ConfigLoader:
    """
    YAML Configuration Loader with environment variable substitution.
    """
    
    _instance = None
    _config_cache: dict = {}
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        self.base_path = Path(__file__).parent.parent / "config"
        
    def _substitute_env_vars(self, value: Any) -> Any:
        """Substitute environment variables in config values."""
        if isinstance(value, str):
            # Pattern: ${VAR_NAME:-default_value} or ${VAR_NAME}
            pattern = r'\$\{([^}:]+)(?::-([^}]*))?\}'
            
            def replacer(match):
                var_name = match.group(1)
                default = match.group(2) if match.group(2) is not None else ""
                return os.environ.get(var_name, default)
            
            return re.sub(pattern, replacer, value)
        elif isinstance(value, dict):
            return {k: self._substitute_env_vars(v) for k, v in value.items()}
        elif isinstance(value, list):
            return [self._substitute_env_vars(item) for item in value]
        return value
    
    def load_settings(self, force_reload: bool = False) -> dict:
        """Load main settings.yaml configuration."""
        cache_key = "settings"
        
        if not force_reload and cache_key in self._config_cache:
            return self._config_cache[cache_key]
        
        settings_path = self.base_path / "settings.yaml"
        
        if not settings_path.exists():
            raise FileNotFoundError(f"Settings file not found: {settings_path}")
        
        with open(settings_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        
        config = self._substitute_env_vars(config)
        self._config_cache[cache_key] = config
        
        return config
    
    def load_site_config(self, site_name: str, force_reload: bool = False) -> dict:
        """Load site-specific configuration."""
        cache_key = f"site_{site_name}"
        
        if not force_reload and cache_key in self._config_cache:
            return self._config_cache[cache_key]
        
        site_path = self.base_path / "sites" / f"{site_name}.yaml"
        
        if not site_path.exists():
            raise FileNotFoundError(f"Site config not found: {site_path}")
        
        with open(site_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        
        config = self._substitute_env_vars(config)
        self._config_cache[cache_key] = config
        
        return config
    
    def list_available_sites(self) -> list:
        """List all available site configurations."""
        sites_dir = self.base_path / "sites"
        
        if not sites_dir.exists():
            return []
        
        return [
            f.stem for f in sites_dir.glob("*.yaml")
            if f.is_file()
        ]
    
    def get_enabled_sites(self) -> list:
        """Get list of enabled sites with their configs."""
        enabled = []
        
        for site_name in self.list_available_sites():
            try:
                config = self.load_site_config(site_name)
                if config.get("site", {}).get("enabled", False):
                    enabled.append({
                        "name": site_name,
                        "config": config
                    })
            except Exception as e:
                logger.warning(f"Failed to load site config {site_name}: {e}")
        
        # Sort by priority
        enabled.sort(key=lambda x: x["config"].get("site", {}).get("priority", 99))
        
        return enabled
    
    def clear_cache(self):
        """Clear configuration cache."""
        self._config_cache.clear()


def setup_logging(log_dir: str = "logs", level: str = "INFO"):
    """
    Configure Loguru logging with rotation and compression.
    """
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    
    # Remove default handler
    logger.remove()
    
    # Console handler
    logger.add(
        sys.stdout,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | <level>{message}</level>",
        level=level,
        colorize=True,
    )
    
    # File handler - general logs
    logger.add(
        log_path / "realslate_{time:YYYY-MM-DD}.log",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} | {message}",
        level=level,
        rotation="100 MB",
        retention="30 days",
        compression="gz",
        enqueue=True,
    )
    
    # Error-only file handler
    logger.add(
        log_path / "errors_{time:YYYY-MM-DD}.log",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} | {message}",
        level="ERROR",
        rotation="50 MB",
        retention="60 days",
        compression="gz",
        enqueue=True,
    )
    
    # Crawl-specific logs
    logger.add(
        log_path / "crawl_{time:YYYY-MM-DD}.log",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
        level="DEBUG",
        rotation="100 MB",
        retention="14 days",
        compression="gz",
        filter=lambda record: "crawl" in record["extra"],
        enqueue=True,
    )
    
    return logger


def generate_uid(*args) -> str:
    """Generate a unique ID from multiple values."""
    combined = "|".join(str(arg) for arg in args if arg)
    return hashlib.sha256(combined.encode()).hexdigest()[:32]


def generate_url_hash(url: str) -> str:
    """Generate hash from URL for deduplication. Returns empty for empty URLs."""
    if not url or not url.strip():
        return ""
    
    # Normalize URL
    url = url.lower().strip()
    url = re.sub(r'[?#].*$', '', url)  # Remove query params and fragments
    url = re.sub(r'/$', '', url)  # Remove trailing slash
    
    return hashlib.md5(url.encode()).hexdigest()


def generate_address_hash(address: str) -> str:
    """Generate hash from normalized address."""
    if not address:
        return ""
    
    # Normalize address
    normalized = unidecode(address.lower())
    normalized = re.sub(r'[^\w\s]', '', normalized)
    normalized = re.sub(r'\s+', ' ', normalized).strip()
    
    return hashlib.md5(normalized.encode()).hexdigest()


def parse_indian_price(price_str: str) -> Optional[float]:
    """
    Parse Indian price notation (Lac, Cr) to numeric value.
    Examples:
        "45 Lac" -> 4500000
        "1.25 Cr" -> 12500000
        "₹ 45,00,000" -> 4500000
    """
    if not price_str:
        return None
    
    price_str = str(price_str).lower().strip()
    
    # Remove currency symbols and commas
    price_str = re.sub(r'[₹,\s]', '', price_str)
    
    # Handle Crore notation
    cr_match = re.search(r'([\d.]+)\s*(?:cr|crore)', price_str)
    if cr_match:
        return float(cr_match.group(1)) * 10000000
    
    # Handle Lac/Lakh notation
    lac_match = re.search(r'([\d.]+)\s*(?:lac|lakh|l)', price_str)
    if lac_match:
        return float(lac_match.group(1)) * 100000
    
    # Handle thousand notation
    k_match = re.search(r'([\d.]+)\s*(?:k|thousand)', price_str)
    if k_match:
        return float(k_match.group(1)) * 1000
    
    # Try to extract plain number
    num_match = re.search(r'([\d.]+)', price_str)
    if num_match:
        try:
            return float(num_match.group(1))
        except ValueError:
            return None
    
    return None


def parse_area(area_str: str) -> Optional[float]:
    """
    Parse area string to square feet.
    """
    if not area_str:
        return None
    
    area_str = str(area_str).lower().strip()
    
    # Extract numeric value
    num_match = re.search(r'([\d,]+\.?\d*)', area_str)
    if not num_match:
        return None
    
    try:
        value = float(num_match.group(1).replace(',', ''))
    except ValueError:
        return None
    
    # Convert to sq.ft if in other units
    if 'sq.m' in area_str or 'sqm' in area_str or 'meter' in area_str:
        value = value * 10.764  # sq meters to sq feet
    elif 'yard' in area_str:
        value = value * 9  # sq yards to sq feet
    elif 'acre' in area_str:
        value = value * 43560  # acres to sq feet
    elif 'hectare' in area_str or 'ha' in area_str:
        value = value * 107639  # hectares to sq feet
    
    return value


def parse_beds(beds_str: str) -> Optional[int]:
    """
    Parse bedroom count from string.
    Examples:
        "2 BHK" -> 2
        "3 Bedroom" -> 3
    """
    if not beds_str:
        return None
    
    beds_str = str(beds_str).lower().strip()
    
    # Extract number before BHK/Bedroom/bed
    match = re.search(r'(\d+)\s*(?:bhk|bedroom|bed|rk)', beds_str)
    if match:
        return int(match.group(1))
    
    # Try plain number
    match = re.search(r'^(\d+)$', beds_str)
    if match:
        return int(match.group(1))
    
    return None


def random_delay(min_seconds: float, max_seconds: float) -> float:
    """Generate random delay with slight randomization."""
    base_delay = random.uniform(min_seconds, max_seconds)
    # Add small random variation
    variation = random.uniform(-0.5, 0.5)
    return max(0.5, base_delay + variation)


def random_string(length: int = 16) -> str:
    """Generate random alphanumeric string."""
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))


def sanitize_filename(filename: str) -> str:
    """Sanitize string for use as filename."""
    # Remove invalid characters
    sanitized = re.sub(r'[<>:"/\\|?*]', '', filename)
    sanitized = re.sub(r'\s+', '_', sanitized)
    return sanitized[:200]  # Limit length


def truncate_string(s: str, max_length: int = 255) -> str:
    """Truncate string to max length."""
    if not s:
        return ""
    return s[:max_length] if len(s) > max_length else s


def normalize_address(address: str) -> str:
    """Normalize address for comparison."""
    if not address:
        return ""
    
    # Convert to ASCII
    normalized = unidecode(address)
    
    # Lowercase
    normalized = normalized.lower()
    
    # Remove special characters except spaces
    normalized = re.sub(r'[^\w\s]', ' ', normalized)
    
    # Normalize whitespace
    normalized = re.sub(r'\s+', ' ', normalized).strip()
    
    # Common abbreviations
    replacements = {
        ' rd ': ' road ',
        ' st ': ' street ',
        ' ave ': ' avenue ',
        ' blvd ': ' boulevard ',
        ' dr ': ' drive ',
        ' ln ': ' lane ',
        ' apt ': ' apartment ',
        ' fl ': ' floor ',
        ' bldg ': ' building ',
    }
    
    for old, new in replacements.items():
        normalized = normalized.replace(old, new)
    
    return normalized


def get_timestamp() -> str:
    """Get current timestamp in ISO format."""
    return datetime.utcnow().isoformat()


def parse_relative_date(date_str: str) -> Optional[datetime]:
    """
    Parse relative date strings like "Posted 2 days ago".
    """
    if not date_str:
        return None
    
    date_str = date_str.lower().strip()
    now = datetime.utcnow()
    
    # Check for "today" or "just now"
    if 'today' in date_str or 'just now' in date_str or 'just posted' in date_str:
        return now
    
    # Check for "yesterday"
    if 'yesterday' in date_str:
        return now - timedelta(days=1)
    
    # Pattern: "X days/weeks/months ago"
    patterns = [
        (r'(\d+)\s*(?:day|days)\s*ago', lambda m: now - timedelta(days=int(m.group(1)))),
        (r'(\d+)\s*(?:week|weeks)\s*ago', lambda m: now - timedelta(weeks=int(m.group(1)))),
        (r'(\d+)\s*(?:month|months)\s*ago', lambda m: now - timedelta(days=int(m.group(1)) * 30)),
        (r'(\d+)\s*(?:hour|hours)\s*ago', lambda m: now - timedelta(hours=int(m.group(1)))),
    ]
    
    for pattern, handler in patterns:
        match = re.search(pattern, date_str)
        if match:
            return handler(match)
    
    return None


class RateLimiter:
    """
    Simple rate limiter for request throttling.
    """
    
    def __init__(self, requests_per_minute: int = 10):
        self.requests_per_minute = requests_per_minute
        self.requests: list = []
    
    def can_proceed(self) -> bool:
        """Check if we can make a request."""
        now = datetime.utcnow()
        cutoff = now - timedelta(minutes=1)
        
        # Remove old requests
        self.requests = [r for r in self.requests if r > cutoff]
        
        return len(self.requests) < self.requests_per_minute
    
    def record_request(self):
        """Record a request."""
        self.requests.append(datetime.utcnow())
    
    def wait_time(self) -> float:
        """Get wait time in seconds before next request."""
        if self.can_proceed():
            return 0
        
        oldest = min(self.requests)
        wait = (oldest + timedelta(minutes=1) - datetime.utcnow()).total_seconds()
        return max(0, wait)


# Singleton config loader
config_loader = ConfigLoader()


def get_config() -> dict:
    """Get main configuration."""
    return config_loader.load_settings()


def get_site_config(site_name: str) -> dict:
    """Get site-specific configuration."""
    return config_loader.load_site_config(site_name)
