"""验证真正的冻结程序，所有测试数据均位于本次构建目录，不接触开发数据库。"""

import argparse
from datetime import datetime, timezone
import http.cookiejar
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import time
import urllib.parse
import urllib.request


def verify(package, output):
    """验证冻结程序；package 为程序目录、output 为报告路径；返回检查结果，失败抛出异常。"""
    root = output.parent / f"离线验证 空格路径-{time.time_ns()}"
    root.mkdir()
    data = root / "客户自选数据目录"
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    config = root / "BigScreen.ini"
    config.write_text(f"[server]\nport={port}\ndata_directory={data}\n", encoding="utf-8-sig")
    executable = package / "BigScreen.exe"
    # 检查展示文件完整性；无桌面的打包验证不伪称完成 LED 界面验收。
    for name in ["BigScreen.Display.exe", "Microsoft.Web.WebView2.Core.dll", "Microsoft.Web.WebView2.WinForms.dll",
                 "WebView2Loader.dll", "MicrosoftEdgeWebView2RuntimeInstallerX64.exe"]:
        with (package / name).open("rb") as stream:
            assert stream.read(2) == b"MZ", f"展示交付文件缺失或损坏：{name}"
    environment = {k: v for k, v in os.environ.items() if k.upper() not in {"PYTHONPATH", "PYTHONHOME", "BIGSCREEN_DATABASE"}}
    environment["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")
    environment["PYTHONUTF8"] = "1"
    # 仅连接本机，不使用环境中的代理，验证过程中无需网络。
    browser = urllib.request.build_opener(urllib.request.ProxyHandler({}),
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    base = f"http://127.0.0.1:{port}"

    def request(path, payload=None, method=None):
        """访问验证服务；path 为相对路径、payload 为 JSON、method 为方法；返回响应原始字节。"""
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(base + urllib.parse.quote(path, safe="/?=&"), data=body,
            headers={"Content-Type": "application/json"} if body else {}, method=method)
        print(f"验证接口：{path}", flush=True)
        with browser.open(req, timeout=10) as response:
            assert response.status == 200, path
            return response.read()

    def start(log):
        """启动冻结服务；log 为日志流；返回健康检查成功的子进程，启动失败时终止该进程。"""
        process = subprocess.Popen([str(executable), "--host", "127.0.0.1", "--config", str(config)],
            cwd=root, env=environment, stdout=log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            deadline = time.monotonic() + 35
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f"冻结程序启动失败，退出码 {process.returncode}")
                try:
                    if json.loads(request("/api/health"))["status"] == "ok":
                        return process
                except OSError:
                    time.sleep(0.2)
            raise RuntimeError("冻结程序未在 35 秒内就绪")
        except BaseException:
            process.terminate()
            process.wait(timeout=10)
            raise

    with (root / "server.log").open("wb") as log:
        process = start(log)
        try:
            for path in ["/screen", "/login", "/static/js/editor.js", "/static/vendor/vue.global.prod.js",
                         "/static/vendor/element-plus.js", "/static/vendor/echarts.min.js", "/assets/01_钢卷.png"]:
                assert len(request(path)) > 50, path
            dashboard = json.loads(request("/api/dashboard"))
            assert dashboard["version"] == 0, "安装包夹带了业务数据"
            request("/api/auth/login", {"username": "admin", "password": "admin123"})
            assert b"<html" in request("/edit").lower()
            snapshot = json.loads(request("/api/employees"))
            snapshot["employees"].append({"username": "package_probe", "name": "离线验证员工", "password": "test123", "enabled": True})
            request("/api/employees", {"version": snapshot["version"], "employees": snapshot["employees"]}, "PUT")
            backup_directory = root / "备份目录"
            subprocess.run([str(executable), "--backup-database", str(data / "dashboard.sqlite3"),
                            "--backup-directory", str(backup_directory)], cwd=root, env=environment,
                           stdout=log, stderr=subprocess.STDOUT, check=True, timeout=25)
        finally:
            process.terminate()
            process.wait(timeout=10)
        process = start(log)
        try:
            request("/api/auth/login", {"username": "package_probe", "password": "test123"})
            assert json.loads(request("/api/auth/session"))["employee"]["name"] == "离线验证员工"
        finally:
            process.terminate()
            process.wait(timeout=10)
    backup = next(backup_directory.glob("*.sqlite3"))
    with sqlite3.connect(backup) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT COUNT(*) FROM employee WHERE username='package_probe'").fetchone()[0] == 1
    assert not (package / "data").exists(), "验证数据不得写入交付目录"
    result = {"passed": True, "checked_at": datetime.now(timezone.utc).isoformat(),
              "checks": ["无 Python 路径运行", "中文及空格自选数据目录", "本地页面和资源", "空白新数据库",
                         "登录和 API 写入", "服务重启后数据保留", "运行中 SQLite 在线备份及完整性",
                         "专用展示程序、内核组件及离线运行库文件"]}
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("离线程序验证通过", flush=True)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="验证冻结后的离线程序")
    parser.add_argument("package", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    verify(options.package.resolve(), options.output.resolve())
