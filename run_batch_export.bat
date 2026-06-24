@echo off
setlocal
REM ============================================================================
REM  SquadHeight - headless batch heightmap export
REM  Machine-specific paths live in settings.bat (copy settings.example.bat).
REM ============================================================================

if not exist "%~dp0settings.bat" (
    echo [SquadHeight] settings.bat not found.
    echo [SquadHeight] Copy settings.example.bat to settings.bat and edit the paths.
    exit /b 2
)
call "%~dp0settings.bat"

if not exist "%UE_CMD%"   ( echo [SquadHeight] UE_CMD not found: %UE_CMD% & exit /b 2 )
if not exist "%UPROJECT%" ( echo [SquadHeight] UPROJECT not found: %UPROJECT% & exit /b 2 )
if not exist "%SQUADHEIGHT_CONFIG%" (
    echo [SquadHeight] Config not found: %SQUADHEIGHT_CONFIG%
    echo [SquadHeight] Copy tools\maps_config.example.json to tools\maps_config.json first.
    exit /b 2
)

REM Console verbosity: by default the engine's own log is NOT streamed to the
REM console - it floods it with thousands of asset/shader/streaming lines.
REM batch_export.py prints a clean plain-English phase + progress view instead,
REM and the full detail is written to output\logs\squadheight_<date>.log (plus
REM the engine's own Saved\Logs). Set SQUADHEIGHT_VERBOSE=1 (here or in
REM settings.bat) to ALSO stream the raw engine log to the console for deep
REM debugging.
set "ENGINE_LOG_ARGS="
if defined SQUADHEIGHT_VERBOSE set "ENGINE_LOG_ARGS=-stdout -FullStdOutLogOutput"

echo [SquadHeight] Starting headless batch export...
echo [SquadHeight] Clean progress shows below; full detail -^> output\logs\ and Saved\Logs.

REM Notes:
REM  * -run=pythonscript executes the script via the Python commandlet.
REM    If the editor exits immediately complaining about an unknown
REM    commandlet, the Python Editor Script Plugin is NOT enabled in this
REM    SDK build - see README.md "Setup".
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
    exit /b 1
)
echo [SquadHeight] === editor run %ATTEMPT% ===
"%UE_CMD%" "%UPROJECT%" -run=pythonscript -script="%~dp0tools\batch_export.py" ^
    %ENGINE_LOG_ARGS% -Unattended -NoSplash -NoSound -NoLiveCoding
set "RC=%ERRORLEVEL%"
if exist "%REPORT%" goto done
echo [SquadHeight] Editor exited without a final report (exit code %RC%) - resuming...
goto loop

:done
if "%RC%"=="0" (
    echo [SquadHeight] Batch export finished OK.
) else (
    echo [SquadHeight] Batch export finished with failures (exit code %RC%^).
    echo [SquadHeight] See output\batch_report.json and output\logs\ for detail.
)
exit /b %RC%
