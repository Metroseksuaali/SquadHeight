@echo off
REM ============================================================================
REM  SquadHeight - machine-specific paths.
REM  Copy this file to settings.bat (same folder) and edit the three lines.
REM  settings.bat is gitignored, so your local paths never enter the repo.
REM ============================================================================

REM Editor binary of the Squad SDK build:
REM   UE5:    ...\Engine\Binaries\Win64\UnrealEditor-Cmd.exe
REM   UE4.27: ...\Engine\Binaries\Win64\UE4Editor-Cmd.exe
set "UE_CMD=C:\SquadSDK\UnrealEngine\Engine\Binaries\Win64\UnrealEditor-Cmd.exe"

REM The SDK project file:
set "UPROJECT=C:\SquadSDK\Squad\SquadGame.uproject"

REM Batch config (which maps to export); the default usually needs no change:
set "SQUADHEIGHT_CONFIG=%~dp0tools\maps_config.json"
