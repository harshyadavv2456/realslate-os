"""
RealSlate OS — Intelligence Engine v3
Fixed appreciation: uses per-sqft AND BHK-segmented comparison.
Requires minimum sample sizes. Smoothed rolling medians.
Self-improving: more data = more accurate signals.
"""

import duckdb
import json
import os
import sys
import glob
from datetime import datetime

# ── DRIVE CONFIG ─────────────────────────────────────────────────
from pathlib import Path
REPO_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR   = os.environ.get('RAW_DIR', str(REPO_ROOT / 'data' / 'raw'))
INTEL_DIR = os.environ.get('INTEL_DIR', str(REPO_ROOT / 'data' / 'intelligence'))

def nan_safe(v):
    """Convert NaN/None to None for JSON safety."""
    if v is None: return None
    try:
        f = float(v)
        if f != f: return None  # NaN check
        if abs(f) == float('inf'): return None
        return f
    except (TypeError, ValueError):
        return v

def safe_dict(d):
    """Recursively clean NaN from dict."""
    if isinstance(d, dict):
        return {k: safe_dict(v) for k, v in d.items()}
    if isinstance(d, list):
        return [safe_dict(i) for i in d]
    return nan_safe(d)

def compute():
    os.makedirs(INTEL_DIR, exist_ok=True)

    files = glob.glob(os.path.join(RAW_DIR, '*.parquet'))
    files = [f for f in files if 'price_events' not in f]
    if not files:
        print(f"FATAL: No parquet files found in {RAW_DIR}")
        print(f"  RAW_DIR={RAW_DIR} — did parse_and_export.py run first?")
        sys.exit(1)

    # Validate each file individually first. A single corrupted/truncated
    # parquet file (e.g. from antivirus locking a freshly-written file, or
    # a crashed write) should not take down the whole run - skip it and
    # keep going with the rest, but say so loudly so it gets noticed.
    con = duckdb.connect()
    good_files = []
    bad_files = []
    for f in files:
        fpath = f.replace(chr(92), '/')
        try:
            con.execute(f"SELECT COUNT(*) FROM read_parquet('{fpath}')").fetchone()
            good_files.append(f)
        except Exception as e:
            bad_files.append((f, str(e)))

    if bad_files:
        print(f"WARNING: {len(bad_files)} parquet file(s) failed validation and will be SKIPPED this run:")
        for f, err in bad_files:
            print(f"  SKIPPED: {f}")
            print(f"    reason: {err}")
        print("  These cities/months will be missing from this run's intelligence until")
        print("  re-scraped (parse_and_export.py will overwrite them next time it runs).")

    if not good_files:
        print("FATAL: no valid parquet files remain after validation.")
        sys.exit(1)

    fs = ", ".join([f"'{f.replace(chr(92), '/')}'" for f in good_files])
    con.execute(f"CREATE VIEW listings AS SELECT * FROM read_parquet([{fs}])")

    total = con.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
    print(f"Total records: {total}")
    cities = [r[0] for r in con.execute(
        "SELECT DISTINCT city FROM listings WHERE city IS NOT NULL ORDER BY city"
    ).fetchall()]
    print(f"Cities: {len(cities)}\n")

    all_summary = []

    for city in cities:
        print(f"Processing {city}...")

        # ── CITY SUMMARY ─────────────────────────────────────────
        cs = con.execute(f"""
            SELECT
                COUNT(*) as total_listings,
                COUNT(DISTINCT locality) as total_localities,
                MEDIAN(CASE WHEN listing_type='buy'  THEN price END) as median_buy_price,
                MEDIAN(CASE WHEN listing_type='rent' THEN price END) as median_rent_price,
                AVG(CASE WHEN listing_type='buy'  THEN price END) as avg_buy_price,
                MIN(CASE WHEN listing_type='buy'  THEN price END) as min_buy_price,
                MAX(CASE WHEN listing_type='buy'  THEN price END) as max_buy_price,
                COUNT(CASE WHEN listing_type='buy'  THEN 1 END) as buy_count,
                COUNT(CASE WHEN listing_type='rent' THEN 1 END) as rent_count,
                COUNT(CASE WHEN is_active=1 THEN 1 END) as active_count,
                SUM(CASE WHEN builder_name LIKE 'Owner%' THEN 1 ELSE 0 END) as owner_listings,
                SUM(CASE WHEN builder_name LIKE 'Agent%' THEN 1 ELSE 0 END) as agent_listings,
                AVG(CASE WHEN last_seen IS NOT NULL AND scraped_at IS NOT NULL
                    THEN DATEDIFF('day', CAST(scraped_at AS TIMESTAMP), CAST(last_seen AS TIMESTAMP))
                    END) as avg_days_on_market,
                COUNT(CASE WHEN CAST(scraped_at AS DATE) >= CURRENT_DATE - INTERVAL 7 DAY THEN 1 END) as new_this_week,
                COUNT(CASE WHEN CAST(scraped_at AS DATE) = CURRENT_DATE THEN 1 END) as new_today
            FROM listings WHERE city = '{city}'
        """).df().to_dict(orient='records')[0]

        # ── LOCALITY BASE STATS ───────────────────────────────────
        loc_stats = con.execute(f"""
            SELECT
                COALESCE(locality, 'Unknown') as locality,
                listing_type,
                COUNT(*) as listing_count,
                MEDIAN(price) as median_price,
                PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY price) as p25_price,
                PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY price) as p75_price,
                -- Per sqft metrics (only where area is valid: >100 sqft, <20000 sqft)
                COUNT(CASE WHEN area > 100 AND area < 20000 AND price > 0 THEN 1 END) as sqft_sample_count,
                MEDIAN(CASE WHEN area > 100 AND area < 20000 AND price > 0
                    THEN price / area END) as median_price_per_sqft,
                AVG(CASE WHEN area > 100 AND area < 20000 THEN area END) as avg_area_sqft,
                AVG(beds) as avg_beds,
                COUNT(CASE WHEN is_active=1 THEN 1 END) as active_count,
                SUM(CASE WHEN builder_name LIKE 'Owner%' THEN 1 ELSE 0 END) as owner_count,
                SUM(CASE WHEN builder_name LIKE 'Agent%' THEN 1 ELSE 0 END) as agent_count,
                AVG(CASE WHEN last_seen IS NOT NULL AND scraped_at IS NOT NULL
                    THEN DATEDIFF('day', CAST(scraped_at AS TIMESTAMP), CAST(last_seen AS TIMESTAMP))
                    END) as avg_days_on_market,
                COUNT(CASE WHEN CAST(scraped_at AS DATE) >= CURRENT_DATE - INTERVAL 7 DAY THEN 1 END) as new_this_week,
                AVG(lat) as lat,
                AVG(lng) as lng
            FROM listings
            WHERE city = '{city}' AND price > 0
            GROUP BY locality, listing_type
            HAVING COUNT(*) >= 3
            ORDER BY listing_count DESC
        """).df().to_dict(orient='records')

        # ── APPRECIATION — FIXED METHODOLOGY ─────────────────────
        # Strategy:
        # 1. If per-sqft data available (area populated): compare median ₹/sqft
        # 2. Else: compare within same BHK segment (1BHK vs 1BHK etc.)
        # 3. Require minimum 4 observations per week-segment
        # 4. Require at least 2 distinct weeks before showing any %
        # 5. Apply outlier cap: reject weekly change > ±30% as data artifact
        # 6. Final MoM: compare smoothed 4-week rolling median

        appreciation_map = {}

        # Method A: Per-sqft weekly trend (most accurate)
        try:
            sqft_weekly = con.execute(f"""
                WITH valid AS (
                    SELECT
                        COALESCE(locality, 'Unknown') as locality,
                        listing_type,
                        STRFTIME(CAST(scraped_at AS TIMESTAMP), '%Y-%W') as week_key,
                        DATE_TRUNC('week', CAST(scraped_at AS TIMESTAMP))::DATE as week_start,
                        price / area as price_per_sqft
                    FROM listings
                    WHERE city = '{city}'
                      AND price > 0
                      AND area > 100 AND area < 20000
                      AND locality IS NOT NULL AND locality != 'Unknown'
                      AND scraped_at IS NOT NULL
                ),
                weekly AS (
                    SELECT
                        locality, listing_type, week_key, week_start,
                        MEDIAN(price_per_sqft) as median_psf,
                        COUNT(*) as n
                    FROM valid
                    GROUP BY locality, listing_type, week_key, week_start
                    HAVING COUNT(*) >= 4
                ),
                with_lag AS (
                    SELECT *,
                        LAG(median_psf, 1) OVER (PARTITION BY locality, listing_type ORDER BY week_start) as psf_1w_ago,
                        LAG(median_psf, 4) OVER (PARTITION BY locality, listing_type ORDER BY week_start) as psf_4w_ago,
                        FIRST_VALUE(median_psf) OVER (PARTITION BY locality, listing_type ORDER BY week_start
                            ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) as psf_first,
                        COUNT(*) OVER (PARTITION BY locality, listing_type) as total_weeks
                    FROM weekly
                )
                SELECT * FROM with_lag
                WHERE week_start = (SELECT MAX(week_start) FROM weekly w2
                    WHERE w2.locality = with_lag.locality AND w2.listing_type = with_lag.listing_type)
                  AND total_weeks >= 2
                ORDER BY locality, listing_type
            """).df()

            for _, row in sqft_weekly.iterrows():
                key = f"{row['locality']}_{row['listing_type']}"
                curr = row['median_psf']
                prev_w = row['psf_1w_ago']
                prev_m = row['psf_4w_ago']
                first  = row['psf_first']

                def safe_pct(new, old, cap=30):
                    if old is None or old != old or old == 0: return None
                    if new is None or new != new: return None
                    v = (new - old) / old * 100
                    return round(v, 2) if abs(v) <= cap else None

                wow   = safe_pct(curr, prev_w)
                mom   = safe_pct(curr, prev_m)
                total = safe_pct(curr, first, cap=200)

                if mom is not None or wow is not None:
                    appreciation_map[key] = {
                        'wow_pct': wow,
                        'mom_pct': mom,
                        'total_pct': total,
                        'method': 'per_sqft',
                        'median_psf': round(float(curr), 0) if curr == curr else None,
                        'weeks_of_data': int(row['total_weeks']),
                    }
        except Exception as e:
            print(f"  Per-sqft appreciation error: {e}")

        # Method B: BHK-segmented for localities without sqft coverage
        try:
            bhk_weekly = con.execute(f"""
                WITH weekly AS (
                    SELECT
                        COALESCE(locality, 'Unknown') as locality,
                        listing_type,
                        CAST(beds AS INTEGER) as bhk,
                        STRFTIME(CAST(scraped_at AS TIMESTAMP), '%Y-%W') as week_key,
                        DATE_TRUNC('week', CAST(scraped_at AS TIMESTAMP))::DATE as week_start,
                        MEDIAN(price) as median_price,
                        COUNT(*) as n
                    FROM listings
                    WHERE city = '{city}'
                      AND price > 0
                      AND beds >= 1 AND beds <= 5
                      AND locality IS NOT NULL AND locality != 'Unknown'
                      AND scraped_at IS NOT NULL
                    GROUP BY locality, listing_type, bhk, week_key, week_start
                    HAVING COUNT(*) >= 4
                ),
                -- Only use most common BHK in each locality (avoids mix-shift)
                dominant_bhk AS (
                    SELECT locality, listing_type,
                        MODE() WITHIN GROUP (ORDER BY CAST(beds AS INTEGER)) as dom_bhk
                    FROM listings
                    WHERE city = '{city}' AND beds >= 1 AND beds <= 5 AND price > 0
                    GROUP BY locality, listing_type
                ),
                filtered AS (
                    SELECT w.* FROM weekly w
                    JOIN dominant_bhk d ON w.locality=d.locality
                        AND w.listing_type=d.listing_type AND w.bhk=d.dom_bhk
                ),
                with_lag AS (
                    SELECT *,
                        LAG(median_price,1) OVER (PARTITION BY locality,listing_type,bhk ORDER BY week_start) as p_1w,
                        LAG(median_price,4) OVER (PARTITION BY locality,listing_type,bhk ORDER BY week_start) as p_4w,
                        FIRST_VALUE(median_price) OVER (PARTITION BY locality,listing_type,bhk ORDER BY week_start
                            ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) as p_first,
                        COUNT(*) OVER (PARTITION BY locality,listing_type,bhk) as total_weeks
                    FROM filtered
                )
                SELECT * FROM with_lag
                WHERE week_start = (SELECT MAX(week_start) FROM filtered f2
                    WHERE f2.locality=with_lag.locality AND f2.listing_type=with_lag.listing_type
                      AND f2.bhk=with_lag.bhk)
                  AND total_weeks >= 2
            """).df()

            for _, row in bhk_weekly.iterrows():
                key = f"{row['locality']}_{row['listing_type']}"
                if key in appreciation_map:
                    continue  # per-sqft is better, don't overwrite

                curr  = row['median_price']
                prev_w = row['p_1w']
                prev_m = row['p_4w']
                first  = row['p_first']

                def safe_pct(new, old, cap=25):
                    if old is None or old != old or old == 0: return None
                    if new is None or new != new: return None
                    v = (new - old) / old * 100
                    return round(v, 2) if abs(v) <= cap else None

                wow   = safe_pct(curr, prev_w)
                mom   = safe_pct(curr, prev_m)
                total = safe_pct(curr, first, cap=200)

                if mom is not None or wow is not None:
                    appreciation_map[key] = {
                        'wow_pct': wow,
                        'mom_pct': mom,
                        'total_pct': total,
                        'method': f"bhk_{int(row['bhk'])}bhk_segmented",
                        'weeks_of_data': int(row['total_weeks']),
                    }
        except Exception as e:
            print(f"  BHK-segmented appreciation error: {e}")

        # Attach appreciation + confidence to locality_stats
        for row in loc_stats:
            key = f"{row['locality']}_{row['listing_type']}"
            appr = appreciation_map.get(key, {})
            row['wow_pct']    = appr.get('wow_pct')
            row['mom_pct']    = appr.get('mom_pct')
            row['total_appreciation_pct'] = appr.get('total_pct')
            row['appr_method']  = appr.get('method')
            row['weeks_of_data'] = appr.get('weeks_of_data', 0)
            row['median_price_per_sqft'] = nan_safe(row.get('median_price_per_sqft'))
            # Confidence: how reliable is this appreciation signal
            wks = appr.get('weeks_of_data', 0) or 0
            row['signal_confidence'] = (
                'high'   if wks >= 8  else
                'medium' if wks >= 4  else
                'low'    if wks >= 2  else
                'none'
            )

        # ── RENTAL YIELD ─────────────────────────────────────────
        yield_stats = con.execute(f"""
            WITH buy_data AS (
                SELECT locality,
                    MEDIAN(price) as buy_price,
                    AVG(lat) as lat, AVG(lng) as lng,
                    COUNT(*) as n
                FROM listings
                WHERE city = '{city}' AND listing_type='buy' AND price > 0
                GROUP BY locality HAVING COUNT(*) >= 3
            ),
            rent_data AS (
                SELECT locality,
                    MEDIAN(price) as rent_price,
                    COUNT(*) as n
                FROM listings
                WHERE city = '{city}' AND listing_type='rent' AND price > 0
                GROUP BY locality HAVING COUNT(*) >= 3
            )
            SELECT
                b.locality,
                b.buy_price, r.rent_price,
                ROUND((r.rent_price * 12 / b.buy_price) * 100, 2) as gross_yield_pct,
                b.lat, b.lng
            FROM buy_data b
            JOIN rent_data r ON b.locality = r.locality
            WHERE b.buy_price > 100000
              AND r.rent_price > 1000
              AND (r.rent_price * 12 / b.buy_price) BETWEEN 0.005 AND 0.15
            ORDER BY gross_yield_pct DESC
        """).df().to_dict(orient='records')

        # ── BHK BREAKDOWN ─────────────────────────────────────────
        bhk_stats = con.execute(f"""
            SELECT
                beds, listing_type,
                COUNT(*) as count,
                MEDIAN(price) as median_price,
                PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY price) as p25,
                PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY price) as p75,
                -- Per sqft for BHK bands too
                MEDIAN(CASE WHEN area > 100 AND area < 20000 THEN price/area END) as median_psf,
                AVG(CASE WHEN area > 100 AND area < 20000 THEN area END) as avg_area,
                AVG(CASE WHEN last_seen IS NOT NULL AND scraped_at IS NOT NULL
                    THEN DATEDIFF('day', CAST(scraped_at AS TIMESTAMP), CAST(last_seen AS TIMESTAMP))
                    END) as avg_days_on_market
            FROM listings
            WHERE city = '{city}'
              AND beds IS NOT NULL AND beds >= 1 AND beds <= 6
              AND price > 0
            GROUP BY beds, listing_type
            ORDER BY beds, listing_type
        """).df().to_dict(orient='records')

        # ── SUPPLY TREND ──────────────────────────────────────────
        supply_trend = con.execute(f"""
            SELECT
                COALESCE(locality, 'Unknown') as locality,
                STRFTIME(CAST(scraped_at AS TIMESTAMP), '%Y-%m-%d') as week,
                COUNT(*) as new_listings
            FROM listings
            WHERE city = '{city}' AND scraped_at IS NOT NULL
            GROUP BY locality, week
            ORDER BY locality, week
        """).df().to_dict(orient='records')

        # ── TOP APPRECIATING (for summary) ────────────────────────
        top_appreciating = sorted(
            [{'locality': k.rsplit('_buy',1)[0], **v}
             for k, v in appreciation_map.items()
             if k.endswith('_buy') and v.get('mom_pct') is not None],
            key=lambda x: x.get('mom_pct', 0) or 0, reverse=True
        )[:10]

        # ── WRITE CITY JSON ───────────────────────────────────────
        city_data = safe_dict({
            'city': city,
            'computed_at': datetime.now().isoformat(),
            'summary': cs,
            'locality_stats': loc_stats,
            'bhk_stats': bhk_stats,
            'yield_stats': yield_stats,
            'supply_trend': supply_trend,
            'top_appreciating': top_appreciating,
        })

        n_appr = sum(1 for v in appreciation_map.values() if v.get('mom_pct') is not None)
        n_psft = sum(1 for v in appreciation_map.values() if v.get('method') == 'per_sqft')
        print(f"  Localities: {len(loc_stats)} | Yield: {len(yield_stats)} | Appr: {n_appr} ({n_psft} per-sqft) | Done")

        path = os.path.join(INTEL_DIR, f"{city.lower().replace(' ','_')}.json")
        with open(path, 'w') as f:
            json.dump(city_data, f, indent=2, default=str)

        all_summary.append({
            'city': city,
            'total_listings': cs['total_listings'],
            'total_localities': cs['total_localities'],
            'median_buy_price': nan_safe(cs['median_buy_price']),
            'median_rent_price': nan_safe(cs['median_rent_price']),
            'avg_buy_price': nan_safe(cs['avg_buy_price']),
            'buy_count': cs['buy_count'],
            'rent_count': cs['rent_count'],
            'active_count': cs['active_count'],
            'new_this_week': cs['new_this_week'],
            'new_today': cs['new_today'],
            'avg_days_on_market': nan_safe(cs['avg_days_on_market']),
        })

    # Master summary
    with open(os.path.join(INTEL_DIR, 'all_cities.json'), 'w') as f:
        json.dump({'computed_at': datetime.now().isoformat(),
                   'total_records': total,
                   'cities': all_summary}, f, indent=2, default=str)

    con.close()
    print(f"\nDone. {len(cities)} cities processed.")

if __name__ == '__main__':
    try:
        compute()
    except Exception as e:
        print(f"FATAL: compute_intelligence failed: {e}")
        sys.exit(1)