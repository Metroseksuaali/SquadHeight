@echo off
setlocal
REM ============================================================================
REM  SquadHeight - headless batch heightmap export
REM  Machine-specific paths live in settings.bat (copy settings.example.bat).
REM
REM  The console shows only SquadHeight's clean phase/progress view; the
REM  engine's own (very noisy) log is redirected to a file under output\logs.
REM  Set SQUADHEIGHT_VERBOSE=1 to stream the raw engine log to the console.
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
if not exist "%SQUADHEIGHT_CONFIG%" (
    echo [SquadHeight] Config not found: %SQUADHEIGHT_CONFIG%
    echo [SquadHeight] Copy tools\maps_config.example.json to tools\maps_config.json first.
    set "RC=2" & goto :end
)

REM Make sure the log folder exists and name a timestamped engine-log file.
set "LOGDIR=%~dp0output\logs"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "TS=%%i"
set "ENGINE_LOG=%LOGDIR%\engine_batch_%TS%.log"
set "SH_LOG=%LOGDIR%\squadheight_%TS:~0,8%.log"

echo [SquadHeight] Starting headless batch export...
echo [SquadHeight] Clean progress shows below. Detailed logs are written to:
echo [SquadHeight]     %SH_LOG%   (SquadHeight: phases + per-map detail)
echo [SquadHeight]     %ENGINE_LOG%   (raw engine log, all editor runs appended)
echo.

REM Notes:
REM  * -run=pythonscript executes the script via the Python commandlet. If the
REM    editor exits immediately complaining about an unknown commandlet, the
REM    Python Editor Script Plugin is NOT enabled - see README.md "Setup".
REM    On UE5 you can often force-enable it per-run by appending:
REM        -EnablePlugins=PythonScriptPlugin
REM  * Optionally append -NullRHI to run without a GPU/rendering device
REM    (faster startup on build agents). If traces then come back empty on
REM    your build, drop it again.
REM  * The editor can run out of memory after many large maps; finished maps
REM    are skipped on re-run, so the loop below simply relaunches the editor
REM    until batch_export.py writes its final report.
REM  * Set SQUADHEIGHT_ONE_MAP=1 to export ONE map per editor run (fresh
REM    process every map). Slower, but immune to the silent collision loss
REM    seen in long multi-map sessions (Tallil exported with ~2% of its
REM    structures as map ~22 of one session). Recommended for final exports.
set "REPORT=%~dp0output\batch_report.json"
if exist "%REPORT%" del "%REPORT%"
set ATTEMPT=0

:loop
set /a ATTEMPT+=1
if %ATTEMPT% GTR 40 (
    echo [SquadHeight] Giving up after 40 editor runs without a finished report.
    set "RC=1" & goto :end
)
echo [SquadHeight] === editor run %ATTEMPT% ===
if defined SQUADHEIGHT_VERBOSE (
    "%UE_CMD%" "%UPROJECT%" -run=pythonscript -script="%~dp0tools\batch_export.py" ^
        -stdout -FullStdOutLogOutput -Unattended -NoSplash -NoSound -NoLiveCoding
) else (
    "%UE_CMD%" "%UPROJECT%" -run=pythonscript -script="%~dp0tools\batch_export.py" ^
        -Unattended -NoSplash -NoSound -NoLiveCoding >> "%ENGINE_LOG%" 2>&1
)
set "RC=%ERRORLEVEL%"
if exist "%REPORT%" goto done
echo [SquadHeight] Editor exited without a final report (exit code %RC%) - resuming...
goto loop

:done
echo.
if "%RC%"=="0" (
    echo [SquadHeight] Batch export finished OK.
) else (
    echo [SquadHeight] Batch export finished with failures (exit code %RC%^).
    echo [SquadHeight] See output\batch_report.json, %SH_LOG% and %ENGINE_LOG%.
)

:end
echo.
pause
exit /b %RC%
