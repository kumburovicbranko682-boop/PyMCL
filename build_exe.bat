@echo off
rem Rebuild dist\PyMCL.exe (windowed, no console).
rem Keep this file ASCII-only: cmd.exe on Chinese Windows reads bat files as GBK.
cd /d "%~dp0"

set "BUILD_LOG=build_exe.log"
echo ============================================ > "%BUILD_LOG%"
echo [build_exe.bat] started %date% %time% >> "%BUILD_LOG%"

rem ---- Find Python ----
set "PYEXE="
set "PYARGS="

rem 1) workbuddy env (developer machine only)
if exist "C:\Users\Administrator\.workbuddy\binaries\python\envs\pymcl5\Scripts\python.exe" (
  set "PYEXE=C:\Users\Administrator\.workbuddy\binaries\python\envs\pymcl5\Scripts\python.exe"
)

rem 2) plain python on PATH
if not defined PYEXE (
  python -c "import sys" >nul 2>nul
  if not errorlevel 1 set "PYEXE=python"
)

rem 3) py launcher with specific version
if not defined PYEXE (
  py -3.13 -c "import sys" >nul 2>nul
  if not errorlevel 1 (
    set "PYEXE=py"
    set "PYARGS=-3.13"
  )
)
if not defined PYEXE (
  py -3.12 -c "import sys" >nul 2>nul
  if not errorlevel 1 (
    set "PYEXE=py"
    set "PYARGS=-3.12"
  )
)
if not defined PYEXE (
  py -3.11 -c "import sys" >nul 2>nul
  if not errorlevel 1 (
    set "PYEXE=py"
    set "PYARGS=-3.11"
  )
)

if not defined PYEXE (
  echo.
  echo [ERROR] Python not found. Install Python 3.11+ from https://www.python.org/downloads/
  echo         Make sure "Add Python to PATH" is checked when installing.
  echo.
  echo         The build script tried: python, py -3.13, py -3.12, py -3.11
  echo.
  pause
  exit /b 1
)

echo [1/3] Building with: "%PYEXE%" %PYARGS%
"%PYEXE%" %PYARGS% --version >> "%BUILD_LOG%" 2>&1
if errorlevel 1 (
  echo [ERROR] Python check failed. See %BUILD_LOG% for details.
  pause
  exit /b 1
)

echo [2/3] Installing build + app dependencies if needed...
"%PYEXE%" %PYARGS% -m pip install "pyinstaller==6.10.0" "requests" >> "%BUILD_LOG%" 2>&1
if errorlevel 1 (
  echo [ERROR] pip install failed. See %BUILD_LOG% for details.
  pause
  exit /b 1
)
if exist requirements.txt (
  "%PYEXE%" %PYARGS% -m pip install -r requirements.txt >> "%BUILD_LOG%" 2>&1
  if errorlevel 1 (
    echo [ERROR] Failed to install app dependencies. See %BUILD_LOG% for details.
    pause
    exit /b 1
  )
)

echo [3/3] Building windowed exe: dist\PyMCL.exe
"%PYEXE%" %PYARGS% -m PyInstaller --noconfirm --clean PyMCL.spec >> "%BUILD_LOG%" 2>&1
if errorlevel 1 (
  echo [ERROR] PyInstaller build failed. See %BUILD_LOG% for details.
  type "%BUILD_LOG%" 2>nul
  pause
  exit /b 1
)

echo.
echo ============================================
echo   Done: dist\PyMCL.exe
echo   Build log: %BUILD_LOG%
echo   Run with:  dist\PyMCL.exe
echo ============================================
echo.
if /I "%CI%"=="true" exit /b 0
if /I "%~1"=="--nopause" exit /b 0
if not "%NOPAUSE%"=="" exit /b 0
pause
exit /b 0