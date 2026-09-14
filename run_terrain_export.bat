@echo off
setlocal
REM ============================================================================
REM  SquadHeight - ground-only batch export
REM  Same as run_batch_export.bat, but records the Landscape heightfield only:
REM  buildings, bridges and rock meshes are ignored. Output goes
REM  to output\_terrain\<Map>\ (same bounds, grid and files as a normal export).
REM
REM  Water: by default the scan goes through water down to the seabed. Run
REM      run_terrain_export.bat water
REM  to stop every column at the water surface instead (never deeper); that
REM  variant goes to output\_terrain_water\<Map>\.
REM ============================================================================
set "SQUADHEIGHT_TERRAIN_ONLY=1"
if /i "%~1"=="water" set "SQUADHEIGHT_TERRAIN_WATER=surface"
if /i "%~1"=="seabed" set "SQUADHEIGHT_TERRAIN_WATER=seabed"
call "%~dp0run_batch_export.bat"
exit /b %ERRORLEVEL%
