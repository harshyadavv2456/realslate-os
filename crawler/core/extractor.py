"""
RealSlate Core - Data Extraction Engine
Config-driven extraction with CSS/XPath selectors and fallback support.
Integrated with selector health monitoring for automatic optimization.
"""

import re
from typing import Optional, List, Dict, Any, Union, Tuple
from datetime import datetime

from lxml import html
from lxml.etree import XPath
from bs4 import BeautifulSoup
from selectolax.parser import HTMLParser
from loguru import logger

from .utils import (
    parse_indian_price,
    parse_area,
    parse_beds,
    parse_relative_date,
    generate_uid,
    generate_url_hash,
    generate_address_hash,
    truncate_string,
    normalize_address,
)
from .selector_health import get_selector_monitor, get_dom_detector


class DataExtractor:
    """
    Production-grade data extractor with:
    - Multi-selector fallback
    - CSS and XPath support
    - Value transformations
    - Field validation
    - Integrated selector health monitoring
    """
    
    def __init__(self, site_config: Dict[str, Any], enable_health_monitoring: bool = True):
        self.site_config = site_config
        self.selectors = site_config.get("selectors", {})
        self.transformations = site_config.get("transformations", {})
        self.site_name = site_config.get("site", {}).get("name", "unknown")
        
        # Selector health monitoring integration
        self._enable_health_monitoring = enable_health_monitoring
        self._selector_monitor = get_selector_monitor() if enable_health_monitoring else None
        self._dom_detector = get_dom_detector() if enable_health_monitoring else None
        
        # Stats
        self._extraction_count = 0
        self._success_count = 0
        self._field_stats: Dict[str, Dict[str, int]] = {}
    
    def extract_listings(
        self,
        page_html: str,
        base_url: str = "",
        detect_dom_changes: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Extract all listings from a page.
        Optionally detect DOM changes for selector breakage.
        """
        listings = []
        
        # Detect DOM changes if enabled
        if detect_dom_changes and self._dom_detector:
            changes = self._dom_detector.detect_changes(
                self.site_name, "listing_page", page_html
            )
            if changes.get("status") == "changed":
                logger.warning(
                    f"DOM changes detected for {self.site_name}: "
                    f"{len(changes.get('changes', []))} changes"
                )
                for change in changes.get("changes", [])[:3]:
                    logger.warning(f"  - {change.get('type')}: {change.get('items', [])[:5]}")
        
        # Get listing cards
        card_selectors = self.selectors.get("listing_card", [])
        cards = self._find_all_elements(page_html, card_selectors)
        
        logger.debug(f"Found {len(cards)} listing cards")
        
        for card_html in cards:
            try:
                listing = self._extract_single_listing(card_html, base_url)
                if listing:
                    listings.append(listing)
                    self._success_count += 1
            except Exception as e:
                logger.warning(f"Failed to extract listing: {e}")
            
            self._extraction_count += 1
        
        return listings
    
    def _extract_single_listing(
        self,
        card_html: str,
        base_url: str
    ) -> Optional[Dict[str, Any]]:
        """Extract data from a single listing card."""
        data = {
            "scraped_at": datetime.utcnow().isoformat(),
            "source": self.site_config.get("site", {}).get("name", "unknown"),
        }
        
        # Extract each configured field
        field_mappings = {
            "price": self._extract_price,
            "address": self._extract_address,
            "beds": self._extract_beds,
            "area": self._extract_area,
            "detail_link": self._extract_link,
            "property_type": self._extract_text,
            "posted_date": self._extract_date,
            "builder_name": self._extract_text,
            "description": self._extract_text,
            "amenities": self._extract_list,
        }
        
        for field, extractor in field_mappings.items():
            selectors = self.selectors.get(field, [])
            if selectors:
                value = extractor(card_html, selectors, base_url if field == "detail_link" else None)
                if value is not None:
                    data[field] = value
                    self._record_field_success(field, True)
                else:
                    self._record_field_success(field, False)
        
        # Generate unique identifiers
        detail_link = data.get("detail_link", "")
        url_hash = generate_url_hash(detail_link)
        
        # If no detail_link, build a fallback hash from stable listing fields
        # (no scraped_at — must be same across runs for incremental matching)
        if not url_hash:
            import hashlib
            fallback_key = "|".join([
                str(data.get("address", "")),
                str(data.get("price", "")),
                str(data.get("beds", "")),
                str(data.get("area", "")),
                str(data.get("source", "")),
            ])
            url_hash = hashlib.md5(fallback_key.encode()).hexdigest()
        
        data["url_hash"] = url_hash
        data["address_hash"] = generate_address_hash(data.get("address", ""))
        data["property_uid"] = generate_uid(
            data.get("address", ""),
            data.get("price", ""),
            data.get("area", ""),
            data.get("source", "")
        )
        
        # Apply transformations
        data = self._apply_transformations(data)
        
        return data
    
    def _find_all_elements(
        self,
        html_content: str,
        selectors: List[Dict[str, str]]
    ) -> List[str]:
        """Find all matching elements using multiple selector fallbacks."""
        for sel_config in selectors:
            selector = sel_config.get("selector", "")
            sel_type = sel_config.get("type", "css")
            
            try:
                if sel_type == "xpath":
                    elements = self._xpath_find_all(html_content, selector)
                else:
                    elements = self._css_find_all(html_content, selector)
                
                if elements:
                    return elements
                    
            except Exception as e:
                logger.debug(f"Selector failed: {selector} - {e}")
                continue
        
        return []
    
    def _find_element_with_tracking(
        self,
        html_content: str,
        selectors: List[Dict[str, str]],
        field_name: str = ""
    ) -> Tuple[Optional[str], Optional[int]]:
        """
        Find first matching element using selector fallbacks.
        Returns (value, success_index) for health tracking.
        """
        # Optionally optimize selector order based on historical success
        if self._selector_monitor and field_name:
            selectors = self._selector_monitor.get_optimized_selectors(
                self.site_name, field_name, selectors
            )
        
        for idx, sel_config in enumerate(selectors):
            selector = sel_config.get("selector", "")
            sel_type = sel_config.get("type", "css")
            attribute = sel_config.get("attribute", "text")
            index = sel_config.get("index", 0)
            
            try:
                if sel_type == "xpath":
                    elements = self._xpath_find_all(html_content, selector)
                else:
                    elements = self._css_find_all(html_content, selector)
                
                if not elements or index >= len(elements):
                    continue
                
                element_html = elements[index]
                value = self._get_attribute(element_html, attribute)
                
                if value and value.strip():
                    return value.strip(), idx
                    
            except Exception as e:
                logger.debug(f"Selector extraction failed: {selector} - {e}")
                continue
        
        return None, None
    
    def _find_element(
        self,
        html_content: str,
        selectors: List[Dict[str, str]],
        field_name: str = ""
    ) -> Optional[str]:
        """Find first matching element using selector fallbacks."""
        value, success_index = self._find_element_with_tracking(
            html_content, selectors, field_name
        )
        
        # Record extraction result for health monitoring
        if self._selector_monitor and field_name and selectors:
            self._selector_monitor.record_extraction(
                self.site_name,
                field_name,
                selectors,
                success_index
            )
        
        return value
    
    def _css_find_all(self, html_content: str, selector: str) -> List[str]:
        """Find all elements using CSS selector."""
        try:
            # Use selectolax for speed
            parser = HTMLParser(html_content)
            elements = parser.css(selector)
            return [e.html for e in elements if e]
        except Exception:
            # Fallback to BeautifulSoup
            soup = BeautifulSoup(html_content, 'lxml')
            elements = soup.select(selector)
            return [str(e) for e in elements]
    
    def _xpath_find_all(self, html_content: str, xpath: str) -> List[str]:
        """Find all elements using XPath selector."""
        try:
            tree = html.fromstring(html_content)
            elements = tree.xpath(xpath)
            return [html.tostring(e, encoding='unicode') for e in elements if hasattr(e, 'tag')]
        except Exception as e:
            logger.debug(f"XPath error: {e}")
            return []
    
    def _get_attribute(self, element_html: str, attribute: str) -> Optional[str]:
        """Extract attribute value from element HTML."""
        try:
            soup = BeautifulSoup(element_html, 'lxml')
            element = soup.find()
            
            if not element:
                return None
            
            if attribute == "text":
                return element.get_text(strip=True, separator=' ')
            elif attribute == "html":
                return str(element)
            else:
                return element.get(attribute)
                
        except Exception as e:
            logger.debug(f"Attribute extraction error: {e}")
            return None
    
    def _extract_price(
        self,
        html_content: str,
        selectors: List[Dict[str, str]],
        _: Any = None
    ) -> Optional[float]:
        """Extract and parse price."""
        raw_value = self._find_element(html_content, selectors)
        if raw_value:
            return parse_indian_price(raw_value)
        return None
    
    def _extract_address(
        self,
        html_content: str,
        selectors: List[Dict[str, str]],
        _: Any = None
    ) -> Optional[str]:
        """Extract and normalize address."""
        raw_value = self._find_element(html_content, selectors)
        if raw_value:
            # Basic cleanup
            address = re.sub(r'\s+', ' ', raw_value).strip()
            return truncate_string(address, 500)
        return None
    
    def _extract_beds(
        self,
        html_content: str,
        selectors: List[Dict[str, str]],
        _: Any = None
    ) -> Optional[int]:
        """Extract bedroom count."""
        raw_value = self._find_element(html_content, selectors)
        if raw_value:
            return parse_beds(raw_value)
        return None
    
    def _extract_area(
        self,
        html_content: str,
        selectors: List[Dict[str, str]],
        _: Any = None
    ) -> Optional[float]:
        """Extract area in sq.ft."""
        raw_value = self._find_element(html_content, selectors)
        if raw_value:
            return parse_area(raw_value)
        return None
    
    def _extract_link(
        self,
        html_content: str,
        selectors: List[Dict[str, str]],
        base_url: Optional[str] = None
    ) -> Optional[str]:
        """Extract and resolve link URL."""
        raw_value = self._find_element(html_content, selectors)
        if raw_value:
            # Make absolute URL
            if base_url and raw_value.startswith('/'):
                raw_value = base_url.rstrip('/') + raw_value
            return truncate_string(raw_value, 2000)
        return None
    
    def _extract_text(
        self,
        html_content: str,
        selectors: List[Dict[str, str]],
        _: Any = None
    ) -> Optional[str]:
        """Extract text content."""
        raw_value = self._find_element(html_content, selectors)
        if raw_value:
            return truncate_string(raw_value, 1000)
        return None
    
    def _extract_date(
        self,
        html_content: str,
        selectors: List[Dict[str, str]],
        _: Any = None
    ) -> Optional[str]:
        """Extract and parse date."""
        raw_value = self._find_element(html_content, selectors)
        if raw_value:
            parsed = parse_relative_date(raw_value)
            if parsed:
                return parsed.isoformat()
        return None
    
    def _extract_list(
        self,
        html_content: str,
        selectors: List[Dict[str, str]],
        _: Any = None
    ) -> Optional[List[str]]:
        """Extract list of values."""
        for sel_config in selectors:
            if not sel_config.get("multiple"):
                continue
            
            selector = sel_config.get("selector", "")
            sel_type = sel_config.get("type", "css")
            
            try:
                if sel_type == "xpath":
                    elements = self._xpath_find_all(html_content, selector)
                else:
                    elements = self._css_find_all(html_content, selector)
                
                if elements:
                    values = []
                    for el in elements:
                        text = self._get_attribute(el, "text")
                        if text:
                            values.append(text)
                    
                    if values:
                        return values[:20]  # Limit to 20 items
                        
            except Exception:
                continue
        
        return None
    
    def _apply_transformations(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Apply configured transformations to extracted data."""
        for field, transforms in self.transformations.items():
            if field not in data or data[field] is None:
                continue
            
            value = data[field]
            
            for transform in transforms:
                action = transform.get("action")
                
                if action == "regex":
                    pattern = transform.get("pattern", "")
                    replacement = transform.get("replacement", "")
                    group = transform.get("group")
                    
                    if group is not None:
                        match = re.search(pattern, str(value))
                        if match:
                            try:
                                value = match.group(group)
                            except IndexError:
                                pass
                    else:
                        value = re.sub(pattern, replacement, str(value))
                
                elif action == "to_int":
                    try:
                        value = int(float(str(value).replace(',', '')))
                    except ValueError:
                        pass
                
                elif action == "to_float":
                    try:
                        value = float(str(value).replace(',', ''))
                    except ValueError:
                        pass
                
                elif action == "convert_indian_notation":
                    if isinstance(value, str):
                        parsed = parse_indian_price(value)
                        if parsed:
                            value = parsed
                
                elif action == "lowercase":
                    value = str(value).lower()
                
                elif action == "uppercase":
                    value = str(value).upper()
                
                elif action == "strip":
                    value = str(value).strip()
            
            data[field] = value
        
        return data
    
    def extract_pagination_info(
        self,
        page_html: str
    ) -> Dict[str, Any]:
        """Extract pagination information from page."""
        info = {
            "has_next": False,
            "next_selector": None,
            "total_count": None,
            "current_page": None,
        }
        
        # Check for next page
        next_selectors = self.selectors.get("next_page", [])
        for sel_config in next_selectors:
            selector = sel_config.get("selector", "")
            sel_type = sel_config.get("type", "css")
            
            try:
                if sel_type == "xpath":
                    elements = self._xpath_find_all(page_html, selector)
                else:
                    elements = self._css_find_all(page_html, selector)
                
                if elements:
                    info["has_next"] = True
                    info["next_selector"] = selector
                    break
                    
            except Exception:
                continue
        
        # Try to extract total count
        pagination_config = self.site_config.get("pagination", {})
        total_selector = pagination_config.get("total_count_selector")
        
        if total_selector:
            try:
                elements = self._css_find_all(page_html, total_selector)
                if elements:
                    text = self._get_attribute(elements[0], "text")
                    if text:
                        # Extract number from text like "1,234 properties found"
                        match = re.search(r'([\d,]+)', text)
                        if match:
                            info["total_count"] = int(match.group(1).replace(',', ''))
            except Exception:
                pass
        
        return info
    
    def has_more_content(self, page_html: str) -> bool:
        """Check if page has load more button or infinite scroll indicator."""
        load_more_selectors = self.selectors.get("load_more", [])
        
        for sel_config in load_more_selectors:
            selector = sel_config.get("selector", "")
            
            try:
                elements = self._css_find_all(page_html, selector)
                if elements:
                    return True
            except Exception:
                continue
        
        return False
    
    def _record_field_success(self, field: str, success: bool) -> None:
        """Record field extraction success/failure."""
        if field not in self._field_stats:
            self._field_stats[field] = {"success": 0, "failure": 0}
        
        if success:
            self._field_stats[field]["success"] += 1
        else:
            self._field_stats[field]["failure"] += 1
    
    def get_stats(self) -> Dict[str, Any]:
        """Get extraction statistics."""
        field_rates = {}
        for field, stats in self._field_stats.items():
            total = stats["success"] + stats["failure"]
            if total > 0:
                field_rates[field] = stats["success"] / total
        
        return {
            "extraction_count": self._extraction_count,
            "success_count": self._success_count,
            "success_rate": self._success_count / max(1, self._extraction_count),
            "field_success_rates": field_rates,
        }
    
    def reset_stats(self) -> None:
        """Reset extraction statistics."""
        self._extraction_count = 0
        self._success_count = 0
        self._field_stats.clear()


class DetailPageExtractor(DataExtractor):
    """
    Extended extractor for property detail pages.
    """
    
    def extract_detail_page(
        self,
        page_html: str,
        existing_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Extract additional details from property detail page.
        Merges with existing listing data.
        """
        data = existing_data.copy()
        
        # Additional detail page selectors
        detail_fields = {
            "full_description": "description",
            "amenities_list": "amenities",
            "latitude": "lat",
            "longitude": "lng",
            "floor": "floor",
            "total_floors": "total_floors",
            "furnishing": "furnishing",
            "facing": "facing",
            "possession_status": "possession_status",
            "age_of_property": "property_age",
            "bathroom_count": "bathrooms",
            "balcony_count": "balconies",
            "parking": "parking",
            "maintenance": "maintenance",
            "price_per_sqft": "price_per_sqft",
        }
        
        for config_key, data_key in detail_fields.items():
            selectors = self.selectors.get(config_key, [])
            if selectors:
                value = self._find_element(page_html, selectors)
                if value and value.strip():
                    data[data_key] = value.strip()
        
        # Mark as having detail data
        data["has_detail_data"] = True
        data["detail_scraped_at"] = datetime.utcnow().isoformat()
        
        return data
