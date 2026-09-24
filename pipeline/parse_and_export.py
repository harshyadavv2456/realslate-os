import sqlite3
import re
import pandas as pd
import json
import os

from pathlib import Path
REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = os.environ.get('REALSLATE_DB', str(REPO_ROOT / 'crawler' / 'realslate.db'))
OUTPUT_DIR = os.environ.get('RAW_DIR', str(REPO_ROOT / 'data' / 'raw'))

def parse_address(address, city):
    if not address:
        return {}
    
    result = {}
    addr = str(address).strip()
    city_clean = str(city).strip().lower()
    
    # --- BHK ---
    bhk = re.search(r'(\d+)\s*BHK', addr, re.IGNORECASE)
    if bhk:
        result['beds_parsed'] = int(bhk.group(1))
    
    # --- Property Type ---
    type_map = {
        'Flat': ['flat', 'apartment'],
        'House': ['house', 'villa', 'bungalow', 'row house', 'rowhouse'],
        'Plot': ['plot', 'land'],
        'Office': ['office space', 'office'],
        'Shop': ['shop', 'showroom', 'retail'],
        'Studio': ['studio'],
        'Builder Floor': ['builder floor', 'builder_floor'],
        'Penthouse': ['penthouse'],
        'Farmhouse': ['farmhouse'],
    }
    addr_lower = addr.lower()
    for ptype, keywords in type_map.items():
        if any(k in addr_lower for k in keywords):
            result['property_type_parsed'] = ptype
            break
    
    # --- Listing Type ---
    if 'for rent' in addr_lower or 'for lease' in addr_lower:
        result['listing_type_parsed'] = 'rent'
    elif 'for sale' in addr_lower:
        result['listing_type_parsed'] = 'buy'
    
    # --- Locality Extraction ---
    # Only works on "X for Y in BUILDING, LOCALITY, CITY" pattern
    if ' in ' in addr_lower:
        # Get everything after " in "
        after_in = re.split(r'\s+in\s+', addr, maxsplit=1, flags=re.IGNORECASE)
        if len(after_in) > 1:
            remainder = after_in[1].strip()
            
            # Remove city from end (various forms)
            city_variants = [city_clean, city_clean.title(), city_clean.upper()]
            # Handle compound cities
            if city_clean == 'delhi ncr':
                city_variants += ['delhi', 'new delhi', 'ncr']
            elif city_clean == 'navi mumbai':
                city_variants += ['navi mumbai']
            
            for cv in city_variants:
                remainder = re.sub(rf',?\s*{re.escape(cv)}\s*$', '', remainder, flags=re.IGNORECASE).strip()
            
            # Split by comma — segments now are: [Building, Building(repeated), Locality] or [Building, Locality] or [Locality]
            segments = [s.strip() for s in remainder.split(',') if s.strip()]
            
            if segments:
                # Last segment is most likely the locality
                locality_candidate = segments[-1]
                
                # Validate — not just a number, not too short, not too long
                if (len(locality_candidate) > 2 and 
                    len(locality_candidate) < 80 and
                    not locality_candidate.isdigit()):
                    
                    # Clean trailing city name that snuck in
                    for cv in city_variants:
                        locality_candidate = re.sub(rf'\s+{re.escape(cv)}\s*$', '', locality_candidate, flags=re.IGNORECASE).strip()
                    
                    if len(locality_candidate) > 2:
                        result['locality_parsed'] = locality_candidate.title()
    
    return result

def run():
    print("Connecting to DB...")
    conn = sqlite3.connect(DB_PATH)
    
    df = pd.read_sql("""
        SELECT 
            l.id, l.property_uid, l.source, l.city, l.listing_type,
            l.price, l.address, l.beds, l.area, l.property_type,
            l.builder_name, l.locality, l.scraped_at, l.last_seen,
            l.is_active, l.completeness_score,
            p.lat, p.lng, p.first_seen, p.status
        FROM listings l
        LEFT JOIN properties p USING (property_uid)
        WHERE l.is_valid = 1 AND l.price IS NOT NULL AND l.price > 0
    """, conn)
    conn.close()
    
    print(f"Loaded {len(df)} records. Parsing addresses...")
    
    # Parse all addresses
    parsed = df.apply(lambda row: parse_address(row['address'], row['city']), axis=1)
    parsed_df = pd.DataFrame(list(parsed))
    
    # Merge parsed fields — only fill nulls, never overwrite existing data
    if 'beds_parsed' in parsed_df.columns:
        df['beds'] = df['beds'].combine_first(parsed_df['beds_parsed'])
    if 'property_type_parsed' in parsed_df.columns:
        df['property_type'] = df['property_type'].combine_first(parsed_df['property_type_parsed'])
    if 'locality_parsed' in parsed_df.columns:
        df['locality'] = df['locality'].combine_first(parsed_df['locality_parsed'])
    
    # Stats
    total = len(df)
    loc_filled = df['locality'].notna().sum()
    beds_filled = df['beds'].notna().sum()
    type_filled = df['property_type'].notna().sum()
    
    print(f"\nAfter parsing:")
    print(f"  locality      : {loc_filled}/{total} ({round(loc_filled/total*100,1)}%)")
    print(f"  beds          : {beds_filled}/{total} ({round(beds_filled/total*100,1)}%)")
    print(f"  property_type : {type_filled}/{total} ({round(type_filled/total*100,1)}%)")
    
    # Sample check
    print("\nSample parsed localities (mumbai):")
    sample = df[df['city']=='mumbai'][['address','locality','beds','property_type']].head(8)
    for _, row in sample.iterrows():
        print(f"  [{row['locality']}] | beds={row['beds']} | type={row['property_type']}")
        print(f"    {row['address'][:80]}")
    
    # Export by city
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    from datetime import datetime
    ym = datetime.now().strftime("%Y_%m")
    
    print("\nExporting enriched Parquet files...")
    for city, group in df.groupby('city'):
        city_clean = city.lower().strip().replace(' ', '_')
        path = os.path.join(OUTPUT_DIR, f"{city_clean}_{ym}.parquet")
        # Guard: a fresh/empty CI database must not clobber a fuller export.
        if os.path.exists(path) and not os.environ.get('FORCE_EXPORT'):
            prev_rows = len(pd.read_parquet(path, columns=['id']))
            if len(group) < prev_rows * 0.9:
                print(f"  {city:<15} SKIPPED: {len(group)} rows < 90% of existing {prev_rows}")
                continue
        group.to_parquet(path, index=False, compression='snappy')
        
        loc_pct = round(group['locality'].notna().mean()*100, 1)
        print(f"  {city:<15} {len(group):>6} records | locality {loc_pct}% filled")
    
    print("\nDone. Run compute_intelligence.py next.")

if __name__ == '__main__':
    run()
