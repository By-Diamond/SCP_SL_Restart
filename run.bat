@echo off
setlocal
cd /d "%~dp0"

REM ============================================================
REM  SL Restart 直接运行（用 dist 里已经编译好的 exe，不打包）
REM    要重新编译请用 build.bat
REM ============================================================

echo ==================================================
echo   SL Restart  启动
echo ==================================================
echo.

if not exist "dist\SL Restart.exe" (
  echo [X] 还没有 dist\SL Restart.exe，请先运行 build.bat 编译。
  echo.
  pause
  exit /b 1
)

tasklist /FI "IMAGENAME eq SL Restart.exe" 2>nul | find /i "SL Restart.exe" >nul
if not errorlevel 1 (
  echo 已经在运行了，托盘图标在右下角。
  echo 想换成新编译的版本：先右键托盘图标 -^> 退出程序，再运行本脚本。
  echo.
  ping -n 5 127.0.0.1 >nul
  exit /b 0
)

echo 启动 dist\SL Restart.exe ...
start "" "%~dp0dist\SL Restart.exe"
echo.
echo 已启动，托盘图标在右下角。
echo   左键托盘图标 = 显示/隐藏人数条
echo   右键托盘图标 = 菜单（暂停 / 编辑服务器清单 / 提醒设置 / 退出）
echo.
ping -n 5 127.0.0.1 >nul
exit /b 0
