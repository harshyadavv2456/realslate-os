"""
RealSlate Core - Browser Automation Manager
Production-grade Playwright browser management with stealth and human-like behavior.
"""

import asyncio
import random
import time
import gc
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable
from contextlib import asynccontextmanager
from datetime import datetime

from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Playwright
from loguru import logger
from fake_useragent import UserAgent
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .utils import get_config, random_delay, random_string
from .proxy import get_proxy_manager, get_throttler, Proxy
from .checkpoint import BrowserWatchdog


# Fingerprint data for randomization
SCREEN_RESOLUTIONS = [
    (1920, 1080), (1366, 768), (1536, 864), (1440, 900),
    (1280, 720), (1600, 900), (1680, 1050), (2560, 1440),
]

TIMEZONES = [
    "Asia/Kolkata",  # Valid timezone for India (Mumbai, Delhi, etc.)
]

LANGUAGES = [
    ["en-IN", "en-US", "en"],
    ["en-IN", "en", "hi"],
    ["en-US", "en-IN", "en"],
]

WEBGL_VENDORS = [
    "Intel Inc.", "Google Inc.", "NVIDIA Corporation",
]

WEBGL_RENDERERS = [
    "Intel Iris OpenGL Engine",
    "ANGLE (Intel, Intel(R) UHD Graphics Direct3D11 vs_5_0 ps_5_0)",
    "ANGLE (NVIDIA, NVIDIA GeForce GTX 1650 Direct3D11 vs_5_0 ps_5_0)",
]


class BrowserManager:
    """
    Production-grade browser management with:
    - Stealth mode
    - Human-like behavior simulation
    - Session management
    - Resource blocking
    - Proxy support
    """
    
    def __init__(self, config: Optional[dict] = None):
        self.config = config or get_config()
        self.browser_config = self.config.get("browser", {})
        self.crawl_config = self.config.get("crawling", {})
        
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        
        self._ua = UserAgent()
        self._session_dir = Path("sessions")
        self._session_dir.mkdir(exist_ok=True)
        
        # Tracking
        self._request_count = 0
        self._error_count = 0
        self._last_request_time = 0
        self._context_count = 0
        self._browser_start_time: Optional[float] = None
        
        # Production features
        self._proxy_manager = get_proxy_manager()
        self._throttler = get_throttler()
        self._watchdog = BrowserWatchdog(
            timeout_seconds=self.browser_config.get("watchdog_timeout", 120)
        )
        self._current_proxy: Optional[Proxy] = None
        
        # Browser recycling
        self._max_requests_per_browser = self.browser_config.get("max_requests_per_browser", 500)
        self._max_browser_age_seconds = self.browser_config.get("max_browser_age_seconds", 3600)
        self._max_contexts = self.browser_config.get("max_contexts", 3)
        
        # Current fingerprint
        self._current_fingerprint: Dict[str, Any] = {}
        
    @property
    def is_initialized(self) -> bool:
        return self._browser is not None and self._browser.is_connected()
    
    async def initialize(self, proxy: Optional[Proxy] = None) -> None:
        """Initialize Playwright and browser with optional proxy."""
        if self.is_initialized:
            # Check if browser needs recycling
            if self._should_recycle_browser():
                logger.info("Browser needs recycling, closing...")
                await self.close()
            else:
                logger.debug("Browser already initialized")
                return
        
        logger.info("Initializing browser...")
        
        self._playwright = await async_playwright().start()
        
        # Ensure headless is a boolean (YAML env substitution may return string)
        headless_value = self.browser_config.get("headless", True)
        if isinstance(headless_value, str):
            headless_value = headless_value.lower() in ("true", "1", "yes")
        
        launch_options = {
            "headless": headless_value,
            "slow_mo": self.browser_config.get("slow_mo", 100),
        }
        
        # Add browser arguments for stability and stealth
        launch_options["args"] = [
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-accelerated-2d-canvas",
            "--disable-gpu",
            "--window-size=1920,1080",
        ]
        
        # Get proxy from manager if not provided
        if proxy:
            self._current_proxy = proxy
        elif self._proxy_manager.is_enabled:
            self._current_proxy = await self._proxy_manager.get_proxy()
        
        # Proxy configuration
        if self._current_proxy:
            launch_options["proxy"] = self._current_proxy.playwright_config
            logger.info(f"Using proxy: {self._current_proxy.server}:{self._current_proxy.port}")
        
        # Launch browser
        self._browser = await self._playwright.chromium.launch(**launch_options)
        self._browser_start_time = time.time()
        self._request_count = 0
        self._context_count = 0
        
        # Generate new fingerprint for this browser session
        self._generate_fingerprint()
        
        logger.info("Browser initialized successfully")
    
    def _should_recycle_browser(self) -> bool:
        """Check if browser should be recycled."""
        if not self._browser_start_time:
            return False
        
        # Check request count
        if self._request_count >= self._max_requests_per_browser:
            logger.info(f"Browser recycling: request limit reached ({self._request_count})")
            return True
        
        # Check age
        age = time.time() - self._browser_start_time
        if age >= self._max_browser_age_seconds:
            logger.info(f"Browser recycling: age limit reached ({age:.0f}s)")
            return True
        
        # Check watchdog
        if self._watchdog.is_frozen:
            logger.warning("Browser recycling: watchdog detected freeze")
            return True
        
        return False
    
    def _generate_fingerprint(self) -> None:
        """Generate random fingerprint for current session."""
        resolution = random.choice(SCREEN_RESOLUTIONS)
        
        self._current_fingerprint = {
            "screen_width": resolution[0],
            "screen_height": resolution[1],
            "viewport_width": resolution[0] - random.randint(0, 100),
            "viewport_height": resolution[1] - random.randint(50, 150),
            "timezone": random.choice(TIMEZONES),
            "languages": random.choice(LANGUAGES),
            "webgl_vendor": random.choice(WEBGL_VENDORS),
            "webgl_renderer": random.choice(WEBGL_RENDERERS),
            "platform": random.choice(["Win32", "Win64", "MacIntel"]),
            "hardware_concurrency": random.choice([4, 8, 12, 16]),
            "device_memory": random.choice([4, 8, 16, 32]),
        }
        
        logger.debug(f"Generated fingerprint: {self._current_fingerprint['platform']}, "
                    f"{self._current_fingerprint['screen_width']}x{self._current_fingerprint['screen_height']}")
    
    async def create_context(self, site_name: str = "default") -> BrowserContext:
        """Create a new browser context with stealth settings and fingerprint randomization."""
        if not self.is_initialized:
            await self.initialize()
        
        # Check context limit
        if self._context_count >= self._max_contexts:
            logger.warning(f"Context limit reached ({self._max_contexts}), recycling browser")
            await self.close()
            await self.initialize()
        
        # Use fingerprint for viewport
        fp = self._current_fingerprint
        width = fp.get("viewport_width", 1920) + random.randint(-20, 20)
        height = fp.get("viewport_height", 1080) + random.randint(-20, 20)
        
        # Get random user agent
        user_agent = self._get_random_user_agent()
        
        # Randomize geolocation within India
        geo_offsets = [
            (19.0760, 72.8777),   # Mumbai
            (28.6139, 77.2090),   # Delhi
            (12.9716, 77.5946),   # Bangalore
            (17.3850, 78.4867),   # Hyderabad
            (13.0827, 80.2707),   # Chennai
            (18.5204, 73.8567),   # Pune
        ]
        lat, lng = random.choice(geo_offsets)
        lat += random.uniform(-0.1, 0.1)
        lng += random.uniform(-0.1, 0.1)
        
        context_options = {
            "viewport": {"width": width, "height": height},
            "screen": {
                "width": fp.get("screen_width", 1920),
                "height": fp.get("screen_height", 1080),
            },
            "user_agent": user_agent,
            "locale": "en-IN",
            "timezone_id": fp.get("timezone", "Asia/Kolkata"),
            "geolocation": {"latitude": lat, "longitude": lng},
            "permissions": ["geolocation"],
            "java_script_enabled": True,
            "accept_downloads": False,
            "ignore_https_errors": False,
            "color_scheme": random.choice(["light", "dark", "no-preference"]),
        }
        
        # Session storage for cookie persistence
        if self.crawl_config.get("cookie_persistence"):
            storage_path = self._session_dir / f"{site_name}_session.json"
            if storage_path.exists():
                try:
                    context_options["storage_state"] = str(storage_path)
                except Exception as e:
                    logger.warning(f"Failed to load session state: {e}")
        
        self._context = await self._browser.new_context(**context_options)
        self._context_count += 1
        
        # Apply stealth scripts with fingerprint
        if self.browser_config.get("stealth_mode", True):
            await self._apply_stealth(self._context)
        
        # Block unnecessary resources
        blocked_resources = self.browser_config.get("block_resources", [])
        if blocked_resources:
            await self._setup_resource_blocking(self._context, blocked_resources)
        
        logger.debug(f"Created browser context for {site_name} (#{self._context_count})")
        
        return self._context
    
    async def get_page(self, context: Optional[BrowserContext] = None) -> Page:
        """Get or create a page in the context."""
        ctx = context or self._context
        
        if not ctx:
            ctx = await self.create_context()
        
        self._page = await ctx.new_page()
        
        # Set extra HTTP headers
        await self._page.set_extra_http_headers({
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-IN,en;q=0.9,hi;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Cache-Control": "max-age=0",
        })
        
        return self._page
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=30),
        retry=retry_if_exception_type((TimeoutError, Exception)),
        before_sleep=lambda retry_state: logger.warning(
            f"Retrying page navigation, attempt {retry_state.attempt_number}"
        )
    )
    async def navigate(
        self,
        url: str,
        page: Optional[Page] = None,
        wait_until: str = "domcontentloaded",
        timeout: Optional[int] = None
    ) -> Page:
        """
        Navigate to URL with retry logic, dynamic throttling, and human-like behavior.
        """
        p = page or self._page
        if not p:
            p = await self.get_page()
        
        # Update watchdog heartbeat
        self._watchdog.heartbeat()
        
        # Apply dynamic delay (adjusts based on error rate)
        delay = self._throttler.get_delay()
        logger.debug(f"Applying delay: {delay:.2f}s (multiplier: {self._throttler.current_multiplier:.1f})")
        await asyncio.sleep(delay)
        
        timeout = timeout or self.browser_config.get("timeout", 60000)
        
        logger.info(f"Navigating to: {url[:100]}...")
        
        start_time = time.time()
        success = False
        
        try:
            response = await p.goto(url, wait_until=wait_until, timeout=timeout)
            
            # Check response status
            if response and response.status >= 400:
                logger.warning(f"Page returned status {response.status}: {url}")
                self._error_count += 1
                
                # Record proxy failure for ban detection
                if self._current_proxy and response.status in [403, 429, 503]:
                    self._proxy_manager.record_failure(
                        self._current_proxy,
                        f"HTTP {response.status}"
                    )
                    self._throttler.record_result(False)
            else:
                success = True
                self._throttler.record_result(True)
                
                # Record proxy success
                if self._current_proxy:
                    response_time = time.time() - start_time
                    self._proxy_manager.record_success(self._current_proxy, response_time)
            
            self._request_count += 1
            self._last_request_time = time.time()
            
            # Update watchdog
            self._watchdog.heartbeat()
            
            # Additional wait for dynamic content
            page_load_wait = self.crawl_config.get("page_load_wait", 3)
            await asyncio.sleep(page_load_wait + random.uniform(0, 1))
            
            # Human-like scrolling
            if self.crawl_config.get("scroll_enabled", True):
                await self._human_scroll(p)
            
            return p
            
        except Exception as e:
            self._error_count += 1
            self._throttler.record_result(False)
            
            # Record proxy failure
            if self._current_proxy:
                self._proxy_manager.record_failure(self._current_proxy, str(e))
            
            logger.error(f"Navigation failed for {url}: {e}")
            raise
    
    async def _apply_delay(self) -> None:
        """Apply random delay between requests."""
        min_delay = self.crawl_config.get("min_delay_seconds", 5)
        max_delay = self.crawl_config.get("max_delay_seconds", 30)
        
        delay = random_delay(min_delay, max_delay)
        logger.debug(f"Applying delay: {delay:.2f}s")
        await asyncio.sleep(delay)
    
    async def _human_scroll(self, page: Page) -> None:
        """Simulate human-like scrolling behavior."""
        try:
            # Get page height
            scroll_height = await page.evaluate("document.body.scrollHeight")
            viewport_height = await page.evaluate("window.innerHeight")
            
            if scroll_height <= viewport_height:
                return
            
            # Random scroll behavior
            scroll_positions = []
            current_pos = 0
            
            while current_pos < scroll_height - viewport_height:
                # Random scroll distance (200-800 pixels)
                scroll_dist = random.randint(200, 800)
                current_pos += scroll_dist
                scroll_positions.append(min(current_pos, scroll_height - viewport_height))
            
            # Sometimes scroll back up
            if random.random() > 0.7:
                scroll_positions.append(random.randint(0, current_pos // 2))
            
            # Execute scrolls
            for pos in scroll_positions[:10]:  # Limit scrolls
                await page.evaluate(f"window.scrollTo(0, {pos})")
                
                pause_min = self.crawl_config.get("scroll_pause_min", 0.5)
                pause_max = self.crawl_config.get("scroll_pause_max", 2.0)
                await asyncio.sleep(random.uniform(pause_min, pause_max))
            
            # Scroll back to top with probability
            if random.random() > 0.5:
                await page.evaluate("window.scrollTo(0, 0)")
                
        except Exception as e:
            logger.debug(f"Scroll simulation error (non-critical): {e}")
    
    async def infinite_scroll(
        self,
        page: Page,
        max_scrolls: int = 50,
        scroll_wait: float = 2.0,
        no_change_limit: int = 3
    ) -> int:
        """
        Handle infinite scroll pages.
        Returns number of items loaded.
        """
        last_height = await page.evaluate("document.body.scrollHeight")
        no_change_count = 0
        scroll_count = 0
        
        while scroll_count < max_scrolls and no_change_count < no_change_limit:
            # Scroll to bottom
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            
            # Wait for content to load
            await asyncio.sleep(scroll_wait + random.uniform(0, 1))
            
            # Check if height changed
            new_height = await page.evaluate("document.body.scrollHeight")
            
            if new_height == last_height:
                no_change_count += 1
            else:
                no_change_count = 0
                last_height = new_height
            
            scroll_count += 1
            logger.debug(f"Infinite scroll: {scroll_count}/{max_scrolls}, height: {new_height}")
        
        return scroll_count
    
    async def click_element(
        self,
        page: Page,
        selector: str,
        timeout: int = 10000
    ) -> bool:
        """Click element with human-like behavior."""
        try:
            element = await page.wait_for_selector(selector, timeout=timeout)
            if not element:
                return False
            
            # Random small delay before click
            await asyncio.sleep(random.uniform(0.1, 0.5))
            
            # Move mouse and click
            if self.crawl_config.get("mouse_movement", True):
                box = await element.bounding_box()
                if box:
                    # Add small random offset
                    x = box["x"] + box["width"] / 2 + random.randint(-5, 5)
                    y = box["y"] + box["height"] / 2 + random.randint(-5, 5)
                    await page.mouse.move(x, y, steps=random.randint(5, 15))
            
            await element.click()
            return True
            
        except Exception as e:
            logger.debug(f"Click failed for {selector}: {e}")
            return False
    
    async def wait_for_selector(
        self,
        page: Page,
        selectors: List[Dict[str, str]],
        timeout: int = 10000
    ) -> Optional[str]:
        """
        Wait for any of multiple selectors to appear.
        Returns the first matching selector.
        """
        for sel_config in selectors:
            selector = sel_config.get("selector", "")
            sel_type = sel_config.get("type", "css")
            
            try:
                if sel_type == "xpath":
                    element = await page.wait_for_selector(f"xpath={selector}", timeout=timeout // len(selectors))
                else:
                    element = await page.wait_for_selector(selector, timeout=timeout // len(selectors))
                
                if element:
                    return selector
                    
            except Exception:
                continue
        
        return None
    
    def _get_random_user_agent(self) -> str:
        """Get a random desktop user agent."""
        if self.browser_config.get("random_user_agents", True):
            try:
                # Prefer Chrome/Firefox on Windows/Mac
                for _ in range(5):
                    ua = self._ua.random
                    if any(browser in ua.lower() for browser in ['chrome', 'firefox']):
                        if any(os in ua.lower() for os in ['windows', 'macintosh']):
                            return ua
                return self._ua.chrome
            except Exception:
                pass
        
        # Fallback user agent
        return "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    
    async def _apply_stealth(self, context: BrowserContext) -> None:
        """Apply comprehensive stealth scripts with fingerprint randomization."""
        fp = self._current_fingerprint
        
        stealth_js = f"""
        // Overwrite navigator properties
        Object.defineProperty(navigator, 'webdriver', {{
            get: () => undefined,
        }});
        
        // Overwrite plugins with realistic data
        Object.defineProperty(navigator, 'plugins', {{
            get: () => {{
                const plugins = [
                    {{name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer'}},
                    {{name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai'}},
                    {{name: 'Native Client', filename: 'internal-nacl-plugin'}},
                ];
                plugins.length = 3;
                return plugins;
            }},
        }});
        
        // Overwrite languages with fingerprint
        Object.defineProperty(navigator, 'languages', {{
            get: () => {fp.get('languages', ['en-IN', 'en-US', 'en'])},
        }});
        
        // Platform override
        Object.defineProperty(navigator, 'platform', {{
            get: () => '{fp.get('platform', 'Win32')}',
        }});
        
        // Hardware concurrency
        Object.defineProperty(navigator, 'hardwareConcurrency', {{
            get: () => {fp.get('hardware_concurrency', 8)},
        }});
        
        // Device memory
        Object.defineProperty(navigator, 'deviceMemory', {{
            get: () => {fp.get('device_memory', 8)},
        }});
        
        // Mock chrome object
        window.chrome = {{
            runtime: {{
                connect: function() {{}},
                sendMessage: function() {{}},
            }},
            loadTimes: function() {{
                return {{
                    requestTime: Date.now() / 1000,
                    startLoadTime: Date.now() / 1000,
                }};
            }},
            csi: function() {{
                return {{pageT: Date.now()}};
            }},
        }};
        
        // Overwrite permissions
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) => (
            parameters.name === 'notifications' ?
                Promise.resolve({{ state: Notification.permission }}) :
                originalQuery(parameters)
        );
        
        // Add missing properties
        Object.defineProperty(navigator, 'maxTouchPoints', {{
            get: () => 0,
        }});
        
        // WebGL fingerprint spoofing
        const getParameter = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function(parameter) {{
            if (parameter === 37445) {{
                return '{fp.get('webgl_vendor', 'Intel Inc.')}';
            }}
            if (parameter === 37446) {{
                return '{fp.get('webgl_renderer', 'Intel Iris OpenGL Engine')}';
            }}
            return getParameter.call(this, parameter);
        }};
        
        // Canvas fingerprint randomization
        const toDataURL = HTMLCanvasElement.prototype.toDataURL;
        HTMLCanvasElement.prototype.toDataURL = function(type) {{
            if (type === 'image/png') {{
                const context = this.getContext('2d');
                if (context) {{
                    const imageData = context.getImageData(0, 0, this.width, this.height);
                    for (let i = 0; i < imageData.data.length; i += 4) {{
                        imageData.data[i] ^= {random.randint(0, 3)};
                    }}
                    context.putImageData(imageData, 0, 0);
                }}
            }}
            return toDataURL.apply(this, arguments);
        }};
        
        // Audio fingerprint randomization
        const audioContextConstructor = window.AudioContext || window.webkitAudioContext;
        if (audioContextConstructor) {{
            const getChannelData = AudioBuffer.prototype.getChannelData;
            AudioBuffer.prototype.getChannelData = function(channel) {{
                const data = getChannelData.call(this, channel);
                for (let i = 0; i < data.length; i += 100) {{
                    data[i] += Math.random() * 0.0001;
                }}
                return data;
            }};
        }}
        
        // Remove automation indicators
        delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
        delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
        delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;
        
        // Consistent Date/Time
        const originalDateGetTimezoneOffset = Date.prototype.getTimezoneOffset;
        Date.prototype.getTimezoneOffset = function() {{
            return -330; // IST
        }};
        """
        
        await context.add_init_script(stealth_js)
    
    async def _setup_resource_blocking(
        self,
        context: BrowserContext,
        resource_types: List[str]
    ) -> None:
        """Block specified resource types for faster loading."""
        async def route_handler(route):
            if route.request.resource_type in resource_types:
                await route.abort()
            else:
                await route.continue_()
        
        await context.route("**/*", route_handler)
    
    async def save_session(self, site_name: str) -> None:
        """Save session state for future use."""
        if not self._context:
            return
        
        storage_path = self._session_dir / f"{site_name}_session.json"
        
        try:
            await self._context.storage_state(path=str(storage_path))
            logger.debug(f"Session saved for {site_name}")
        except Exception as e:
            logger.warning(f"Failed to save session for {site_name}: {e}")
    
    async def get_page_content(self, page: Optional[Page] = None) -> str:
        """Get page HTML content."""
        p = page or self._page
        if not p:
            return ""
        
        return await p.content()
    
    async def screenshot(
        self,
        page: Optional[Page] = None,
        path: Optional[str] = None,
        full_page: bool = False
    ) -> bytes:
        """Take page screenshot."""
        p = page or self._page
        if not p:
            return b""
        
        options = {"full_page": full_page}
        if path:
            options["path"] = path
        
        return await p.screenshot(**options)
    
    async def close_page(self, page: Optional[Page] = None) -> None:
        """Close a specific page."""
        p = page or self._page
        if p:
            await p.close()
            if p == self._page:
                self._page = None
    
    async def close_context(self, context: Optional[BrowserContext] = None) -> None:
        """Close browser context."""
        ctx = context or self._context
        if ctx:
            await ctx.close()
            if ctx == self._context:
                self._context = None
    
    async def close(self) -> None:
        """Close browser and cleanup all resources."""
        logger.info("Closing browser...")
        
        # Close page first
        if self._page:
            try:
                await self._page.close()
            except Exception as e:
                logger.debug(f"Error closing page: {e}")
            self._page = None
        
        # Close context
        if self._context:
            try:
                await self._context.close()
            except Exception as e:
                logger.debug(f"Error closing context: {e}")
            self._context = None
        
        # Close browser
        if self._browser:
            try:
                await self._browser.close()
            except Exception as e:
                logger.debug(f"Error closing browser: {e}")
            self._browser = None
        
        # Stop playwright
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception as e:
                logger.debug(f"Error stopping playwright: {e}")
            self._playwright = None
        
        # Reset state
        self._current_proxy = None
        self._browser_start_time = None
        self._context_count = 0
        self._watchdog.reset()
        
        # Force garbage collection to release memory
        gc.collect()
        
        logger.info("Browser closed and memory released")
    
    async def safe_restart(self, new_proxy: bool = True) -> None:
        """Safely restart browser with optional new proxy."""
        logger.info("Safe browser restart initiated...")
        
        # Save session if needed
        if self._context and self.crawl_config.get("cookie_persistence"):
            try:
                await self.save_session("default")
            except Exception as e:
                logger.warning(f"Failed to save session before restart: {e}")
        
        # Close everything
        await self.close()
        
        # Small delay before restart
        await asyncio.sleep(1)
        
        # Get new proxy if requested
        proxy = None
        if new_proxy and self._proxy_manager.is_enabled:
            proxy = await self._proxy_manager.get_proxy(
                exclude=[self._current_proxy] if self._current_proxy else None
            )
        
        # Reinitialize
        await self.initialize(proxy=proxy)
        
        logger.info("Browser restarted successfully")
    
    @asynccontextmanager
    async def session(self, site_name: str = "default"):
        """
        Context manager for browser session with proper cleanup.
        """
        page = None
        context = None
        
        try:
            await self.initialize()
            context = await self.create_context(site_name)
            page = await self.get_page(context)
            yield page
            
        except Exception as e:
            logger.error(f"Session error: {e}")
            raise
            
        finally:
            # Always clean up
            try:
                if self.crawl_config.get("cookie_persistence"):
                    await self.save_session(site_name)
            except Exception as e:
                logger.debug(f"Failed to save session: {e}")
            
            try:
                if page:
                    await page.close()
                    if self._page == page:
                        self._page = None
            except Exception as e:
                logger.debug(f"Error closing page: {e}")
            
            try:
                if context:
                    await context.close()
                    if self._context == context:
                        self._context = None
            except Exception as e:
                logger.debug(f"Error closing context: {e}")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get comprehensive browser statistics."""
        browser_age = None
        if self._browser_start_time:
            browser_age = time.time() - self._browser_start_time
        
        return {
            "request_count": self._request_count,
            "error_count": self._error_count,
            "error_rate": self._error_count / max(1, self._request_count),
            "last_request_time": self._last_request_time,
            "is_initialized": self.is_initialized,
            "context_count": self._context_count,
            "browser_age_seconds": browser_age,
            "watchdog": {
                "is_frozen": self._watchdog.is_frozen,
                "seconds_since_activity": self._watchdog.seconds_since_activity,
            },
            "throttler": {
                "current_multiplier": self._throttler.current_multiplier,
                "recommended_delay": self._throttler.get_delay(),
            },
            "proxy": {
                "enabled": self._proxy_manager.is_enabled,
                "current": f"{self._current_proxy.server}:{self._current_proxy.port}" if self._current_proxy else None,
                "healthy_count": len(self._proxy_manager.healthy_proxies) if self._proxy_manager.is_enabled else 0,
            },
            "fingerprint": {
                "platform": self._current_fingerprint.get("platform"),
                "screen": f"{self._current_fingerprint.get('screen_width')}x{self._current_fingerprint.get('screen_height')}",
            } if self._current_fingerprint else None,
        }
    
    def reset_stats(self) -> None:
        """Reset statistics counters."""
        self._request_count = 0
        self._error_count = 0
        self._throttler.reset()
