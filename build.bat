@echo off
setlocal
cd /d "%~dp0"

echo ==================================================
echo  SL Restart - onefile build (PyInstaller)
echo ==================================================
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo [X] python not found in PATH. Install Python 3.9+ first.
  goto :fail
)

echo [1/4] Installing build dependencies ...
python -m pip install -r requirements-build.txt
if errorlevel 1 goto :fail

echo.
echo [2/4] Generating app icon ...
python tools\make_icon.py
if errorlevel 1 goto :fail

echo.
echo [3/4] Building single-file exe (this takes a while) ...
python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name "SL Restart" ^
  --icon assets\app.ico ^
  --version-file tools\version_info.txt ^
  sl_restart.py
if errorlevel 1 goto :fail

echo.
echo [4/4] Done.
echo.
echo   Output : dist\SL Restart.exe
echo   settings.json / history.csv / latest.md / events.log 都生成在 exe 同目录。
echo   运行后看右下角托盘图标（左键 = 显示/隐藏人数条，右键 = 菜单）。
echo.
pause
exit /b 0

:fail
echo.
echo [X] Build failed - scroll up for the error message.
pause
exit /b 1
