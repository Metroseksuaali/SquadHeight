@echo off
setlocal
REM ============================================================================
REM  SquadHeight - headless maps_config.json generation
REM  Scans the SDK's map assets and writes tools\maps_config.json.
REM  Machine-specific paths live in settings.bat (copy settings.example.bat).
REM
REM  Nothing is loaded (the registry is read-only), so unlike the batch export
REM  this needs no relaunch loop and cannot OOM: one editor run does it.
REM
REM  The console shows only SquadHeight's clean level picks; the engine's own
REM  (very noisy) log is redirected to a file under output\logs. Set
REM  SQUADHEIGHT_VERBOSE=1 to stream the raw engine log to the console instead.
REM ============================================================================

set "RC=0"

if not exist "%~dp0settings.bat" (
    echo [SquadHeight] settings.bat not found.
    echo [SquadHeight] Copy settings.example.bat to settings.bat and edit the paths.
    set "RC=2" & goto :end
)
call "%~dp0settings.bat"

if not exist "%UE_CMD%"   ( echo [SquadHeight] UE_CMD not found: %UE_CMD%   & set "RC=2" & goto :end )
if not exist "%UPROJECT%" ( echo [SquadHeight] UPROJECT not found: %UPROJECT% & set "RC=2" & goto :end )

REM Make sure the log folder exists and name a timestamped engine-log file.
set "LOGDIR=%~dp0output\logs"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "TS=%%i"
set "ENGINE_LOG=%LOGDIR%\engine_makeconfig_%TS%.log"
set "SH_LOG=%LOGDIR%\squadheight_%TS:~0,8%.log"

echo [SquadHeight] Generating tools\maps_config.json (headless)...
echo [SquadHeight] Level picks show below. Detailed logs are written to:
echo [SquadHeight]     %SH_LOG%   (SquadHeight: picks + alternatives + detail)
echo [SquadHeight]     %ENGINE_LOG%   (raw engine log)
echo.

if defined SQUADHEIGHT_VERBOSE (
    "%UE_CMD%" "%UPROJECT%" -run=pythonscript -script="%~dp0tools\make_config.py" ^
        -stdout -FullStdOutLogOutput -Unattended -NoSplash -NoSound -NoLiveCoding
) else (
    "%UE_CMD%" "%UPROJECT%" -run=pythonscript -script="%~dp0tools\make_config.py" ^
        -Unattended -NoSplash -NoSound -NoLiveCoding > "%ENGINE_LOG%" 2>&1
)
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (
    echo [SquadHeight] Wrote tools\maps_config.json - review the level picks above.
    echo [SquadHeight] Alternatives + full detail: %SH_LOG%
    echo [SquadHeight] Then run run_batch_export.bat.
) else (
    echo [SquadHeight] make_config exited with code %RC%.
    echo [SquadHeight] Check %SH_LOG% and %ENGINE_LOG%.
)

:end
echo.
pause
exit /b %RC%
