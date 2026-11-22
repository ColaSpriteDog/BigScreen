from datetime import datetime, timezone
import os
from pathlib import Path
from threading import Thread

import pytest

from backend import create_app


@pytest.fixture
def clock():
    """提供可推进的测试时钟；无参数；返回包含 UTC 当前时间的字典。"""
    return {"now": datetime(2026, 11, 21, 12, tzinfo=timezone.utc)}


@pytest.fixture
def app(tmp_path, clock):
    """创建隔离应用；tmp_path 为临时目录、clock 为时钟；返回测试 Flask 应用。"""
    return create_app({"TESTING": True, "DATABASE": str(tmp_path / "test.sqlite3"),
                       "BUSINESS_TIMEZONE": "UTC", "NOW_PROVIDER": lambda: clock["now"]})


@pytest.fixture
def client(app):
    """创建已登录 HTTP 测试客户端；app 为隔离应用；返回默认管理员的 Flask 客户端。"""
    instance = app.test_client()
    assert instance.post('/api/auth/login', json={'username': 'admin', 'password': 'admin123'}).status_code == 200
    return instance


@pytest.fixture
def live_server(app):
    """启动真实 HTTP 测试服务；app 为隔离应用；产生本机随机端口地址并在测试后关闭。"""
    from werkzeug.serving import make_server
    server = make_server('127.0.0.1', 0, app, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}'
    server.shutdown()
    thread.join(timeout=5)
    server.server_close()


@pytest.fixture(scope='session')
def browser():
    """启动真实无头浏览器；无参数；产生 Chrome 实例并在全部浏览器测试后关闭。"""
    from playwright.sync_api import sync_playwright
    candidates = [os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE', ''),
                  'C:/Program Files/Google/Chrome/Application/chrome.exe',
                  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe']
    executable = next((path for path in candidates if path and Path(path).is_file()), None)
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(executable_path=executable, headless=True)
        yield instance
        instance.close()


@pytest.fixture
def ui_context(browser, live_server):
    """创建已登录页面上下文；browser、live_server 为浏览器及服务；产生 1440×720 上下文，测试后关闭。"""
    context = browser.new_context(viewport={'width': 1440, 'height': 720}, reduced_motion='reduce')
    context.set_default_timeout(10000)
    assert context.request.post(live_server + '/api/auth/login', data={'username': 'admin', 'password': 'admin123'}).ok
    yield context
    context.close()
