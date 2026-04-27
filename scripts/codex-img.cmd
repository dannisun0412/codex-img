@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "PY_SCRIPT=%SCRIPT_DIR%codex_img.py"

if not "%CODEX_IMG_PYTHON%"=="" (
  "%CODEX_IMG_PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
  if not %ERRORLEVEL%==0 (
    echo codex-img: CODEX_IMG_PYTHON must point to Python 3.11+. 1>&2
    exit /b 127
  )
  "%CODEX_IMG_PYTHON%" "%PY_SCRIPT%" %*
  exit /b %ERRORLEVEL%
)

where py >nul 2>nul
if %ERRORLEVEL%==0 (
  py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
  if %ERRORLEVEL%==0 (
    py -3 "%PY_SCRIPT%" %*
    exit /b %ERRORLEVEL%
  )
)

where python >nul 2>nul
if %ERRORLEVEL%==0 (
  python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
  if %ERRORLEVEL%==0 (
    python "%PY_SCRIPT%" %*
    exit /b %ERRORLEVEL%
  )
)

where python3 >nul 2>nul
if %ERRORLEVEL%==0 (
  python3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
  if %ERRORLEVEL%==0 (
    python3 "%PY_SCRIPT%" %*
    exit /b %ERRORLEVEL%
  )
)

where uv >nul 2>nul
if %ERRORLEVEL%==0 (
  uv run python "%PY_SCRIPT%" %*
  exit /b %ERRORLEVEL%
)

echo codex-img: no Python runtime found. Install Python 3.11+ or set CODEX_IMG_PYTHON. 1>&2
exit /b 127
