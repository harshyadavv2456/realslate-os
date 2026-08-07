@echo off
chcp 65001 >nul
title RealSlate Ingest Only
color 0B

set "DATA_DRIVE=E:"
set "PLAYWRIGHT_BROWSERS_PATH=%DATA_DRIVE%\PlaywrightCache"
set "PIP_CACHE_DIR=%DATA_DRIVE%\PipCache"
set "TEMP=%DATA_DRIVE%\Temp"
set "TMP=%DATA_DRIVE%\Temp"

echo.
echo ============================================================
echo   REALSLATE - INGEST ONLY (no scrape, no dep install)
echo   Use this after running the scraper manually yourself.
echo   %date% %time%
echo ============================================================
echo.

:: STEP A: PARSE + EXPORT
echo [1/4] Parsing and exporting to Parquet...
echo.
py "E:\RealSlateOS\pipeline\parse_and_export.py"
if errorlevel 1 goto :error_parse
echo.
echo [1/4 DONE]
echo.

:: STEP B: COMPUTE INTELLIGENCE
echo [2/4] Computing intelligence metrics...
echo.
py "E:\RealSlateOS\pipeline\compute_intelligence.py"
if errorlevel 1 goto :error_intel
echo.
echo [2/4 DONE]
echo.

:: STEP C: GEOCODE
echo [3/4] Geocoding localities...
echo.
py "E:\RealSlateOS\pipeline\geocode_localities.py"
echo.
echo [3/4 DONE]
echo.

:: STEP D: GIT COMMIT + PUSH
echo [4/4] Committing and pushing to GitHub...
echo.
cd /d "E:\RealSlateOS"
git add data\intelligence\*
git add data\geocodes\*
git add data\raw\*
git diff --staged --quiet
if %errorlevel% equ 0 (
    echo No changes to commit. Skipping push.
) else (
    git commit -m "intel: %date% %time% - manual ingest"
    git push origin main
    if errorlevel 1 goto :error_push
)
echo.
echo [4/4 DONE]
echo.

echo ============================================================
echo   INGEST COMPLETE - %time%
echo   Vercel will auto-deploy from the push above (~60s).
echo ============================================================
echo.
echo Press any key to close this window...
pause >nul
exit /b 0

:error_parse
echo.
echo ============================================================
echo   FAILED at parse_and_export.py - see errors above
echo ============================================================
pause
exit /b 1

:error_intel
echo.
echo ============================================================
echo   FAILED at compute_intelligence.py - see errors above
echo ============================================================
pause
exit /b 1

:error_push
echo.
echo ============================================================
echo   Data was computed but git push FAILED - see errors above
echo   Data is NOT live yet. Check your git credentials/connection
echo   and try: cd /d E:\RealSlateOS  then  git push origin main
echo ============================================================
pause
exit /b 1
