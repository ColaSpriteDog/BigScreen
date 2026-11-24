@echo off
chcp 65001 >nul
setlocal
set "PYTHONUTF8=1"
pushd "%~dp0"
rem 优先使用项目环境；实际打包依赖安装在独立目录，不改动项目环境。
if exist ".venv\Scripts\python.exe" goto project_python
where py >nul 2>&1
if not errorlevel 1 goto python_launcher
where python >nul 2>&1
if not errorlevel 1 goto system_python
echo 未找到 Python。请在开发电脑安装 Python 3.10 或以上的 64 位版本。
set "BUILD_EXIT=1"
goto finish
:project_python
".venv\Scripts\python.exe" "tools\packaging\build.py" %*
goto result
:python_launcher
py -3 "tools\packaging\build.py" %*
goto result
:system_python
python "tools\packaging\build.py" %*
:result
set "BUILD_EXIT=%ERRORLEVEL%"
:finish
popd
if not "%BUILD_EXIT%"=="0" echo 制作失败，请查看上方错误及 output\packaging 下的日志。
if "%~1"=="" pause
exit /b %BUILD_EXIT%
