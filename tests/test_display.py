"""验证 Windows 展示客户端的配置边界及真实子进程恢复，全部使用临时配置和临时数据库。"""

import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.name != "nt", reason="原生展示客户端仅支持 Windows")


@pytest.fixture(scope="module")
def native_programs(tmp_path_factory):
    """编译原生测试入口；tmp_path_factory 为临时目录工具；返回展示 EXE、测试 EXE 和独立产物目录。"""
    from tools.display.build import ASSEMBLIES, CACHE, SDK_NAME, build_client

    if not (CACHE / "tools" / SDK_NAME).is_file():
        pytest.skip("本机尚未缓存 WebView2 SDK，请先运行 tools/display/build.py；测试不自动联网")
    directory = tmp_path_factory.mktemp("display-native")
    executable = build_client(directory, directory / "build.log")
    harness = directory / "DisplayTests.exe"
    compiler = Path(os.environ["SystemRoot"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    command = [str(compiler), "/nologo", "/utf8output", "/target:exe", "/platform:x64",
               "/main:BigScreen.Display.DisplayTests", "/out:" + str(harness),
               "/r:System.dll", "/r:System.Core.dll", "/r:System.Drawing.dll", "/r:System.Windows.Forms.dll",
               *["/r:" + str(directory / name) for name in ASSEMBLIES],
               *[str(path) for path in sorted((ROOT / "tools/display").glob("*.cs"))],
               str(ROOT / "tests/display/DisplayTests.cs")]
    subprocess.run(command, check=True, capture_output=True, timeout=30)
    harness.with_suffix(".exe.config").write_bytes(executable.with_suffix(".exe.config").read_bytes())
    return executable, harness, directory


def test_native_settings_and_legacy_configuration(native_programs, tmp_path):
    """验证真实 .NET 配置和旧版兼容；参数为已编译程序及临时目录；断言所有边界检查通过。"""
    _, harness, _ = native_programs
    result = subprocess.run([str(harness), str(tmp_path)], capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.display_ui
def test_exit_confirmation_stays_on_primary_screen(native_programs, tmp_path):
    """验证副屏所属窗口的退出确认；参数为隔离程序和目录；断言主屏居中及确认、取消行为正确。"""
    if os.environ.get("BIGSCREEN_TEST_DISPLAY_UI") != "1":
        pytest.skip("需交互桌面；设置 BIGSCREEN_TEST_DISPLAY_UI=1 后运行此测试")
    _, harness, _ = native_programs
    result = subprocess.run([str(harness), str(tmp_path), "dialogs"], capture_output=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


def wait_for(predicate, timeout=15):
    """轮询有截止时间的条件；predicate 为检查函数、timeout 为秒数；返回首次有效结果，超时失败。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.2)
    pytest.fail("等待展示程序状态超时")


@pytest.mark.display_ui
def test_supervisor_recovers_window_and_late_server(native_programs, tmp_path):
    """验证真实窗口及服务晚启动恢复；参数为隔离产物及临时目录；断言页面加载和子进程重建。"""
    if os.environ.get("BIGSCREEN_TEST_DISPLAY_UI") != "1":
        pytest.skip("需交互桌面；设置 BIGSCREEN_TEST_DISPLAY_UI=1 后运行此测试")
    executable, harness, _ = native_programs
    profile = tmp_path / "展示配置 空格"
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    subprocess.run([str(harness), str(profile), "init", f"http://127.0.0.1:{port}"], check=True, timeout=10)
    log = profile / "display.log"
    supervisor = subprocess.Popen([str(executable), "--profile", str(profile)], creationflags=subprocess.CREATE_NO_WINDOW)
    service = None
    request = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def messages():
        """读取隔离程序日志；无参数；返回日志文本，尚未创建时返回空文本。"""
        return log.read_text(encoding="utf-8-sig") if log.exists() else ""

    def children():
        """读取本测试守护进程启动的子进程；无参数；返回已记录的进程 ID 列表。"""
        return [int(value) for value in re.findall(r"展示窗口已启动，进程 (\d+)", messages())]

    try:
        first = wait_for(lambda: children())[-1]
        # 先打开窗口再启动服务，验证启动顺序和临时断连不会留下永久空白窗口。
        service = subprocess.Popen([sys.executable, str(ROOT / "server.py"), "--host", "127.0.0.1", "--port", str(port),
                                    "--database", str(tmp_path / "business.sqlite3")],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        wait_for(lambda: "大屏页面加载完成" in messages(), timeout=25)
        with request.open(f"http://127.0.0.1:{port}/api/health", timeout=3) as response:
            assert response.status == 200
        # 仅终止本测试日志中记录的窗口进程，验证守护恢复，不接触用户业务服务。
        os.kill(first, 15)
        wait_for(lambda: len(children()) >= 2)
        assert children()[-1] != first
        wait_for(lambda: messages().count("大屏页面加载完成") >= 2, timeout=25)
        assert supervisor.poll() is None
    finally:
        # Windows 虚拟环境可能通过中间解释器启动服务，按本测试创建的进程树统一回收。
        if supervisor.poll() is None:
            subprocess.run(["taskkill.exe", "/PID", str(supervisor.pid), "/T", "/F"], capture_output=True, timeout=10)
        supervisor.wait(timeout=10)
        if service is not None and service.poll() is None:
            subprocess.run(["taskkill.exe", "/PID", str(service.pid), "/T", "/F"], capture_output=True, timeout=10)
            service.wait(timeout=10)
