@echo off
chcp 65001 >nul
pushd "%~dp0"
if not exist "output\display\BigScreen.Display.exe" (
  echo 请先执行 .venv\Scripts\python.exe tools\display\build.py 编译展示程序。
  pause
  popd
  exit /b 1
)
start "" "%~dp0output\display\BigScreen.Display.exe" %*
popd
