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

REM Sortable timestamp for the run's log file (locale-independent, unlike
REM %DATE%/%TIME% which vary by Windows regional settings).
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HH-mm-ss"') do set "TS=%%i"
set "LOGDIR=%~dp0logs"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set "RUNLOG=%LOGDIR%\export_%TS%.log"

echo [SquadHeight] Starting headless batch export...
echo [SquadHeight] Full engine/script log: %RUNLOG%

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
REM  * UnrealEditor-Cmd.exe mirrors its entire log (thousands of asset-load
REM    lines) to the console by default, with no flag to filter that down to
REM    just errors without also filtering its log output entirely (a
REM    verbosity threshold applies before a line is written ANYWHERE, not
REM    just to the console). So instead the editor's own stdout/stderr are
REM    redirected to %RUNLOG% below (appended, so every relaunch attempt in
REM    one batch run lands in the same dated file - no digging through the
REM    UE project's own Saved\Logs). batch_export.py and export_heightmap.py
REM    write their own status (which map, a live progress bar, the final
REM    summary, the per-checkpoint scan progress) BOTH to the console device
REM    directly (bypassing this redirect - see export_heightmap._console_write)
REM    AND to fd 1 (os.write(1, ...) - see export_heightmap._log_write), which
REM    IS this same redirected file: sys.stdout inside the editor's embedded
REM    Python is not reliably wired to it, and a second open() of %RUNLOG%
REM    collides with the handle this redirect already holds, so fd 1 is the
REM    one mechanism that actually reaches it. %RUNLOG% therefore gets
REM    everything, including a crash - the loop below already detects one
REM    without needing the editor's own console output: it relaunches
REM    whenever the editor exits without a finished report.
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
    -Unattended -NoSplash -NoSound -NoLiveCoding >>"%RUNLOG%" 2>&1
set "RC=%ERRORLEVEL%"
if exist "%REPORT%" goto done
echo [SquadHeight] Editor exited without a final report (exit code %RC%) - resuming...
goto loop

:done
if "%RC%"=="0" (
    echo [SquadHeight] Batch export finished OK.
) else (
    echo [SquadHeight] Batch export finished with failures (exit code %RC%^).
    echo [SquadHeight] Check output above, output\batch_report.json and %RUNLOG%.
)
exit /b %RC%
