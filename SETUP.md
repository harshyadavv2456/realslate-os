# RealSlate — Automation Fix Setup

## 1. Drop in the fixed files
Replace these in your `E:\RealSlateOS\` repo:
- `pipeline/parse_and_export.py`
- `pipeline/compute_intelligence.py`
- `pipeline/geocode_localities.py`
- `serve.py`
- Replace your `.bat` with `realslate_daily_pipeline.bat` (put it in `E:\RealSlateOS\`)

What changed: all four Python scripts now read `DATA_DRIVE` from the environment
(set once at the top of the `.bat`) instead of hardcoding `D:\`. If you ever move
drives again, change ONE line in the `.bat` and everything follows. Each script
also now fails loudly (non-zero exit + clear message) instead of silently
producing nothing, and the `.bat` checks `errorlevel` after every step instead
of barreling through failures.

## 2. (Optional but recommended) Wire up ntfy.sh alerts
You already use ntfy.sh for Pixelsprout. Open `realslate_daily_pipeline.bat`,
find this line near the top:
```
set "NTFY_URL="
```
Set it to your topic URL, e.g. `set "NTFY_URL=https://ntfy.sh/your-topic-here"`.
You'll now get a push notification whether the daily run succeeded or failed —
so a silent 5-day-stale site during exam crunch becomes impossible.

## 3. Schedule it — this is the actual missing piece
Right now nothing runs this automatically. Open **Task Scheduler** (Win+R →
`taskschd.msc`) and either import via GUI or run this once in an **elevated**
PowerShell/cmd to create the task:

```cmd
schtasks /create /tn "RealSlate Daily Pipeline" /tr "E:\RealSlateOS\realslate_daily_pipeline.bat" /sc daily /st 05:30 /rl highest /f
```

- `/st 05:30` — pick a time your PC is reliably on and not mid-gym/study-block.
- `/rl highest` — runs with elevated rights (needed if Playwright/pip write
  outside your user folder).
- Your PC needs to be **on and awake** at that time — check Power Options →
  "Sleep" isn't kicking in, or set the task's Conditions tab to "Wake the
  computer to run this task."

Verify it's registered:
```cmd
schtasks /query /tn "RealSlate Daily Pipeline"
```

Test it manually once before trusting the schedule:
```cmd
schtasks /run /tn "RealSlate Daily Pipeline"
```

## 4. What this does NOT fix (by design — deferred to post-Nov)
- The `area` field parsing bug in `realslate_core` (scraper-level, not in this repo)
- Missing `url` field on listings
- Dedup at ingest
- Any dashboard/UI changes

Those are data-quality and product work, not "keep it alive" work — parking
them as planned.
