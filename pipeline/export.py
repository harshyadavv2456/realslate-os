import sqlite3
import pandas as pd
import os
from datetime import datetime

DB_PATH = r'D:\RealSlate\realslate_core\realslate.db'
OUTPUT_DIR = r'D:\RealSlateOS\data\raw'

def export():
    print(f"[{datetime.now()}] Connecting to DB...")
    conn = sqlite3.connect(DB_PATH)

    # Export listings — the primary intelligence table
    print("Exporting listings...")
    listings = pd.read_sql("""
        SELECT 
            l.property_uid,
            l.source,
            l.city,
            l.listing_type,
            l.price,
            l.address,
            l.beds,
            l.bathrooms,
            l.area,
            l.property_type,
            l.builder_name,
            l.locality,
            l.scraped_at,
            l.last_seen,
            l.is_active,
            l.completeness_score,
            p.lat,
            p.lng,
            p.first_seen,
            p.status
        FROM listings l
        LEFT JOIN properties p ON l.property_uid = p.property_uid
        WHERE l.is_valid = 1
        AND l.price IS NOT NULL
        AND l.price > 0
    """, conn)
    print(f"Total listings: {len(listings)}")

    # Export price events — for appreciation tracking
    print("Exporting price events...")
    events = pd.read_sql("""
        SELECT 
            property_uid,
            event_type,
            old_value,
            new_value,
            value_numeric,
            timestamp
        FROM property_events
        WHERE event_type = 'price_change'
    """, conn)
    print(f"Total price events: {len(events)}")

    conn.close()

    # Save by city
    ym = datetime.now().strftime("%Y_%m")
    for city, group in listings.groupby("city"):
        city_clean = city.lower().strip().replace(" ", "_")
        path = os.path.join(OUTPUT_DIR, f"{city_clean}_{ym}.parquet")
        group.to_parquet(path, index=False, compression="snappy")
        print(f"  Saved {city}: {len(group)} records")

    # Save events separately
    if len(events) > 0:
        events.to_parquet(
            os.path.join(OUTPUT_DIR, "price_events.parquet"),
            index=False, compression="snappy"
        )
        print(f"  Saved price events: {len(events)} records")

    print(f"[{datetime.now()}] Export complete.")

if __name__ == "__main__":
    export()
