@echo off
chcp 65001 >nul
title RealSlate Pipeline
color 0A

echo.
echo ============================================================
echo   REALSLATE - FULL DAILY PIPELINE
echo   %date% %time%
echo ============================================================
echo.

:: STEP 1: SCRAPER
echo [STEP 1/6] Running scraper...
echo.
cd /d "E:\RealSlate\realslate_core"
py runners/run_all_india.py --mode incremental --max-pages 20
echo.
echo [STEP 1 DONE]
echo.

:: STEP 2: PARSE + EXPORT
echo [STEP 2/6] Parsing and exporting to Parquet...
echo.
py "E:\RealSlateOS\pipeline\parse_and_export.py"
echo.
echo [STEP 2 DONE]
echo.

:: STEP 3: COMPUTE INTELLIGENCE
echo [STEP 3/6] Computing intelligence metrics...
echo.
py "E:\RealSlateOS\pipeline\compute_intelligence.py"
echo.
echo [STEP 3 DONE]
echo.

:: STEP 4: GEOCODE
echo [STEP 4/6] Geocoding localities...
echo.
py "E:\RealSlateOS\pipeline\geocode_localities.py"
echo.
echo [STEP 4 DONE]
echo.

:: STEP 5: GIT COMMIT + PUSH
echo [STEP 5/6] Committing and pushing to GitHub...
echo.
cd /d "E:\RealSlateOS"
git add data\intelligence\*
git add data\geocodes\*
git add data\raw\*
git diff --staged --quiet
if %errorlevel% equ 0 (
    echo No changes to commit. Skipping push.
) else (
    git commit -m "intel: %date% %time% - auto-update"
    git push origin main
)
echo.
echo [STEP 5 DONE]
echo.

:: STEP 6: VERCEL
echo [STEP 6/6] Vercel will auto-deploy from the git push above.
echo            Dashboard live at your Vercel URL in ~60 seconds.
echo.

echo ============================================================
echo   ALL STEPS COMPLETE - %time%
echo ============================================================
echo.
pause
