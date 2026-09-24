#!/usr/bin/env python3
"""
RealSlate Core - Data Export Script
Export crawled data to various formats.

Usage:
    python export_data.py --format csv --output exports/listings.csv
    python export_data.py --format json --days 7
    python export_data.py --format parquet --site 99acres
"""

import sys
import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from loguru import logger
from sqlalchemy import and_

from core.utils import setup_logging, get_config
from core.db import DatabaseManager, Listing, Property, PropertyEvent


class DataExporter:
    """Export crawled data to various formats."""
    
    def __init__(self):
        self.db = DatabaseManager()
        self.db.initialize()
        
        self.export_dir = Path("exports")
        self.export_dir.mkdir(exist_ok=True)
    
    def export_listings(
        self,
        format: str = "csv",
        output: str = None,
        days: int = None,
        site: str = None,
        city: str = None,
        limit: int = None
    ) -> str:
        """Export listings data."""
        logger.info(f"Exporting listings to {format}...")
        
        # Build query and convert to dicts while in session
        listings = []
        with self.db.session() as session:
            query = session.query(Listing)
            
            if days:
                cutoff = datetime.utcnow() - timedelta(days=days)
                query = query.filter(Listing.scraped_at > cutoff)
            
            if site:
                query = query.filter(Listing.source == site)
            
            if city:
                query = query.filter(Listing.city == city)
            
            if limit:
                query = query.limit(limit)
            
            for l in query.all():
                listings.append({
                    'id': l.id,
                    'property_uid': l.property_uid,
                    'source': l.source,
                    'city': l.city,
                    'price': l.price,
                    'address': l.address,
                    'beds': l.beds,
                    'bathrooms': l.bathrooms,
                    'area': l.area,
                    'url': l.url,
                    'property_type': l.property_type,
                    'builder_name': l.builder_name,
                    'scraped_at': l.scraped_at,
                })
        
        logger.info(f"Found {len(listings)} listings")
        
        # Generate output path
        if not output:
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            output = self.export_dir / f"listings_{timestamp}.{format}"
        
        # Export based on format
        if format == "csv":
            return self._export_csv_from_dicts(listings, output)
        elif format == "json":
            return self._export_json_from_dicts(listings, output)
        elif format == "parquet":
            return self._export_parquet_from_dicts(listings, output)
        else:
            raise ValueError(f"Unsupported format: {format}")
    
    def export_properties(
        self,
        format: str = "csv",
        output: str = None,
        city: str = None
    ) -> str:
        """Export canonical properties."""
        logger.info(f"Exporting properties to {format}...")
        
        with self.db.session() as session:
            query = session.query(Property)
            
            if city:
                query = query.filter(Property.city == city)
            
            properties = query.all()
        
        logger.info(f"Found {len(properties)} properties")
        
        if not output:
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            output = self.export_dir / f"properties_{timestamp}.{format}"
        
        if format == "csv":
            return self._export_properties_csv(properties, output)
        elif format == "json":
            return self._export_properties_json(properties, output)
        else:
            raise ValueError(f"Unsupported format: {format}")
    
    def export_price_history(
        self,
        property_uid: str = None,
        output: str = None
    ) -> str:
        """Export price history events."""
        logger.info("Exporting price history...")
        
        with self.db.session() as session:
            query = session.query(PropertyEvent).filter(
                PropertyEvent.event_type == "price_change"
            )
            
            if property_uid:
                query = query.filter(PropertyEvent.property_uid == property_uid)
            
            events = query.order_by(PropertyEvent.timestamp).all()
        
        logger.info(f"Found {len(events)} price events")
        
        if not output:
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            output = self.export_dir / f"price_history_{timestamp}.csv"
        
        return self._export_events_csv(events, output)
    
    def _export_csv_from_dicts(self, listings: list, output: str) -> str:
        """Export to CSV from list of dicts."""
        import csv
        
        output = Path(output)
        
        with open(output, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            
            # Header
            writer.writerow([
                'id', 'property_uid', 'source', 'city', 'price', 
                'address', 'beds', 'bathrooms', 'area', 'url',
                'property_type', 'builder_name', 'scraped_at'
            ])
            
            # Data
            for l in listings:
                writer.writerow([
                    l['id'], l['property_uid'], l['source'], l['city'], l['price'],
                    l['address'], l['beds'], l['bathrooms'], l['area'], l['url'],
                    l['property_type'], l['builder_name'], l['scraped_at']
                ])
        
        logger.info(f"Exported to {output}")
        return str(output)
    
    def _export_json_from_dicts(self, listings: list, output: str) -> str:
        """Export to JSON from list of dicts."""
        output = Path(output)
        
        # Convert datetime to string
        for l in listings:
            if l.get('scraped_at'):
                l['scraped_at'] = l['scraped_at'].isoformat() if hasattr(l['scraped_at'], 'isoformat') else str(l['scraped_at'])
        
        with open(output, 'w', encoding='utf-8') as f:
            json.dump(listings, f, indent=2, ensure_ascii=False, default=str)
        
        logger.info(f"Exported to {output}")
        return str(output)
    
    def _export_parquet_from_dicts(self, listings: list, output: str) -> str:
        """Export to Parquet (requires pandas and pyarrow)."""
        try:
            import pandas as pd
        except ImportError:
            raise ImportError("pandas required for parquet export: pip install pandas pyarrow")
        
        output = Path(output)
        
        df = pd.DataFrame(listings)
        df.to_parquet(output, index=False)
        
        logger.info(f"Exported to {output}")
        return str(output)
    
    def _export_properties_csv(self, properties: list, output: str) -> str:
        """Export properties to CSV."""
        import csv
        
        output = Path(output)
        
        with open(output, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            
            writer.writerow([
                'id', 'property_uid', 'canonical_address', 'city',
                'current_price', 'current_area', 'beds', 'bathrooms',
                'property_type', 'lat', 'lng', 'first_seen', 'last_seen',
                'sources', 'listing_count'
            ])
            
            for p in properties:
                writer.writerow([
                    p.id, p.property_uid, p.canonical_address, p.city,
                    p.current_price, p.current_area, p.beds, p.bathrooms,
                    p.property_type, p.lat, p.lng, p.first_seen, p.last_seen,
                    json.dumps(p.sources) if p.sources else '',
                    p.listing_count
                ])
        
        logger.info(f"Exported to {output}")
        return str(output)
    
    def _export_properties_json(self, properties: list, output: str) -> str:
        """Export properties to JSON."""
        output = Path(output)
        
        data = [{
            'id': p.id,
            'property_uid': p.property_uid,
            'canonical_address': p.canonical_address,
            'city': p.city,
            'current_price': p.current_price,
            'current_area': p.current_area,
            'beds': p.beds,
            'bathrooms': p.bathrooms,
            'property_type': p.property_type,
            'lat': p.lat,
            'lng': p.lng,
            'first_seen': p.first_seen.isoformat() if p.first_seen else None,
            'last_seen': p.last_seen.isoformat() if p.last_seen else None,
            'sources': p.sources,
            'source_urls': p.source_urls,
            'listing_count': p.listing_count
        } for p in properties]
        
        with open(output, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Exported to {output}")
        return str(output)
    
    def _export_events_csv(self, events: list, output: str) -> str:
        """Export events to CSV."""
        import csv
        
        output = Path(output)
        
        with open(output, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            
            writer.writerow([
                'id', 'property_uid', 'event_type', 
                'old_value', 'new_value', 'source', 'timestamp'
            ])
            
            for e in events:
                writer.writerow([
                    e.id, e.property_uid, e.event_type,
                    e.old_value, e.new_value, e.source, e.timestamp
                ])
        
        logger.info(f"Exported to {output}")
        return str(output)


def main():
    parser = argparse.ArgumentParser(description="RealSlate Data Export")
    
    parser.add_argument(
        "--type",
        choices=["listings", "properties", "prices"],
        default="listings",
        help="Data type to export"
    )
    parser.add_argument(
        "--format", "-f",
        choices=["csv", "json", "parquet"],
        default="csv",
        help="Output format"
    )
    parser.add_argument(
        "--output", "-o",
        help="Output file path"
    )
    parser.add_argument(
        "--days", "-d",
        type=int,
        help="Export data from last N days"
    )
    parser.add_argument(
        "--site", "-s",
        help="Filter by site"
    )
    parser.add_argument(
        "--city", "-c",
        help="Filter by city"
    )
    parser.add_argument(
        "--limit", "-l",
        type=int,
        help="Limit number of records"
    )
    
    args = parser.parse_args()
    
    setup_logging(level="INFO")
    
    exporter = DataExporter()
    
    try:
        if args.type == "listings":
            output = exporter.export_listings(
                format=args.format,
                output=args.output,
                days=args.days,
                site=args.site,
                city=args.city,
                limit=args.limit
            )
        elif args.type == "properties":
            output = exporter.export_properties(
                format=args.format,
                output=args.output,
                city=args.city
            )
        elif args.type == "prices":
            output = exporter.export_price_history(output=args.output)
        
        print(f"\nExport complete: {output}")
        
    except Exception as e:
        logger.error(f"Export failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
