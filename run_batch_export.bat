@echo off
setlocal
REM ============================================================================
REM  SquadHeight - headless batch heightmap export
REM  Edit the three paths below, then double-click (or run from a shell).
REM ============================================================================

REM --- 1. Editor binary of the Squad SDK build ---
REM     UE5:    ...\Engine\Binaries\Win64\UnrealEditor-Cmd.exe
REM     UE4.27: ...\Engine\Binaries\Win64\UE4Editor-Cmd.exe
set "UE_CMD=C:\SquadSDK\Engine\Binaries\Win64\UnrealEditor-Cmd.exe"

REM --- 2. The SDK project file ---
set "UPROJECT=C:\SquadSDK\Squad.uproject"

REM --- 3. Batch config (which maps to export) ---
set "SQUADHEIGHT_CONFIG=%~dp0tools\maps_config.json"

REM ----------------------------------------------------------------------------
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
REM    SDK build - see README.md "Python plugin missing".
REM    On UE5 you can often force-enable it per-run by appending:
REM        -EnablePlugins=PythonScriptPlugin
REM  * Optionally append -NullRHI to run without a GPU/rendering device
REM    (faster startup on build agents). If traces then come back empty on
REM    your build, drop it again - some setups skip registering certain
REM    components without a rendering device.
"%UE_CMD%" "%UPROJECT%" -run=pythonscript -script="%~dp0tools\batch_export.py" ^
    -stdout -FullStdOutLogOutput -Unattended -NoSplash -NoSound -NoLiveCoding

set "RC=%ERRORLEVEL%"
if "%RC%"=="0" (
    echo [SquadHeight] Batch export finished OK.
) else (
    echo [SquadHeight] Batch export FAILED or partially failed (exit code %RC%^).
    echo [SquadHeight] Check output above and output\batch_report.json.
)
exit /b %RC%
