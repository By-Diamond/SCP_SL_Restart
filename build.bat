@echo off
setlocal
cd /d "%~dp0"

REM ============================================================
REM  SL Restart 一键打包
REM    build.bat            编译（编完停一下看结果）
REM    build.bat notest     不跑自检，直接编译
REM    build.bat nopause    编译完不停（给 run.bat 用）
REM ============================================================

set "NOPAUSE="
set "SKIPTEST="
echo %* | findstr /i /c:"nopause" >nul && set "NOPAUSE=1"
echo %* | findstr /i /c:"notest" >nul && set "SKIPTEST=1"

echo ==================================================
echo   SL Restart  一键打包
echo ==================================================
echo.

REM ---------- 1/6 找 Python ----------
set "PY="
python -c "import sys" >nul 2>&1
if not errorlevel 1 set "PY=python"
if not defined PY (
  py -3 -c "import sys" >nul 2>&1
  if not errorlevel 1 set "PY=py -3"
)
if not defined PY (
  echo [X] 没找到 Python。
  echo     请先安装 Python 3.9+，安装时勾选 "Add python.exe to PATH"。
  goto fail
)
for /f "delims=" %%v in ('%PY% -c "import sys;print(sys.version.split()[0])"') do set "PYVER=%%v"
echo [1/6] Python %PYVER%   ^(%PY%^)

REM ---------- 2/6 依赖 ----------
%PY% -c "import PySide6, PyInstaller" >nul 2>&1
if errorlevel 1 (
  echo [2/6] 缺依赖，正在安装 requirements-build.txt ...
  %PY% -m pip install -r requirements-build.txt
  if errorlevel 1 goto fail
) else (
  echo [2/6] 依赖已就绪 ^(PySide6 / PyInstaller^)
)

REM ---------- 3/6 自检 ----------
if defined SKIPTEST (
  echo [3/6] 跳过自检
) else (
  if exist "tests\verify_logic.py" (
    echo [3/6] 跑纯逻辑自检 ...
    %PY% "tests\verify_logic.py"
    if errorlevel 1 (
      echo.
      echo [X] 自检没过，不打包。确认可以跳过时用：build.bat notest
      goto fail
    )
  ) else (
    echo [3/6] 没有 tests\verify_logic.py，跳过自检
  )
)

REM ---------- 4/6 图标 ----------
echo [4/6] 生成图标 ...
%PY% "tools\make_icon.py" >nul 2>&1
if errorlevel 1 (
  if exist "assets\app.ico" (
    echo       生成失败 ^(多半没装 Pillow^)，沿用现有 assets\app.ico
  ) else (
    echo [X] 既生成不了图标、也没有 assets\app.ico，没法打包
    goto fail
  )
)

REM ---------- 5/6 打包 ----------
echo [5/6] 打包中，第一次大约 1 分钟 ...
%PY% -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name SL_Restart_build ^
  --icon "assets\app.ico" ^
  --version-file "tools\version_info.txt" ^
  --workpath build --distpath dist ^
  "sl_restart.py"
if errorlevel 1 goto fail
if exist "SL_Restart_build.spec" del /q "SL_Restart_build.spec"

REM ---------- 6/6 就位 ----------
set "NEWEXE=dist\SL_Restart_build.exe"
set "OUTEXE=dist\SL Restart.exe"
if not exist "%NEWEXE%" (
  echo [X] 没生成 %NEWEXE%
  goto fail
)

move /y "%NEWEXE%" "%OUTEXE%" >nul 2>&1
if errorlevel 1 (
  echo       旧版 exe 正在运行，先改名让位 ^(旧版照常跑，退出后就不再用了^)
  del /q "dist\旧版_*_可删.exe" >nul 2>&1
  move /y "%OUTEXE%" "dist\旧版_%RANDOM%_可删.exe" >nul 2>&1
  move /y "%NEWEXE%" "%OUTEXE%" >nul 2>&1
)
if not exist "%OUTEXE%" (
  echo [X] 替换 dist\SL Restart.exe 失败，可能还被占用
  goto fail
)

for %%F in ("%OUTEXE%") do set "SIZE=%%~zF"
for /f "delims=" %%v in ('%PY% -c "import config;print(config.VERSION)"') do set "VER=%%v"
echo.
echo   ==================================================
echo    打包完成   v%VER%
echo    产物：%CD%\%OUTEXE%
echo    大小：%SIZE% 字节
echo   ==================================================
echo.
if not defined NOPAUSE pause
exit /b 0

:fail
echo.
echo [X] 打包失败 —— 往上翻看报错信息。
echo.
if not defined NOPAUSE pause
exit /b 1
