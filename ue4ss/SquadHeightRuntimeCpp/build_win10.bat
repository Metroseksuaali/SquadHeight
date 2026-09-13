@echo off
setlocal EnableExtensions

rem This file lives in <workspace>\SquadHeightRuntimeCpp\.
rem It can be launched from any current directory.
for %%I in ("%~dp0..") do set "WORKSPACE=%%~fI"
pushd "%WORKSPACE%" || exit /b 1

if not exist "RE-UE4SS\CMakeLists.txt" (
  echo ERROR: RE-UE4SS not found at:
  echo   %WORKSPACE%\RE-UE4SS
  echo.
  echo Clone/check out UE4SS commit 1c1a1497f942c707f47ba668db75b25e86f6c08a
  echo and initialize its submodules first.
  popd
  exit /b 1
)

if not exist "SquadHeightRuntimeCpp\CMakeLists.txt" (
  echo ERROR: SquadHeightRuntimeCpp source folder is missing next to RE-UE4SS.
  popd
  exit /b 1
)

if not exist "CMakeLists.txt" (
  echo ERROR: workspace CMakeLists.txt is missing:
  echo   %WORKSPACE%\CMakeLists.txt
  echo.
  echo Copy SquadHeightRuntimeCpp\CMakeLists.workspace.txt to the workspace root
  echo and rename the copy to CMakeLists.txt.
  popd
  exit /b 1
)

where cmake >nul 2>nul
if errorlevel 1 (
  echo ERROR: cmake.exe is not in PATH.
  popd
  exit /b 1
)

cmake -S . -B build -G "Visual Studio 17 2022" -A x64
if errorlevel 1 goto :fail

cmake --build build --config Game__Shipping__Win64 --target SquadHeightRuntimeCpp
if errorlevel 1 goto :fail

echo.
echo SUCCESS
 echo Built package:
echo   %WORKSPACE%\build\package\Mods\SquadHeightRuntimeCpp\
popd
exit /b 0

:fail
set "ERR=%errorlevel%"
echo.
echo BUILD FAILED with exit code %ERR%.
popd
exit /b %ERR%
