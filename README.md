# RealSlate — Pan-India Real Estate Data Intelligence Platform

**Status:** Live, automated, in maintenance/hold mode until November 2026
**Owner:** Harsh Yadav
**Repos:** `harshyadavv2456/RealSlate` (core scraper) · `harshyadavv2456/realslate-os` (dashboard/pipeline)

---

## 1. What This Project Is

RealSlate is an autonomous, self-updating real estate data engine for the Indian property market. It has two parts:

- **`realslate_core`** — the scraper. Crawls listings from major Indian property portals (99acres, MagicBricks, NoBroker, Housing.com) across 24+ cities and writes them into a local SQLite database.
- **`RealSlateOS`** — the dashboard and pipeline. Reads the raw scraped data, cleans/parses it, computes market intelligence (like dominant BHK type per locality), geocodes localities, and publishes an interactive dashboard live on Vercel.

The two are connected by a daily automation pipeline that runs without Harsh touching it.

## 2. Scale

- **~354,000 total listings** in the core SQLite database (`realslate.db`, ~886MB)
- **275,719 records** parsed and exported to Parquet in the most recent full run
- **25 cities** with computed market intelligence
- **24+ source cities** actively crawled across 4 major listing portals
- **8,000+ geocoded localities** cached for map/location features

## 3. Architecture

```
realslate_core (scraper)
   ↓ writes to
realslate.db (SQLite, WAL mode)
   ↓ export_data.py
Raw exports (CSV/JSON per city)
   ↓ parse_and_export.py
Parquet files (data/raw/*.parquet, per city per month)
   ↓ compute_intelligence.py
Market intelligence (dominant BHK, pricing signals, etc. — 25 cities)
   ↓ geocode_localities.py
Geocoded locality cache (cache.json, self-healing)
   ↓ git commit + push
GitHub (harshyadavv2456/realslate-os)
   ↓ auto-deploy
Vercel — live dashboard
```

Two automation modes exist:
- **Full pipeline** (`realslate_daily_pipeline.bat`) — dependency check, Playwright browser install, scraper run, parse, intelligence, geocode, commit, push. Run manually when Harsh wants a fresh full scrape.
- **Ingest-only** (`realslate_ingest_only.bat`) — skips the scraper, runs parse → intelligence → geocode → commit → push on whatever raw data already exists. **This is the one on the daily schedule.**

## 4. Automation

- Windows Task Scheduler job **"RealSlate Ingest Only"** runs daily at **08:00**, executing `realslate_ingest_only.bat`.
- Confirmed working: test run succeeded (`Last Result: 0`), task is queued for the next scheduled run.
- On success, changes are committed and pushed to GitHub automatically, and Vercel auto-deploys the updated dashboard — so the live site refreshes daily without manual intervention.
- Scraper runs are **not** on the automated schedule — Harsh triggers those manually via PowerShell when he wants new raw data.
- **Caveat:** Task Scheduler is currently set to "Stop on battery, no start on battery." If the machine isn't plugged in at 8 AM, the run won't fire. Not fixed — low priority, assumed desktop/plugged-in use.

## 5. Reliability Fixes Shipped

These were real bugs hit and fixed during setup, not hypothetical hardening:

| Issue | Fix |
|---|---|
| Pipeline scripts hardcoded to `D:\`, data actually on `E:\` | Scripts now read a `DATA_DRIVE` env var (defaults to `E:`) |
| `compute_intelligence.py` crashed — SQL referenced non-existent `bhk` column | Corrected to `CAST(beds AS INTEGER)` |
| One corrupted Parquet file (recurring, likely AV/backup tool locking freshly-written files) crashed the entire 25-city intelligence run | Per-file validation added; bad files are skipped with a warning instead of halting everything |
| `geocode_localities.py` cache.json corruption crashed geocoding | Self-healing loader: recovers valid JSON via raw decode, discards trailing garbage, backs up the original |
| Dependency versions incompatible with Python 3.13 (no prebuilt Windows wheels — playwright, psycopg2-binary, lxml, selectolax, pandas, numpy, sqlalchemy) | Version pins bumped across both `requirements.txt` files |
| SQLAlchemy AssertionError on Python 3.13 (known upstream bug, GitHub #11334) | Bumped to `>=2.0.36`, which contains the fix |
| Playwright browser binaries never downloaded | `playwright install chromium` added as Step 0 of the full pipeline (~290MB one-time download) |
| `"database is locked"` errors during concurrent scraper writes | Enabled SQLite WAL mode on `realslate.db` |
| Batch file (`.bat`) text corruption (twice — once from Unicode em-dashes, once from a stray raw CR byte) | Rewritten clean ASCII/CRLF; verified byte-level |

End-to-end run confirmed working: fresh scrape → 275,719 records parsed → intelligence computed for all 25 cities → 8,011 localities geocoded → committed (`61dd152`) → pushed → Vercel deployed.

## 6. Known Issues — Deliberately Deferred to Post-November 2026

These are real, identified data-quality problems in the scraper layer. They were **explicitly not touched** in this session, per Harsh's own call to protect exam focus and not risk a working scraper mid-prep:

- **`area` field corrupted for ~85% of rows** — almost certainly a comma-parsing bug in the scraper's numeric extraction. Needs a scraper-level fix, not a pipeline patch.
- **`url` field is 100% null** across all listings — no source links captured at all. Scraper never wrote them.
- **`bathrooms` 100% null; `property_type` and `beds` mostly null** at the raw DB level.
- **`listing_type` missing entirely** from `export_data.py`'s query — not being pulled even though it may exist upstream.
- **`is_active` / `version` fields non-functional** — likely meant for tracking listing freshness/history, currently dead weight.
- **~14,500 exact duplicate rows** — no dedup logic at ingest.
- **Dashboard `/api/chat` references a Vercel serverless function that doesn't exist** — dead code, will 404 if triggered.
- No frontend/dashboard redesign has been done — it's functional, not polished.

None of `realslate_core`'s actual scraper source (`core/db.py`, `core/browser.py`, `runners/run_all_india.py`, etc.) was shared or modified this session — everything above was fixed at the `RealSlateOS` pipeline/config layer only.

## 7. Roadmap (Post-CA Final, Nov 2026+)

1. Fix the `area` parsing bug at the scraper source — this is the highest-leverage fix, since it corrupts 85% of a core field.
2. Capture `url` on every listing — turns RealSlate from a data snapshot into something users can actually click through to source.
3. Dedup at ingest.
4. Backfill/repair `bathrooms`, `property_type`, `beds`, `listing_type`.
5. Remove or properly implement `/api/chat`.
6. Dashboard/frontend pass — this is the layer users actually see, and it's currently the least invested-in part of the stack relative to the data engine underneath it.
7. Decide on activation strategy — is this a public product, an internal research tool, or infrastructure feeding something else (e.g., FinVest-style intelligence layer)?

## 8. Tech Stack

- **Scraping:** Python, Playwright (Chromium), SQLite (WAL mode)
- **Pipeline:** Python, Pandas, Parquet
- **Storage:** SQLite (raw) → Parquet (processed)
- **Dashboard/Hosting:** Vercel (auto-deploy from GitHub)
- **Automation:** Windows Task Scheduler, `.bat` orchestration scripts
- **Version control:** GitHub, two repos (core scraper / OS-dashboard)

---

## My Honest Take

As an engineering artifact, this is genuinely impressive for something built solo and running passively: 354K listings, 25 cities, a working end-to-end scrape → parse → intelligence → geocode → deploy pipeline, on a full daily automation loop, with real self-healing built in (corrupted cache recovery, per-file Parquet validation) rather than just happy-path code. That's not a toy — that's infrastructure that survives being unattended, which is the actual hard part of "passive" projects.

But be honest with yourself about what it currently *is* versus what it looks like from the outside: it's a large, reliably-running pile of **partially broken data**. An `area` field that's wrong 85% of the time and a `url` field that's null 100% of the time aren't cosmetic — they're the two fields a real estate dataset is least useful without. Size (354K rows) is not the same as value. Right now this is a well-engineered pipeline shipping mediocre data on a very reliable schedule. That's actually the right failure mode to have going into a break — the *plumbing* is solid, so the *content* fix later is a scoped, well-understood problem (you already know exactly what's broken and roughly why), not an open-ended one.

Is it "a good project"? As a systems-building exercise and as a demonstration of your ability to design and debug non-trivial infrastructure — yes, clearly. As a monetizable or decision-making-grade dataset today — not yet, and you already know that, which is why you correctly deferred the real fix instead of half-patching it under exam pressure.

One thing worth sitting with before November: RealSlate's ultimate value proposition is undefined. You've got Intima, Phantom, FinVest, and RealSlate all "parked" — but RealSlate's role in your ecosystem is the least clear of the four. FinVest has an explicit path (SEBI RA research infra). Intima has a market (B2B2C health). RealSlate right now is "a very large pile of India real estate data" without a stated destination — product, internal tool, or acquisition-bait dataset. That's a decision, not a coding task, and it's worth making before you sink the post-November scraper-fix hours in, so those hours go toward the right shape of "fixed."

For now: it's done, it's automated, it's off your plate. Good work — go pass CA Final.
