"""
RealSlate Core - Selector Health Monitor
Tracks selector success rates and detects DOM changes.
"""

import json
import hashlib
import difflib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, asdict, field
from collections import defaultdict

from bs4 import BeautifulSoup
from loguru import logger

from .utils import get_config, ConfigLoader


@dataclass
class SelectorStats:
    """Statistics for a single selector."""
    selector: str
    selector_type: str
    field: str
    
    success_count: int = 0
    failure_count: int = 0
    total_attempts: int = 0
    
    last_success: Optional[str] = None
    last_failure: Optional[str] = None
    
    # Fallback tracking
    fallback_position: int = 0  # 0 = primary, 1+ = fallback
    used_as_fallback_count: int = 0
    
    @property
    def success_rate(self) -> float:
        if self.total_attempts == 0:
            return 0.0
        return self.success_count / self.total_attempts
    
    @property
    def is_healthy(self) -> bool:
        # Healthy if >50% success rate with at least 10 attempts
        if self.total_attempts < 10:
            return True
        return self.success_rate >= 0.5


@dataclass
class FieldStats:
    """Statistics for a field across all selectors."""
    field: str
    site: str
    
    total_extractions: int = 0
    successful_extractions: int = 0
    
    primary_success: int = 0
    fallback_success: int = 0
    
    selector_stats: Dict[str, SelectorStats] = field(default_factory=dict)
    
    @property
    def success_rate(self) -> float:
        if self.total_extractions == 0:
            return 0.0
        return self.successful_extractions / self.total_extractions
    
    @property
    def fallback_rate(self) -> float:
        if self.successful_extractions == 0:
            return 0.0
        return self.fallback_success / self.successful_extractions


class SelectorHealthMonitor:
    """
    Production-grade selector health monitoring with:
    - Success rate tracking per selector
    - Automatic fallback ranking
    - DOM change detection
    - Breakage alerts
    """
    
    def __init__(self, config: Optional[dict] = None):
        self.config = config or get_config()
        self.config_loader = ConfigLoader()
        
        self.data_dir = Path(
            self.config.get("monitoring", {}).get("selector_data_dir", ".selector_health")
        )
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        # Stats storage
        self._field_stats: Dict[str, Dict[str, FieldStats]] = defaultdict(dict)
        self._dom_snapshots: Dict[str, str] = {}
        
        # Thresholds
        self._min_success_rate = 0.5
        self._min_samples = 10
        
        # Load saved data
        self._load_stats()
    
    def _get_stats_path(self, site_name: str) -> Path:
        """Get path for site stats file."""
        return self.data_dir / f"{site_name}_selector_stats.json"
    
    def _load_stats(self) -> None:
        """Load saved statistics from disk."""
        for stats_file in self.data_dir.glob("*_selector_stats.json"):
            try:
                site_name = stats_file.stem.replace("_selector_stats", "")
                with open(stats_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                for field_name, field_data in data.items():
                    fs = FieldStats(
                        field=field_data["field"],
                        site=field_data["site"],
                        total_extractions=field_data.get("total_extractions", 0),
                        successful_extractions=field_data.get("successful_extractions", 0),
                        primary_success=field_data.get("primary_success", 0),
                        fallback_success=field_data.get("fallback_success", 0),
                    )
                    
                    for sel_key, sel_data in field_data.get("selector_stats", {}).items():
                        fs.selector_stats[sel_key] = SelectorStats(**sel_data)
                    
                    self._field_stats[site_name][field_name] = fs
                    
            except Exception as e:
                logger.warning(f"Failed to load selector stats from {stats_file}: {e}")
    
    def _save_stats(self, site_name: str) -> None:
        """Save statistics to disk."""
        stats_path = self._get_stats_path(site_name)
        
        try:
            data = {}
            for field_name, fs in self._field_stats.get(site_name, {}).items():
                data[field_name] = {
                    "field": fs.field,
                    "site": fs.site,
                    "total_extractions": fs.total_extractions,
                    "successful_extractions": fs.successful_extractions,
                    "primary_success": fs.primary_success,
                    "fallback_success": fs.fallback_success,
                    "selector_stats": {
                        k: asdict(v) for k, v in fs.selector_stats.items()
                    },
                }
            
            with open(stats_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
                
        except Exception as e:
            logger.error(f"Failed to save selector stats: {e}")
    
    def record_extraction(self,
                         site_name: str,
                         field: str,
                         selectors: List[Dict[str, str]],
                         success_index: Optional[int],
                         html_snippet: str = "") -> None:
        """
        Record an extraction attempt.
        
        Args:
            site_name: Name of the site
            field: Field being extracted
            selectors: List of selector configs tried
            success_index: Index of successful selector (None if all failed)
            html_snippet: HTML snippet for DOM tracking
        """
        # Initialize field stats if needed
        if field not in self._field_stats[site_name]:
            self._field_stats[site_name][field] = FieldStats(
                field=field,
                site=site_name,
            )
        
        fs = self._field_stats[site_name][field]
        fs.total_extractions += 1
        
        if success_index is not None:
            fs.successful_extractions += 1
            
            if success_index == 0:
                fs.primary_success += 1
            else:
                fs.fallback_success += 1
        
        # Update individual selector stats
        for i, sel_config in enumerate(selectors):
            selector = sel_config.get("selector", "")
            sel_type = sel_config.get("type", "css")
            sel_key = f"{sel_type}:{selector}"
            
            if sel_key not in fs.selector_stats:
                fs.selector_stats[sel_key] = SelectorStats(
                    selector=selector,
                    selector_type=sel_type,
                    field=field,
                    fallback_position=i,
                )
            
            ss = fs.selector_stats[sel_key]
            ss.total_attempts += 1
            
            if success_index is not None and i == success_index:
                ss.success_count += 1
                ss.last_success = datetime.utcnow().isoformat()
                
                if i > 0:
                    ss.used_as_fallback_count += 1
            elif success_index is None or i < success_index:
                # Failed before finding a match
                ss.failure_count += 1
                ss.last_failure = datetime.utcnow().isoformat()
        
        # Periodically save
        if fs.total_extractions % 100 == 0:
            self._save_stats(site_name)
    
    def get_optimized_selectors(self, 
                               site_name: str, 
                               field: str,
                               selectors: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """
        Reorder selectors by success rate (best first).
        """
        if site_name not in self._field_stats:
            return selectors
        
        if field not in self._field_stats[site_name]:
            return selectors
        
        fs = self._field_stats[site_name][field]
        
        # Build selector scores
        selector_scores = []
        for sel_config in selectors:
            selector = sel_config.get("selector", "")
            sel_type = sel_config.get("type", "css")
            sel_key = f"{sel_type}:{selector}"
            
            if sel_key in fs.selector_stats:
                ss = fs.selector_stats[sel_key]
                score = ss.success_rate if ss.total_attempts >= self._min_samples else 0.5
            else:
                score = 0.5  # Unknown selector gets neutral score
            
            selector_scores.append((sel_config, score))
        
        # Sort by score (highest first)
        selector_scores.sort(key=lambda x: x[1], reverse=True)
        
        return [s[0] for s in selector_scores]
    
    def check_health(self, site_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Check selector health for a site or all sites.
        Returns degraded selectors and recommendations.
        """
        report = {
            "timestamp": datetime.utcnow().isoformat(),
            "sites": {},
        }
        
        sites_to_check = [site_name] if site_name else list(self._field_stats.keys())
        
        for site in sites_to_check:
            site_report = {
                "healthy_fields": [],
                "degraded_fields": [],
                "critical_fields": [],
                "recommendations": [],
            }
            
            for field_name, fs in self._field_stats.get(site, {}).items():
                if fs.total_extractions < self._min_samples:
                    continue
                
                if fs.success_rate >= 0.8:
                    site_report["healthy_fields"].append({
                        "field": field_name,
                        "success_rate": fs.success_rate,
                    })
                elif fs.success_rate >= 0.5:
                    site_report["degraded_fields"].append({
                        "field": field_name,
                        "success_rate": fs.success_rate,
                        "fallback_rate": fs.fallback_rate,
                    })
                    
                    if fs.fallback_rate > 0.5:
                        site_report["recommendations"].append(
                            f"Consider promoting fallback selector for {field_name}"
                        )
                else:
                    site_report["critical_fields"].append({
                        "field": field_name,
                        "success_rate": fs.success_rate,
                    })
                    site_report["recommendations"].append(
                        f"URGENT: Update selectors for {field_name} ({fs.success_rate:.1%} success)"
                    )
            
            report["sites"][site] = site_report
        
        return report
    
    def get_field_report(self, site_name: str, field: str) -> Dict[str, Any]:
        """Get detailed report for a specific field."""
        if site_name not in self._field_stats:
            return {"error": "Site not found"}
        
        if field not in self._field_stats[site_name]:
            return {"error": "Field not found"}
        
        fs = self._field_stats[site_name][field]
        
        return {
            "field": field,
            "site": site_name,
            "total_extractions": fs.total_extractions,
            "success_rate": fs.success_rate,
            "fallback_rate": fs.fallback_rate,
            "selectors": [
                {
                    "selector": ss.selector,
                    "type": ss.selector_type,
                    "success_rate": ss.success_rate,
                    "total_attempts": ss.total_attempts,
                    "is_healthy": ss.is_healthy,
                    "fallback_position": ss.fallback_position,
                }
                for ss in sorted(
                    fs.selector_stats.values(),
                    key=lambda x: x.success_rate,
                    reverse=True
                )
            ],
        }


class DOMChangeDetector:
    """
    Detect structural changes in webpage DOM that might break selectors.
    """
    
    def __init__(self):
        self.snapshot_dir = Path(".dom_snapshots")
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        
        self._snapshots: Dict[str, Dict[str, Any]] = {}
    
    def _get_snapshot_path(self, site_name: str, page_type: str) -> Path:
        """Get path for DOM snapshot file."""
        return self.snapshot_dir / f"{site_name}_{page_type}_dom.json"
    
    def _extract_structure(self, html: str) -> Dict[str, Any]:
        """Extract DOM structure signature."""
        soup = BeautifulSoup(html, 'lxml')
        
        # Extract key structural elements
        structure = {
            "classes": defaultdict(int),
            "ids": [],
            "data_attrs": defaultdict(int),
            "tag_counts": defaultdict(int),
            "key_elements": [],
        }
        
        for element in soup.find_all(True):
            # Count tag types
            structure["tag_counts"][element.name] += 1
            
            # Collect classes
            for cls in element.get("class", []):
                structure["classes"][cls] += 1
            
            # Collect IDs
            if element.get("id"):
                structure["ids"].append(element.get("id"))
            
            # Collect data attributes
            for attr in element.attrs:
                if attr.startswith("data-"):
                    structure["data_attrs"][attr] += 1
        
        # Convert defaultdicts to regular dicts for JSON serialization
        structure["classes"] = dict(structure["classes"])
        structure["data_attrs"] = dict(structure["data_attrs"])
        structure["tag_counts"] = dict(structure["tag_counts"])
        
        # Generate hash
        structure_str = json.dumps(structure, sort_keys=True)
        structure["hash"] = hashlib.md5(structure_str.encode()).hexdigest()
        structure["timestamp"] = datetime.utcnow().isoformat()
        
        return structure
    
    def capture_snapshot(self, site_name: str, page_type: str, html: str) -> None:
        """Capture DOM snapshot for comparison."""
        structure = self._extract_structure(html)
        
        key = f"{site_name}_{page_type}"
        self._snapshots[key] = structure
        
        # Save to disk
        snapshot_path = self._get_snapshot_path(site_name, page_type)
        with open(snapshot_path, 'w', encoding='utf-8') as f:
            json.dump(structure, f, indent=2)
    
    def detect_changes(self, 
                      site_name: str, 
                      page_type: str, 
                      html: str) -> Dict[str, Any]:
        """
        Detect changes from last snapshot.
        Returns change report.
        """
        current = self._extract_structure(html)
        key = f"{site_name}_{page_type}"
        
        # Load previous snapshot
        snapshot_path = self._get_snapshot_path(site_name, page_type)
        if not snapshot_path.exists():
            # First time, save snapshot
            self.capture_snapshot(site_name, page_type, html)
            return {"status": "new", "message": "First snapshot captured"}
        
        with open(snapshot_path, 'r', encoding='utf-8') as f:
            previous = json.load(f)
        
        # Compare
        changes = {
            "status": "unchanged",
            "hash_changed": current["hash"] != previous.get("hash"),
            "changes": [],
        }
        
        if changes["hash_changed"]:
            changes["status"] = "changed"
            
            # Detect specific changes
            
            # New classes
            prev_classes = set(previous.get("classes", {}).keys())
            curr_classes = set(current["classes"].keys())
            
            new_classes = curr_classes - prev_classes
            removed_classes = prev_classes - curr_classes
            
            if new_classes:
                changes["changes"].append({
                    "type": "new_classes",
                    "items": list(new_classes)[:10],
                })
            
            if removed_classes:
                changes["changes"].append({
                    "type": "removed_classes",
                    "items": list(removed_classes)[:10],
                })
            
            # New/removed IDs
            prev_ids = set(previous.get("ids", []))
            curr_ids = set(current["ids"])
            
            new_ids = curr_ids - prev_ids
            removed_ids = prev_ids - curr_ids
            
            if new_ids:
                changes["changes"].append({
                    "type": "new_ids",
                    "items": list(new_ids)[:10],
                })
            
            if removed_ids:
                changes["changes"].append({
                    "type": "removed_ids",
                    "items": list(removed_ids)[:10],
                })
            
            # Tag count changes
            for tag, count in current["tag_counts"].items():
                prev_count = previous.get("tag_counts", {}).get(tag, 0)
                if abs(count - prev_count) > max(10, prev_count * 0.5):
                    changes["changes"].append({
                        "type": "tag_count_change",
                        "tag": tag,
                        "previous": prev_count,
                        "current": count,
                    })
        
        return changes
    
    def validate_selectors(self,
                          site_name: str,
                          selectors: Dict[str, List[Dict[str, str]]],
                          html: str) -> Dict[str, Any]:
        """
        Validate selectors against HTML.
        Returns validation report.
        """
        soup = BeautifulSoup(html, 'lxml')
        
        report = {
            "site": site_name,
            "timestamp": datetime.utcnow().isoformat(),
            "fields": {},
        }
        
        for field, field_selectors in selectors.items():
            field_report = {
                "working_selectors": [],
                "broken_selectors": [],
            }
            
            for sel_config in field_selectors:
                selector = sel_config.get("selector", "")
                sel_type = sel_config.get("type", "css")
                
                try:
                    if sel_type == "css":
                        elements = soup.select(selector)
                    else:
                        # XPath not directly supported by BeautifulSoup
                        elements = []
                    
                    if elements:
                        field_report["working_selectors"].append({
                            "selector": selector,
                            "type": sel_type,
                            "matches": len(elements),
                        })
                    else:
                        field_report["broken_selectors"].append({
                            "selector": selector,
                            "type": sel_type,
                        })
                        
                except Exception as e:
                    field_report["broken_selectors"].append({
                        "selector": selector,
                        "type": sel_type,
                        "error": str(e),
                    })
            
            report["fields"][field] = field_report
        
        return report


# Singleton instances
_selector_monitor: Optional[SelectorHealthMonitor] = None
_dom_detector: Optional[DOMChangeDetector] = None


def get_selector_monitor() -> SelectorHealthMonitor:
    """Get singleton selector monitor instance."""
    global _selector_monitor
    if _selector_monitor is None:
        _selector_monitor = SelectorHealthMonitor()
    return _selector_monitor


def get_dom_detector() -> DOMChangeDetector:
    """Get singleton DOM change detector instance."""
    global _dom_detector
    if _dom_detector is None:
        _dom_detector = DOMChangeDetector()
    return _dom_detector
