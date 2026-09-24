"""
RealSlate Core - Data Validation
Validates extracted data for completeness, sanity, and format.
"""

import re
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime

from loguru import logger

from .utils import get_config


class ValidationResult:
    """Result of data validation."""
    
    def __init__(self):
        self.is_valid = True
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.completeness_score = 0.0
        self.field_scores: Dict[str, float] = {}
    
    def add_error(self, message: str) -> None:
        """Add validation error."""
        self.errors.append(message)
        self.is_valid = False
    
    def add_warning(self, message: str) -> None:
        """Add validation warning."""
        self.warnings.append(message)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "errors": self.errors,
            "warnings": self.warnings,
            "completeness_score": self.completeness_score,
            "field_scores": self.field_scores,
        }


class DataValidator:
    """
    Production-grade data validator with:
    - Field presence validation
    - Value range validation
    - Format validation
    - Completeness scoring
    """
    
    def __init__(self, config: Optional[dict] = None):
        self.config = config or get_config()
        self.validation_config = self.config.get("validation", {})
        
        # Field weights for completeness scoring
        self.field_weights = {
            "price": 0.25,
            "address": 0.25,
            "beds": 0.10,
            "area": 0.15,
            "detail_link": 0.10,
            "property_type": 0.05,
            "builder_name": 0.05,
            "posted_date": 0.05,
        }
        
        # Validation stats
        self._total_validated = 0
        self._valid_count = 0
        self._invalid_count = 0
        self._validation_errors: Dict[str, int] = {}
    
    def validate(self, data: Dict[str, Any]) -> ValidationResult:
        """
        Validate a listing data dictionary.
        """
        result = ValidationResult()
        self._total_validated += 1
        
        # Required field validation
        self._validate_required_fields(data, result)
        
        # Price validation
        self._validate_price(data, result)
        
        # Area validation
        self._validate_area(data, result)
        
        # Address validation
        self._validate_address(data, result)
        
        # Beds validation
        self._validate_beds(data, result)
        
        # URL validation
        self._validate_url(data, result)
        
        # Calculate completeness score
        result.completeness_score = self._calculate_completeness(data, result)
        
        # Check minimum completeness
        min_completeness = self.validation_config.get("min_completeness_score", 0.4)
        if result.completeness_score < min_completeness:
            result.add_warning(
                f"Completeness score ({result.completeness_score:.2f}) below threshold ({min_completeness})"
            )
        
        # Update stats
        if result.is_valid:
            self._valid_count += 1
        else:
            self._invalid_count += 1
            for error in result.errors:
                error_type = error.split(":")[0] if ":" in error else error
                self._validation_errors[error_type] = self._validation_errors.get(error_type, 0) + 1
        
        return result
    
    def _validate_required_fields(
        self,
        data: Dict[str, Any],
        result: ValidationResult
    ) -> None:
        """Validate required fields are present."""
        required = self.validation_config.get("required_fields", ["price", "address"])
        
        for field in required:
            if field not in data or data[field] is None or data[field] == "":
                result.add_error(f"Required field missing: {field}")
    
    def _validate_price(
        self,
        data: Dict[str, Any],
        result: ValidationResult
    ) -> None:
        """Validate price field."""
        price = data.get("price")
        
        if price is None:
            result.field_scores["price"] = 0.0
            return
        
        # Type check
        if not isinstance(price, (int, float)):
            result.add_error(f"Price: Invalid type {type(price)}")
            result.field_scores["price"] = 0.0
            return
        
        # Range check
        min_price = self.validation_config.get("min_price", 100000)  # 1 Lakh
        max_price = self.validation_config.get("max_price", 10000000000)  # 1000 Cr
        
        if price < min_price:
            result.add_warning(f"Price: Suspiciously low ({price})")
            result.field_scores["price"] = 0.5
        elif price > max_price:
            result.add_warning(f"Price: Suspiciously high ({price})")
            result.field_scores["price"] = 0.5
        else:
            result.field_scores["price"] = 1.0
    
    def _validate_area(
        self,
        data: Dict[str, Any],
        result: ValidationResult
    ) -> None:
        """Validate area field."""
        area = data.get("area")
        
        if area is None:
            result.field_scores["area"] = 0.0
            return
        
        # Type check
        if not isinstance(area, (int, float)):
            result.add_error(f"Area: Invalid type {type(area)}")
            result.field_scores["area"] = 0.0
            return
        
        # Range check
        min_area = self.validation_config.get("min_area", 50)
        max_area = self.validation_config.get("max_area", 1000000)
        
        if area < min_area:
            result.add_warning(f"Area: Suspiciously small ({area})")
            result.field_scores["area"] = 0.5
        elif area > max_area:
            result.add_warning(f"Area: Suspiciously large ({area})")
            result.field_scores["area"] = 0.5
        else:
            result.field_scores["area"] = 1.0
    
    def _validate_address(
        self,
        data: Dict[str, Any],
        result: ValidationResult
    ) -> None:
        """Validate address field."""
        address = data.get("address")
        
        if not address:
            result.field_scores["address"] = 0.0
            return
        
        # Length check
        if len(address) < 5:
            result.add_warning(f"Address: Too short ({len(address)} chars)")
            result.field_scores["address"] = 0.3
        elif len(address) > 500:
            result.add_warning(f"Address: Too long ({len(address)} chars)")
            result.field_scores["address"] = 0.7
        else:
            result.field_scores["address"] = 1.0
        
        # Check for generic/invalid addresses
        invalid_patterns = [
            r'^test',
            r'^sample',
            r'^n/?a$',
            r'^-+$',
            r'^\.+$',
        ]
        
        address_lower = address.lower().strip()
        for pattern in invalid_patterns:
            if re.match(pattern, address_lower):
                result.add_warning(f"Address: Appears invalid ({address[:30]})")
                result.field_scores["address"] = 0.0
                break
    
    def _validate_beds(
        self,
        data: Dict[str, Any],
        result: ValidationResult
    ) -> None:
        """Validate bedroom count."""
        beds = data.get("beds")
        
        if beds is None:
            result.field_scores["beds"] = 0.0
            return
        
        # Type check
        if not isinstance(beds, int):
            try:
                beds = int(beds)
            except (ValueError, TypeError):
                result.add_error(f"Beds: Invalid type {type(data.get('beds'))}")
                result.field_scores["beds"] = 0.0
                return
        
        # Range check (0-20 BHK is reasonable)
        if beds < 0 or beds > 20:
            result.add_warning(f"Beds: Unusual value ({beds})")
            result.field_scores["beds"] = 0.5
        else:
            result.field_scores["beds"] = 1.0
    
    def _validate_url(
        self,
        data: Dict[str, Any],
        result: ValidationResult
    ) -> None:
        """Validate detail URL."""
        url = data.get("detail_link")
        
        if not url:
            result.field_scores["detail_link"] = 0.0
            return
        
        # Basic URL validation
        url_pattern = r'^https?://[^\s/$.?#].[^\s]*$'
        
        if not re.match(url_pattern, url, re.IGNORECASE):
            # Check if it's a relative URL
            if url.startswith('/'):
                result.field_scores["detail_link"] = 0.8
            else:
                result.add_warning(f"URL: Invalid format ({url[:50]})")
                result.field_scores["detail_link"] = 0.0
        else:
            result.field_scores["detail_link"] = 1.0
    
    def _calculate_completeness(
        self,
        data: Dict[str, Any],
        result: ValidationResult
    ) -> float:
        """Calculate overall completeness score."""
        total_weight = 0.0
        weighted_score = 0.0
        
        for field, weight in self.field_weights.items():
            score = result.field_scores.get(field)
            
            if score is None:
                # Field not validated, check if present
                if field in data and data[field] is not None:
                    score = 1.0
                else:
                    score = 0.0
            
            weighted_score += score * weight
            total_weight += weight
        
        return weighted_score / total_weight if total_weight > 0 else 0.0
    
    def validate_batch(
        self,
        listings: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Validate a batch of listings.
        Returns (valid_listings, invalid_listings).
        """
        valid = []
        invalid = []
        
        for listing in listings:
            result = self.validate(listing)
            
            # Add validation result to listing
            listing["is_valid"] = result.is_valid
            listing["completeness_score"] = result.completeness_score
            listing["validation_errors"] = result.errors
            listing["validation_warnings"] = result.warnings
            
            if result.is_valid:
                valid.append(listing)
            else:
                invalid.append(listing)
        
        return valid, invalid
    
    def get_stats(self) -> Dict[str, Any]:
        """Get validation statistics."""
        return {
            "total_validated": self._total_validated,
            "valid_count": self._valid_count,
            "invalid_count": self._invalid_count,
            "valid_rate": self._valid_count / max(1, self._total_validated),
            "top_errors": dict(
                sorted(
                    self._validation_errors.items(),
                    key=lambda x: x[1],
                    reverse=True
                )[:10]
            ),
        }
    
    def reset_stats(self) -> None:
        """Reset validation statistics."""
        self._total_validated = 0
        self._valid_count = 0
        self._invalid_count = 0
        self._validation_errors.clear()


class ListingQualityScorer:
    """
    Calculate quality scores for listings based on multiple factors.
    """
    
    def __init__(self):
        self.quality_weights = {
            "completeness": 0.30,
            "freshness": 0.20,
            "price_reasonability": 0.15,
            "description_quality": 0.15,
            "image_count": 0.10,
            "source_reliability": 0.10,
        }
    
    def score(self, listing: Dict[str, Any], source_config: dict = None) -> float:
        """Calculate overall quality score (0-1)."""
        scores = {}
        
        # Completeness score (from validation)
        scores["completeness"] = listing.get("completeness_score", 0.5)
        
        # Freshness score
        scores["freshness"] = self._score_freshness(listing)
        
        # Price reasonability
        scores["price_reasonability"] = self._score_price_reasonability(listing)
        
        # Description quality
        scores["description_quality"] = self._score_description(listing)
        
        # Image count (if available)
        scores["image_count"] = self._score_images(listing)
        
        # Source reliability
        scores["source_reliability"] = self._score_source(listing, source_config)
        
        # Calculate weighted average
        total_score = 0.0
        total_weight = 0.0
        
        for factor, weight in self.quality_weights.items():
            if factor in scores:
                total_score += scores[factor] * weight
                total_weight += weight
        
        return total_score / total_weight if total_weight > 0 else 0.5
    
    def _score_freshness(self, listing: Dict[str, Any]) -> float:
        """Score based on posting date freshness."""
        posted_date = listing.get("posted_date")
        
        if not posted_date:
            return 0.5  # Unknown freshness
        
        try:
            if isinstance(posted_date, str):
                posted = datetime.fromisoformat(posted_date.replace('Z', '+00:00'))
            else:
                posted = posted_date
            
            days_old = (datetime.utcnow() - posted.replace(tzinfo=None)).days
            
            # Score based on age
            if days_old <= 1:
                return 1.0
            elif days_old <= 7:
                return 0.9
            elif days_old <= 30:
                return 0.7
            elif days_old <= 90:
                return 0.5
            else:
                return 0.3
                
        except Exception:
            return 0.5
    
    def _score_price_reasonability(self, listing: Dict[str, Any]) -> float:
        """Score price reasonability based on area."""
        price = listing.get("price")
        area = listing.get("area")
        
        if not price or not area or area == 0:
            return 0.5
        
        # Price per sq.ft
        price_per_sqft = price / area
        
        # Indian market typical ranges (very rough)
        # Tier 1 cities: 5000-50000/sqft
        # Tier 2 cities: 2000-20000/sqft
        
        if 1000 <= price_per_sqft <= 100000:
            return 1.0
        elif 500 <= price_per_sqft <= 200000:
            return 0.7
        else:
            return 0.3
    
    def _score_description(self, listing: Dict[str, Any]) -> float:
        """Score description quality."""
        desc = listing.get("description", "")
        
        if not desc:
            return 0.3
        
        length = len(desc)
        
        if length < 50:
            return 0.4
        elif length < 200:
            return 0.6
        elif length < 500:
            return 0.8
        else:
            return 1.0
    
    def _score_images(self, listing: Dict[str, Any]) -> float:
        """Score based on image availability."""
        images = listing.get("images", [])
        image_count = listing.get("image_count", len(images) if images else 0)
        
        if image_count == 0:
            return 0.3
        elif image_count < 3:
            return 0.6
        elif image_count < 10:
            return 0.8
        else:
            return 1.0
    
    def _score_source(self, listing: Dict[str, Any], source_config: dict = None) -> float:
        """Score based on source reliability."""
        source = listing.get("source", "")
        
        # Default reliability scores
        source_scores = {
            "99acres": 0.9,
            "magicbricks": 0.9,
            "housing": 0.85,
            "nobroker": 0.8,
            "proptiger": 0.85,
            "squareyards": 0.8,
            "commonfloor": 0.75,
        }
        
        return source_scores.get(source.lower(), 0.7)
