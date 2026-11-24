"""验证 Windows 独占监听，防止重复服务混用同一端口。"""

import socket
import sys

import pytest
from waitress.server import create_server

from server import exclusive_listener, main


@pytest.mark.skipif(sys.platform != "win32", reason="仅验证 Windows 端口独占行为")
def test_waitress_keeps_exclusive_port():
    """验证真实 Waitress 监听；无参数；断言即使后启动实例开启地址复用也无法共用端口。"""
    def application(environ, start_response):
        """提供最小测试应用；参数为 WSGI 环境和响应函数；返回空响应。"""
        start_response("200 OK", [])
        return []

    with exclusive_listener("127.0.0.1", 0) as listener:
        server = create_server(application, sockets=[listener], threads=1)
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as duplicate:
                duplicate.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                with pytest.raises(OSError):
                    duplicate.bind(listener.getsockname())
                    duplicate.listen()
        finally:
            server.close()
            server.task_dispatcher.shutdown()


@pytest.mark.skipif(sys.platform != "win32", reason="仅验证 Windows 重复启动行为")
def test_duplicate_start_exits_before_database_creation(tmp_path, monkeypatch, capsys):
    """验证重复启动失败；参数为隔离目录、补丁及输出捕获；断言明确退出且不创建数据库。"""
    database = tmp_path / "duplicate.sqlite3"
    with exclusive_listener("127.0.0.1", 0) as listener:
        listener.listen()
        port = listener.getsockname()[1]
        monkeypatch.setattr(sys, "argv", ["server.py", "--host", "127.0.0.1", "--port", str(port), "--database", str(database)])
        with pytest.raises(SystemExit) as error:
            main()
    assert error.value.code == 1
    assert "请先关闭已运行的服务" in capsys.readouterr().err
    assert not database.exists()
