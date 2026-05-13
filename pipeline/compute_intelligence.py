"""
RealSlate OS — Intelligence Computation Engine v2
Computes all metrics including price appreciation over time.
Run after parse_and_export.py daily.
"""

import duckdb
import json
import os
import glob
from datetime import datetime

RAW_DIR = r'D:\RealSlateOS\data\raw'
INTEL_DIR = r'D:\RealSlateOS\data\intelligence'

def compute():
    os.makedirs(INTEL_DIR, exist_ok=True)

    files = glob.glob(f"{RAW_DIR}\\*.parquet")
    files = [f for f in files if 'price_events' not in f]

    if not files:
        print("No parquet files found.")
        return

    con = duckdb.connect()
    files_str = ", ".join([f"'{f.replace(chr(92), '/')}'" for f in files])
    con.execute(f"CREATE VIEW listings AS SELECT * FROM read_parquet([{files_str}])")

    total = con.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
    print(f"Total records: {total}")

    cities = [r[0] for r in con.execute(
        "SELECT DISTINCT city FROM listings WHERE city IS NOT NULL ORDER BY city"
    ).fetchall()]
    print(f"Cities: {len(cities)}\n")

    all_summary = []

    for city in cities:
        print(f"Processing {city}...")

        # ── CITY SUMMARY ────────────────────────────────────────
        city_summary = con.execute(f"""
            SELECT
                COUNT(*) as total_listings,
                COUNT(DISTINCT locality) as total_localities,
                MEDIAN(CASE WHEN listing_type='buy' THEN price END) as median_buy_price,
                MEDIAN(CASE WHEN listing_type='rent' THEN price END) as median_rent_price,
                AVG(CASE WHEN listing_type='buy' THEN price END) as avg_buy_price,
                MIN(CASE WHEN listing_type='buy' THEN price END) as min_buy_price,
                MAX(CASE WHEN listing_type='buy' THEN price END) as max_buy_price,
                COUNT(CASE WHEN listing_type='buy' THEN 1 END) as buy_count,
                COUNT(CASE WHEN listing_type='rent' THEN 1 END) as rent_count,
                COUNT(CASE WHEN is_active=1 THEN 1 END) as active_count,
                SUM(CASE WHEN builder_name LIKE 'Owner%' THEN 1 ELSE 0 END) as owner_listings,
                SUM(CASE WHEN builder_name LIKE 'Agent%' THEN 1 ELSE 0 END) as agent_listings,
                AVG(
                    CASE WHEN last_seen IS NOT NULL AND scraped_at IS NOT NULL
                    THEN DATEDIFF('day', CAST(scraped_at AS TIMESTAMP), CAST(last_seen AS TIMESTAMP))
                    END
                ) as avg_days_on_market,
                COUNT(CASE WHEN CAST(scraped_at AS DATE) = CURRENT_DATE THEN 1 END) as new_today,
                COUNT(CASE WHEN CAST(scraped_at AS DATE) >= CURRENT_DATE - INTERVAL 7 DAY THEN 1 END) as new_this_week
            FROM listings
            WHERE city = '{city}'
        """).df().to_dict(orient='records')[0]

        # ── LOCALITY STATS ──────────────────────────────────────
        locality_stats = con.execute(f"""
            SELECT
                COALESCE(locality, 'Unknown') as locality,
                listing_type,
                COUNT(*) as listing_count,
                MEDIAN(price) as median_price,
                PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY price) as p25_price,
                PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY price) as p75_price,
                MIN(price) as min_price,
                MAX(price) as max_price,
                AVG(beds) as avg_beds,
                AVG(area) as avg_area,
                COUNT(CASE WHEN is_active=1 THEN 1 END) as active_count,
                SUM(CASE WHEN builder_name LIKE 'Owner%' THEN 1 ELSE 0 END) as owner_count,
                SUM(CASE WHEN builder_name LIKE 'Agent%' THEN 1 ELSE 0 END) as agent_count,
                AVG(
                    CASE WHEN last_seen IS NOT NULL AND scraped_at IS NOT NULL
                    THEN DATEDIFF('day', CAST(scraped_at AS TIMESTAMP), CAST(last_seen AS TIMESTAMP))
                    END
                ) as avg_days_on_market,
                COUNT(CASE WHEN CAST(scraped_at AS DATE) >= CURRENT_DATE - INTERVAL 7 DAY THEN 1 END) as new_this_week,
                AVG(lat) as lat,
                AVG(lng) as lng
            FROM listings
            WHERE city = '{city}' AND price > 0
            GROUP BY locality, listing_type
            HAVING COUNT(*) >= 3
            ORDER BY listing_count DESC
        """).df().to_dict(orient='records')

        # ── PRICE APPRECIATION: Weekly trend per locality ────────
        # Groups all historical data into weekly buckets per locality
        # Computes week-over-week and month-over-month appreciation
        weekly_prices = con.execute(f"""
            WITH weekly AS (
                SELECT
                    COALESCE(locality, 'Unknown') as locality,
                    listing_type,
                    STRFTIME(CAST(scraped_at AS TIMESTAMP), '%Y-%W') as week_key,
                    DATE_TRUNC('week', CAST(scraped_at AS TIMESTAMP))::DATE as week_start,
                    MEDIAN(price) as median_price,
                    COUNT(*) as listing_count
                FROM listings
                WHERE city = '{city}'
                  AND price > 0
                  AND locality IS NOT NULL
                  AND locality != 'Unknown'
                  AND scraped_at IS NOT NULL
                GROUP BY locality, listing_type, week_key, week_start
                HAVING COUNT(*) >= 2
            )
            SELECT
                locality,
                listing_type,
                week_key,
                week_start,
                median_price,
                listing_count,
                LAG(median_price, 1) OVER (
                    PARTITION BY locality, listing_type
                    ORDER BY week_start
                ) as prev_week_price,
                LAG(median_price, 4) OVER (
                    PARTITION BY locality, listing_type
                    ORDER BY week_start
                ) as prev_month_price,
                FIRST_VALUE(median_price) OVER (
                    PARTITION BY locality, listing_type
                    ORDER BY week_start
                    ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
                ) as first_ever_price,
                LAST_VALUE(median_price) OVER (
                    PARTITION BY locality, listing_type
                    ORDER BY week_start
                    ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
                ) as latest_price
            FROM weekly
            ORDER BY locality, listing_type, week_start
        """).df()

        # Build appreciation summary per locality
        appreciation = {}
        if not weekly_prices.empty:
            # Latest row per locality+type
            latest = weekly_prices.sort_values('week_start').groupby(
                ['locality', 'listing_type']
            ).last().reset_index()

            for _, row in latest.iterrows():
                key = f"{row['locality']}_{row['listing_type']}"
                curr = row['median_price']
                prev_w = row['prev_week_price']
                prev_m = row['prev_month_price']
                first = row['first_ever_price']

                wow = round((curr - prev_w) / prev_w * 100, 2) if prev_w and prev_w > 0 else None
                mom = round((curr - prev_m) / prev_m * 100, 2) if prev_m and prev_m > 0 else None
                total = round((curr - first) / first * 100, 2) if first and first > 0 else None

                appreciation[key] = {
                    'wow_pct': wow,         # week over week
                    'mom_pct': mom,         # month over month (4 weeks)
                    'total_pct': total,     # since first seen
                    'current_median': curr,
                    'first_median': first,
                }

            # Full weekly time series per locality (for sparklines)
            sparklines = {}
            for (loc, lt), grp in weekly_prices.groupby(['locality', 'listing_type']):
                key = f"{loc}_{lt}"
                sparklines[key] = grp.sort_values('week_start')[
                    ['week_key', 'median_price', 'listing_count']
                ].to_dict(orient='records')
        else:
            sparklines = {}

        # Attach appreciation to locality_stats
        for row in locality_stats:
            key = f"{row['locality']}_{row['listing_type']}"
            appr = appreciation.get(key, {})
            row['wow_pct'] = appr.get('wow_pct')
            row['mom_pct'] = appr.get('mom_pct')
            row['total_appreciation_pct'] = appr.get('total_pct')
            row['sparkline'] = sparklines.get(key, [])

        # ── RENTAL YIELD ────────────────────────────────────────
        yield_stats = con.execute(f"""
            WITH buy_data AS (
                SELECT locality, MEDIAN(price) as buy_price, AVG(lat) as lat, AVG(lng) as lng
                FROM listings
                WHERE city = '{city}' AND listing_type = 'buy' AND price > 0
                GROUP BY locality HAVING COUNT(*) >= 2
            ),
            rent_data AS (
                SELECT locality, MEDIAN(price) as rent_price
                FROM listings
                WHERE city = '{city}' AND listing_type = 'rent' AND price > 0
                GROUP BY locality HAVING COUNT(*) >= 2
            )
            SELECT
                b.locality,
                b.buy_price,
                r.rent_price,
                ROUND((r.rent_price * 12 / b.buy_price) * 100, 2) as gross_yield_pct,
                b.lat, b.lng
            FROM buy_data b
            JOIN rent_data r ON b.locality = r.locality
            WHERE b.buy_price > 0
            ORDER BY gross_yield_pct DESC
        """).df().to_dict(orient='records')

        # ── BHK BREAKDOWN ───────────────────────────────────────
        bhk_stats = con.execute(f"""
            SELECT
                beds,
                listing_type,
                COUNT(*) as count,
                MEDIAN(price) as median_price,
                PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY price) as p25,
                PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY price) as p75,
                AVG(
                    CASE WHEN last_seen IS NOT NULL AND scraped_at IS NOT NULL
                    THEN DATEDIFF('day', CAST(scraped_at AS TIMESTAMP), CAST(last_seen AS TIMESTAMP))
                    END
                ) as avg_days_on_market
            FROM listings
            WHERE city = '{city}'
              AND beds IS NOT NULL AND beds > 0 AND beds <= 6
              AND price > 0
            GROUP BY beds, listing_type
            ORDER BY beds, listing_type
        """).df().to_dict(orient='records')

        # ── SUPPLY TREND ─────────────────────────────────────────
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

        # ── PROPERTY TYPE ────────────────────────────────────────
        type_stats = con.execute(f"""
            SELECT
                COALESCE(property_type, 'Unknown') as property_type,
                listing_type,
                COUNT(*) as count,
                MEDIAN(price) as median_price
            FROM listings
            WHERE city = '{city}' AND price > 0
            GROUP BY property_type, listing_type
            ORDER BY count DESC
        """).df().to_dict(orient='records')

        # ── SOURCE STATS ─────────────────────────────────────────
        source_stats = con.execute(f"""
            SELECT source, COUNT(*) as count
            FROM listings WHERE city = '{city}'
            GROUP BY source ORDER BY count DESC
        """).df().to_dict(orient='records')

        # ── TOP APPRECIATING LOCALITIES ──────────────────────────
        # For homepage intelligence: which localities are heating up
        top_appreciating = sorted(
            [
                {
                    'locality': k.rsplit('_', 1)[0],
                    'listing_type': k.rsplit('_', 1)[1] if '_' in k else 'buy',
                    **v
                }
                for k, v in appreciation.items()
                if v.get('mom_pct') is not None and k.endswith('_buy')
            ],
            key=lambda x: x.get('mom_pct', 0) or 0,
            reverse=True
        )[:10]

        # ── WRITE CITY JSON ──────────────────────────────────────
        city_data = {
            'city': city,
            'computed_at': datetime.now().isoformat(),
            'summary': city_summary,
            'locality_stats': locality_stats,
            'bhk_stats': bhk_stats,
            'yield_stats': yield_stats,
            'supply_trend': supply_trend,
            'type_stats': type_stats,
            'source_stats': source_stats,
            'top_appreciating': top_appreciating,
        }

        out_path = os.path.join(INTEL_DIR, f"{city.lower().replace(' ', '_')}.json")
        with open(out_path, 'w') as f:
            json.dump(city_data, f, indent=2, default=str)

        n_appr = len([k for k in appreciation if appreciation[k].get('mom_pct') is not None])
        print(f"  Localities: {len(locality_stats)} | Yield: {len(yield_stats)} | Appreciation: {n_appr} | Done")

        all_summary.append({
            'city': city,
            'total_listings': city_summary['total_listings'],
            'total_localities': city_summary['total_localities'],
            'median_buy_price': city_summary['median_buy_price'],
            'median_rent_price': city_summary['median_rent_price'],
            'buy_count': city_summary['buy_count'],
            'rent_count': city_summary['rent_count'],
            'active_count': city_summary['active_count'],
            'new_today': city_summary['new_today'],
            'new_this_week': city_summary['new_this_week'],
            'avg_days_on_market': city_summary['avg_days_on_market'],
        })

    # ── MASTER SUMMARY ───────────────────────────────────────
    with open(os.path.join(INTEL_DIR, 'all_cities.json'), 'w') as f:
        json.dump({
            'computed_at': datetime.now().isoformat(),
            'total_records': total,
            'cities': all_summary
        }, f, indent=2, default=str)

    con.close()
    print(f"\nDone. {len(cities)} cities. Intelligence at: {INTEL_DIR}")

if __name__ == '__main__':
    compute()
