@echo off
setlocal
REM ============================================================================
REM  SquadHeight - headless maps_config.json generation
REM  Scans the SDK's map assets and writes tools\maps_config.json.
REM  Machine-specific paths live in settings.bat (copy settings.example.bat).
REM
REM  Nothing is loaded (the registry is read-only), so unlike the batch export
REM  this needs no relaunch loop and cannot OOM: one editor run does it.
REM  Review the level picks printed to the log before running the batch export.
REM ============================================================================

if not exist "%~dp0settings.bat" (
    echo [SquadHeight] settings.bat not found.
    echo [SquadHeight] Copy settings.example.bat to settings.bat and edit the paths.
    exit /b 2
)
call "%~dp0settings.bat"

if not exist "%UE_CMD%"   ( echo [SquadHeight] UE_CMD not found: %UE_CMD% & exit /b 2 )
if not exist "%UPROJECT%" ( echo [SquadHeight] UPROJECT not found: %UPROJECT% & exit /b 2 )

echo [SquadHeight] Generating tools\maps_config.json (headless)...
echo [SquadHeight] Log output follows (also written to the project's Saved\Logs).

"%UE_CMD%" "%UPROJECT%" -run=pythonscript -script="%~dp0tools\make_config.py" ^
    -stdout -FullStdOutLogOutput -Unattended -NoSplash -NoSound -NoLiveCoding
set "RC=%ERRORLEVEL%"

if "%RC%"=="0" (
    echo [SquadHeight] Wrote tools\maps_config.json - review the level picks above
    echo [SquadHeight] ^(especially maps that printed alternatives^), then run run_batch_export.bat.
) else (
    echo [SquadHeight] make_config exited with code %RC% - check the output above.
)
exit /b %RC%
