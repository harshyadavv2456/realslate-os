"""
One-time repair for a corrupted E:\\RealSlateOS\\data\\geocodes\\cache.json.

The file has valid JSON followed by trailing garbage (likely from an
interrupted/overlapping write at some point). This recovers the FIRST
complete, valid JSON object in the file and discards whatever comes
after it - preserving your existing geocoded localities instead of
wiping the cache and re-hitting Nominatim's 1-req/sec limit for
everything again.

Run once:  py repair_geocode_cache.py
"""
import json
import shutil
from pathlib import Path

CACHE_PATH = Path(r"E:\RealSlateOS\data\geocodes\cache.json")

def main():
    if not CACHE_PATH.exists():
        print(f"No cache file found at {CACHE_PATH} - nothing to repair.")
        return

    raw = CACHE_PATH.read_text(encoding="utf-8")

    decoder = json.JSONDecoder()
    try:
        obj, end_idx = decoder.raw_decode(raw)
    except json.JSONDecodeError as e:
        print(f"FATAL: could not recover even the first JSON object: {e}")
        print("The file may be corrupted from the very start. Check a backup,")
        print("or delete cache.json to start fresh (slower - will re-geocode everything).")
        return

    trailing = len(raw) - end_idx
    print(f"Recovered {len(obj)} cache entries.")
    print(f"Discarded {trailing} trailing bytes of garbage data after the valid JSON.")

    if trailing == 0:
        print("File was actually already valid - nothing to fix. No changes made.")
        return

    backup_path = CACHE_PATH.with_suffix(".json.bak")
    shutil.copy2(CACHE_PATH, backup_path)
    print(f"Backed up original (corrupted) file to {backup_path}")

    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)

    print(f"Repaired cache.json saved with {len(obj)} entries.")

if __name__ == "__main__":
    main()
