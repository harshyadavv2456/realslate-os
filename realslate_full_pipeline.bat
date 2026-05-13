@echo off
title RealSlate — Full Daily Pipeline
color 0A
setlocal enabledelayedexpansion

echo.
echo ================================================================
echo   REALSLATE — FULL DAILY INTELLIGENCE PIPELINE
echo   Started: %date% %time%
echo ================================================================
echo.

:: ── CONFIGURATION ───────────────────────────────────────────────
set PYTHON=py
set SCRAPER_DIR=D:\RealSlate\realslate_core
set OS_DIR=D:\RealSlateOS
set LOG_DIR=D:\RealSlateOS\logs
set PAGES=20
set GIT_BRANCH=main

:: Log file
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
set LOGFILE=%LOG_DIR%\pipeline_%date:~-4%%date:~3,2%%date:~0,2%_%time:~0,2%%time:~3,2%.log
set LOGFILE=%LOGFILE: =0%

echo Pipeline log: %LOGFILE%
echo.

echo ================================================================ >> "%LOGFILE%"
echo REALSLATE PIPELINE — %date% %time% >> "%LOGFILE%"
echo ================================================================ >> "%LOGFILE%"
echo. >> "%LOGFILE%"

:: ── STEP 1: RUN REALSLATE SCRAPER ───────────────────────────────
echo [STEP 1/6]  Running RealSlate scraper (--max-pages %PAGES%)...
echo             This is the longest step. Please wait.
echo.
echo [%time%] STEP 1: RealSlate Scraper >> "%LOGFILE%"

cd /d "%SCRAPER_DIR%"
%PYTHON% runners/run_all_india.py --mode incremental --max-pages %PAGES% >> "%LOGFILE%" 2>&1

if %errorlevel% neq 0 (
    echo   [WARNING] Scraper exited with errors. Checking if data exists...
    echo [%time%] WARNING: Scraper errors, continuing >> "%LOGFILE%"
) else (
    echo   [OK] Scraper complete.
)
echo.

:: ── STEP 2: PARSE + EXPORT TO PARQUET ───────────────────────────
echo [STEP 2/6]  Parsing addresses and exporting to Parquet...
echo [%time%] STEP 2: parse_and_export >> "%LOGFILE%"

%PYTHON% "%OS_DIR%\pipeline\parse_and_export.py" >> "%LOGFILE%" 2>&1

if %errorlevel% neq 0 (
    echo   [ERROR] Export failed. Check %LOGFILE%
    echo [%time%] ERROR: parse_and_export failed >> "%LOGFILE%"
    goto :fail
)
echo   [OK] Export complete.
echo.

:: ── STEP 3: COMPUTE INTELLIGENCE ────────────────────────────────
echo [STEP 3/6]  Computing intelligence metrics + appreciation...
echo [%time%] STEP 3: compute_intelligence >> "%LOGFILE%"

%PYTHON% "%OS_DIR%\pipeline\compute_intelligence.py" >> "%LOGFILE%" 2>&1

if %errorlevel% neq 0 (
    echo   [ERROR] Intelligence computation failed. Check %LOGFILE%
    echo [%time%] ERROR: compute_intelligence failed >> "%LOGFILE%"
    goto :fail
)
echo   [OK] Intelligence computed.
echo.

:: ── STEP 4: GEOCODE NEW LOCALITIES ──────────────────────────────
echo [STEP 4/6]  Geocoding new localities (incremental)...
echo [%time%] STEP 4: geocode_localities >> "%LOGFILE%"

%PYTHON% "%OS_DIR%\pipeline\geocode_localities.py" >> "%LOGFILE%" 2>&1

if %errorlevel% neq 0 (
    echo   [WARNING] Geocoding had errors. Continuing with existing data.
    echo [%time%] WARNING: geocode errors, continuing >> "%LOGFILE%"
) else (
    echo   [OK] Geocoding complete.
)
echo.

:: ── STEP 5: GIT COMMIT + PUSH ────────────────────────────────────
echo [STEP 5/6]  Committing to GitHub...
echo [%time%] STEP 5: git push >> "%LOGFILE%"

cd /d "%OS_DIR%"

git add data\intelligence\* 2>> "%LOGFILE%"
git add data\geocodes\* 2>> "%LOGFILE%"
git add data\raw\* 2>> "%LOGFILE%"

:: Check if there is anything to commit
git diff --staged --quiet
if %errorlevel% equ 0 (
    echo   [INFO] No changes detected. Nothing to push.
    echo [%time%] No changes to commit >> "%LOGFILE%"
    goto :done
)

:: Build commit message with stats
for /f "tokens=*" %%i in ('%PYTHON% -c "import json,os; d=json.load(open(r'%OS_DIR%\data\intelligence\all_cities.json')); print(str(d['total_records'])+' records, '+str(len(d['cities']))+' cities')" 2^>nul') do set STATS=%%i

git commit -m "intel: %date% %time:~0,5% | %STATS% | auto-update" >> "%LOGFILE%" 2>&1
git push origin %GIT_BRANCH% >> "%LOGFILE%" 2>&1

if %errorlevel% neq 0 (
    echo   [ERROR] Git push failed. Check SSH key and repo config.
    echo [%time%] ERROR: git push failed >> "%LOGFILE%"
    echo.
    echo   Tip: Run 'git remote -v' to verify remote is set.
    echo   Tip: Run 'ssh -T git@github.com' to test SSH auth.
    goto :fail
)
echo   [OK] Pushed to GitHub.
echo.

:: ── STEP 6: VERCEL DEPLOY TRIGGER ────────────────────────────────
echo [STEP 6/6]  Vercel deployment triggered by git push.
echo             Dashboard will be live at your Vercel URL in ~60 seconds.
echo [%time%] STEP 6: Vercel auto-deploy triggered by push >> "%LOGFILE%"
echo.

:done
echo.
echo ================================================================
echo   PIPELINE COMPLETE
echo   Finished: %date% %time%
echo ================================================================
echo.
echo   Dashboard: http://localhost:8000/dashboard/index.html (local)
echo   Production: https://realslate-os.vercel.app (after Vercel setup)
echo   Log: %LOGFILE%
echo.
echo [%time%] Pipeline COMPLETE >> "%LOGFILE%"

timeout /t 15 /nobreak > nul
exit /b 0

:fail
echo.
echo ================================================================
echo   PIPELINE FAILED — %time%
echo   See log: %LOGFILE%
echo ================================================================
echo.
echo [%time%] Pipeline FAILED >> "%LOGFILE%"
pause
exit /b 1
