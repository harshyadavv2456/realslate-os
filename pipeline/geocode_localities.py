"""
RealSlate OS — Locality Geocoder
Run once: py geocode_localities.py
Adds lat/lng to every locality in all city intelligence JSONs.
Uses Nominatim (OpenStreetMap) — free, no API key needed.
"""

import json
import time
import os
import sys
import requests

# ── DRIVE CONFIG ─────────────────────────────────────────────────
from pathlib import Path
REPO_ROOT = Path(__file__).resolve().parent.parent
INTEL_DIR = os.environ.get('INTEL_DIR', str(REPO_ROOT / 'data' / 'intelligence'))
GEOCACHE_FILE = os.environ.get('GEOCACHE_FILE', str(REPO_ROOT / 'data' / 'geocodes' / 'cache.json'))

# Known city coordinates — used as search bias
CITY_COORDS = {
    'mumbai': (19.0760, 72.8777),
    'delhi ncr': (28.6139, 77.2090),
    'bangalore': (12.9716, 77.5946),
    'hyderabad': (17.3850, 78.4867),
    'chennai': (13.0827, 80.2707),
    'pune': (18.5204, 73.8567),
    'kolkata': (22.5726, 88.3639),
    'ahmedabad': (23.0225, 72.5714),
    'gurgaon': (28.4595, 77.0266),
    'noida': (28.5355, 77.3910),
    'thane': (19.2183, 72.9781),
    'navi mumbai': (19.0368, 73.0158),
    'ghaziabad': (28.6692, 77.4538),
    'chandigarh': (30.7333, 76.7794),
    'lucknow': (26.8467, 80.9462),
    'jaipur': (26.9124, 75.7873),
    'surat': (21.1702, 72.8311),
    'nagpur': (21.1458, 79.0882),
    'indore': (22.7196, 75.8577),
    'coimbatore': (11.0168, 76.9558),
    'kochi': (9.9312, 76.2673),
    'vadodara': (22.3072, 73.1812),
    'mysore': (12.2958, 76.6394),
    'bhopal': (23.2599, 77.4126),
    'visakhapatnam': (17.6868, 83.2185),
}


def load_cache():
    os.makedirs(os.path.dirname(GEOCACHE_FILE), exist_ok=True)
    if not os.path.exists(GEOCACHE_FILE):
        return {}

    with open(GEOCACHE_FILE, encoding='utf-8') as f:
        raw = f.read()

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        # Self-heal: recover the first complete valid JSON object in the
        # file and discard anything after it (trailing garbage from a
        # previous interrupted/locked write - e.g. antivirus scanning a
        # freshly-written file). Preserves existing geocodes instead of
        # crashing the whole pipeline or wiping the cache.
        print(f"  WARNING: cache.json corrupted ({e}). Attempting auto-recovery...")
        decoder = json.JSONDecoder()
        try:
            obj, end_idx = decoder.raw_decode(raw)
        except json.JSONDecodeError:
            print("  Could not recover any valid data from cache.json. Starting with empty cache.")
            backup = GEOCACHE_FILE + '.corrupted'
            try:
                import shutil
                shutil.copy2(GEOCACHE_FILE, backup)
                print(f"  Corrupted file backed up to {backup}")
            except Exception:
                pass
            return {}

        discarded = len(raw) - end_idx
        print(f"  Recovered {len(obj)} cache entries, discarded {discarded} trailing corrupt bytes.")
        # Write back the cleaned version immediately so this doesn't
        # need re-recovering on the next run.
        try:
            with open(GEOCACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump(obj, f, indent=2)
        except Exception as write_err:
            print(f"  WARNING: could not save cleaned cache back to disk: {write_err}")
        return obj


def save_cache(cache):
    with open(GEOCACHE_FILE, 'w') as f:
        json.dump(cache, f, indent=2)


def geocode(locality, city, cache):
    key = f"{locality.lower().strip()}_{city.lower().strip()}"
    if key in cache:
        return cache[key]

    city_display = city.replace(' ncr', '').title()
    query = f"{locality}, {city_display}, India"

    try:
        resp = requests.get(
            'https://nominatim.openstreetmap.org/search',
            params={'q': query, 'format': 'json', 'limit': 1, 'countrycodes': 'in'},
            headers={'User-Agent': 'RealSlateOS-geocoder/1.0 (realslate.in)'},
            timeout=8
        )
        results = resp.json()
        if results:
            lat = float(results[0]['lat'])
            lng = float(results[0]['lon'])

            # Sanity check: result should be within ~100km of city center
            city_lat, city_lng = CITY_COORDS.get(city, (20.0, 78.0))
            dist = ((lat - city_lat)**2 + (lng - city_lng)**2) ** 0.5
            if dist < 2.0:  # roughly 200km in degree-space
                cache[key] = {'lat': lat, 'lng': lng}
                print(f"  ✓ {locality}, {city} → {lat:.4f}, {lng:.4f}")
            else:
                cache[key] = None
                print(f"  ✗ {locality}, {city} → result too far ({dist:.2f}°), skipped")
        else:
            cache[key] = None
            print(f"  - {locality}, {city} → not found")

    except Exception as e:
        print(f"  ! {locality}: {e}")
        return None

    time.sleep(1.1)  # Nominatim rate limit: 1 req/sec
    return cache.get(key)


def process_city_file(city_name, cache):
    path = os.path.join(INTEL_DIR, f"{city_name.replace(' ', '_')}.json")
    if not os.path.exists(path):
        print(f"File not found: {path}")
        return

    with open(path) as f:
        data = json.load(f)

    localities_done = 0
    localities_skipped = 0
    new_geocodes = 0

    for row in data.get('locality_stats', []):
        loc = row.get('locality', '')

        # Skip unknown/empty
        if not loc or loc == 'Unknown' or len(loc) < 3:
            row['lat'] = None
            row['lng'] = None
            localities_skipped += 1
            continue

        # Already has valid coordinates
        existing_lat = row.get('lat')
        if existing_lat and str(existing_lat) not in ('NaN', 'null', 'None', '') and existing_lat == existing_lat:
            localities_done += 1
            continue

        # Geocode it
        result = geocode(loc, city_name, cache)
        if result:
            row['lat'] = result['lat']
            row['lng'] = result['lng']
            new_geocodes += 1
        else:
            row['lat'] = None
            row['lng'] = None

        # Save cache every 10 geocodes
        if new_geocodes % 10 == 0 and new_geocodes > 0:
            save_cache(cache)

    # Also add yield stats geocodes (locality name only)
    for row in data.get('yield_stats', []):
        loc = row.get('locality', '')
        if not loc or loc == 'Unknown':
            continue
        key = f"{loc.lower().strip()}_{city_name.lower().strip()}"
        result = cache.get(key)
        if result:
            row['lat'] = result['lat']
            row['lng'] = result['lng']

    # Save updated JSON
    with open(path, 'w') as f:
        json.dump(data, f, indent=2, default=str)

    save_cache(cache)
    print(f"  → {city_name}: {new_geocodes} new geocodes, {localities_done} already done, {localities_skipped} skipped")


def main():
    print("RealSlate OS — Locality Geocoder")
    print("=" * 50)

    cache = load_cache()
    print(f"Cache loaded: {len(cache)} entries\n")

    cities = list(CITY_COORDS.keys())

    for i, city in enumerate(cities):
        print(f"\n[{i+1}/{len(cities)}] Processing {city}...")
        process_city_file(city, cache)

    print(f"\n{'='*50}")
    print(f"Done. Cache now has {len(cache)} entries.")
    print(f"Run serve.py next to start the dashboard server.")


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f"WARNING: geocode_localities failed (non-fatal, existing geocodes still used): {e}")
        sys.exit(0)  # non-fatal — .bat marks this step continue-on-error anyway
