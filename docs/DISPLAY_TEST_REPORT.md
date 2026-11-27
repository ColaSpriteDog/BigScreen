# 专用大屏展示程序验证

验证日期：2026-11-27。环境：Windows x64、.NET Framework（Release 534296）、WebView2 Runtime 158；
实际窗口在 1920 × 1080 桌面检查。所有业务验证使用临时 SQLite 和隔离的展示配置。

## 已验证

- 原生 C# 展示程序使用系统编译器编译成功，无编译错误或警告。
- 无密码配置保存、中文及空格目录、配置备份、旧版密码字段忽略及其他展示设置保留。
- 服务地址限制、安装端口沿用、损坏配置拒绝、WebView2 最低版本检查。
- 关闭事件拦截、Windows 关机事件放行、最小化恢复分支。
- 真实窗口先于服务启动，服务就绪后自动加载页面。
- 强制结束本次测试的展示子进程，守护自动重建窗口并重新加载页面。
- 实际窗口没有浏览器工具栏，页面标题、图表和工艺图片正常显示。
- 实际按 Alt+F4 后窗口保留；当前版本的 Ctrl+Shift+Q 改为无密码退出确认。
- Inno Setup 安装脚本使用测试资源完成编译检查；没有执行安装程序。

## 主屏退出确认修正

退出确认不再按大屏所属窗口居中，改为始终显示在 Windows 主屏工作区中央，默认焦点仍为“继续展示”。
运行期间注册全局 Ctrl+Shift+Q，正常退出和窗口销毁时释放；若组合键已被占用，记录日志并保留窗口内快捷键。

本次定向命令：

```powershell
.\.venv\Scripts\python.exe -X utf8 -c "import os,pytest; os.environ['BIGSCREEN_TEST_DISPLAY_UI']='1'; raise SystemExit(pytest.main(['-q','tests/test_display.py','--junitxml=output/tests/display-primary.xml']))"
```

结果：3 项通过，客户端重新编译成功。界面测试将所属窗口放到副屏或主屏外，验证退出确认仍在主屏居中、
默认焦点、继续展示和退出按钮的返回行为。全局组合键在其他软件获得焦点时的实际触发仍需现场验证。

回归命令（设置仅作用于此测试进程）：

```powershell
.\.venv\Scripts\python.exe -X utf8 -c "import os,pytest; os.environ['BIGSCREEN_TEST_DISPLAY_UI']='1'; raise SystemExit(pytest.main(['-q','tests/test_display.py','tests/test_packaging.py','tests/test_server.py','--junitxml=output/tests/display-verified.xml']))"
```

结果：18 项通过。JUnit 报告位于 `output/tests/display-verified.xml`。
不设置 `BIGSCREEN_TEST_DISPLAY_UI=1` 时，真实窗口测试会跳过，普通配置测试仍执行。

## 待现场验收

- 客户 LED 分辨率、Windows 缩放、多显示器选择与热插拔。
- 安装程序的 WebView2 离线安装、后台服务、快捷方式及登录后自动展示。
- 完整安装包制作及客户离线安装；本次只编译客户端和检查安装脚本。
- 界面连续 45 秒无心跳、网页内核崩溃的自动恢复路径。
- 无密码退出确认的界面操作及设置修改；本次覆盖了无密码启动、旧版配置兼容和关闭拦截。
- 真实 Windows 重启、注销和关机，以及现场持续运行。

本次没有修改正式业务数据库、Windows 启动项、服务注册或防火墙。
