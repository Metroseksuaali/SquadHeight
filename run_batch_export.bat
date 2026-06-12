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

echo [SquadHeight] Starting headless batch export...
echo [SquadHeight] Log output follows (also written to the project's Saved\Logs).

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
    -stdout -FullStdOutLogOutput -Unattended -NoSplash -NoSound -NoLiveCoding
set "RC=%ERRORLEVEL%"
if exist "%REPORT%" goto done
echo [SquadHeight] Editor exited without a final report (exit code %RC%) - resuming...
goto loop

:done
if "%RC%"=="0" (
    echo [SquadHeight] Batch export finished OK.
) else (
    echo [SquadHeight] Batch export finished with failures (exit code %RC%^).
    echo [SquadHeight] Check output above and output\batch_report.json.
)
exit /b %RC%
