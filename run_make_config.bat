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

REM By default the engine's own log is kept OFF the console (it floods it);
REM make_config.py prints a clean list of the level picks instead, and the full
REM detail (including each map's alternatives) goes to output\logs\ and the
REM project's Saved\Logs. Set SQUADHEIGHT_VERBOSE=1 to also stream the raw
REM engine log to the console.
set "ENGINE_LOG_ARGS="
if defined SQUADHEIGHT_VERBOSE set "ENGINE_LOG_ARGS=-stdout -FullStdOutLogOutput"

echo [SquadHeight] Generating tools\maps_config.json (headless)...
echo [SquadHeight] Level picks show below; full detail -^> output\logs\ and Saved\Logs.

"%UE_CMD%" "%UPROJECT%" -run=pythonscript -script="%~dp0tools\make_config.py" ^
    %ENGINE_LOG_ARGS% -Unattended -NoSplash -NoSound -NoLiveCoding
set "RC=%ERRORLEVEL%"

if "%RC%"=="0" (
    echo [SquadHeight] Wrote tools\maps_config.json - review the level picks above
    echo [SquadHeight] ^(alternatives are listed in output\logs\^), then run run_batch_export.bat.
) else (
    echo [SquadHeight] make_config exited with code %RC% - check output\logs\ and Saved\Logs.
)
exit /b %RC%
