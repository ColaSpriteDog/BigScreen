"""验证大屏 Logo 的后台配置、持久化、旧库升级和实际页面同步。"""

from pathlib import Path

import pytest
from playwright.sync_api import expect

from backend import create_app
from backend.db import connect_database
from tests.test_api import settings_payload


def test_logo_visibility_persists_and_omitted_setting_is_preserved(client, app, clock):
    """验证配置往返；参数为接口、应用和时钟；断言默认隐藏、保存归属、重启及旧请求保留设置。"""
    assert client.get('/api/editor').get_json()['settings']['show_logo'] is False
    assert client.get('/api/dashboard').get_json()['show_logo'] is False
    draft = settings_payload(client)
    draft['show_logo'] = True
    assert client.put('/api/settings', json=draft).status_code == 200
    current = client.get('/api/editor').get_json()
    assert current['settings']['show_logo'] is True
    assert current['settings']['_audit']['modified_by_name'] == '管理员'
    # 老客户端只保存其他设置时，不能将已开启的 Logo 恢复为默认隐藏。
    assert client.put('/api/settings', json=settings_payload(client)).status_code == 200
    assert client.get('/api/editor').get_json()['settings']['_audit'] == current['settings']['_audit']
    restarted = create_app({'TESTING': True, 'DATABASE': app.config['DATABASE'],
                            'NOW_PROVIDER': lambda: clock['now']}).test_client()
    assert restarted.get('/api/dashboard').get_json()['show_logo'] is True
    draft = settings_payload(client)
    draft['show_logo'] = False
    assert client.put('/api/settings', json=draft).status_code == 200
    assert client.get('/api/dashboard').get_json()['show_logo'] is False


@pytest.mark.parametrize('invalid', [None, 0, 1, 'false', [], {}])
def test_invalid_logo_visibility_does_not_change_settings(client, invalid):
    """验证非法配置；client 为接口、invalid 为非法值；断言错误请求不改变配置、版本和人员信息。"""
    before = client.get('/api/editor').get_json()
    draft = settings_payload(client)
    draft.update(show_logo=invalid, safety_start_date='2026-11-01')
    draft['lines'][0]['name'] = '不能保存的产线'
    result = client.put('/api/settings', json=draft)
    assert result.status_code == 400
    assert result.get_json()['error'] == '大屏 Logo 显示必须为布尔值'
    assert client.get('/api/editor').get_json() == before


def test_legacy_database_adds_logo_setting_without_resetting_values(tmp_path, clock):
    """验证旧库兼容；参数为临时目录和时钟；断言新增字段默认隐藏且重复启动保留已开启值。"""
    path = tmp_path / 'legacy.sqlite3'
    schema = (Path(__file__).resolve().parents[1] / 'backend/schema.sql').read_text(encoding='utf-8')
    legacy_schema = schema.replace('    show_logo INTEGER NOT NULL DEFAULT 0 CHECK (show_logo IN (0, 1)),\n', '')
    with connect_database(path) as db:
        db.executescript(legacy_schema)
        db.execute("INSERT INTO screen_settings(id,timezone,safety_start_date,version,updated_at) "
                   "VALUES(1,'UTC','2026-11-01',12,?)", (clock['now'].isoformat(),))
        db.execute('PRAGMA user_version=7')
    config = {'TESTING': True, 'DATABASE': str(path), 'NOW_PROVIDER': lambda: clock['now']}
    migrated = create_app(config).test_client()
    data = migrated.get('/api/dashboard').get_json()
    assert data['show_logo'] is False
    assert (data['version'], data['timezone'], data['safety_days']) == (12, 'UTC', 20)
    with connect_database(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 9
        db.execute('UPDATE screen_settings SET show_logo=1 WHERE id=1')
    restarted = create_app(config).test_client()
    assert restarted.get('/api/dashboard').get_json()['show_logo'] is True


def test_logo_switch_saves_and_updates_screen_without_shifting_title(ui_context, live_server, client):
    """验证真实界面；参数为浏览器、服务及接口；断言草稿隔离、自动同步、刷新保留和标题位置稳定。"""
    screen = ui_context.new_page()
    editor = ui_context.new_page()
    errors = []
    screen.on('pageerror', lambda error: errors.append(str(error)))
    editor.on('pageerror', lambda error: errors.append(str(error)))
    screen.goto(live_server + '/screen')
    expect(screen.locator('#connectionStatus')).to_have_text('数据已同步')
    logo = screen.locator('.company-logo')
    expect(logo).not_to_be_visible()
    title_box = screen.locator('header h1').bounding_box()
    editor.goto(live_server + '/edit')
    expect(editor.get_by_test_id('save-button')).to_be_enabled()
    editor.get_by_role('tab', name='流程与设置', exact=True).click()
    toggle = editor.get_by_role('switch', name='显示大屏 Logo', exact=True)
    expect(toggle).not_to_be_checked()
    editor.get_by_test_id('show-logo').click()
    expect(editor.get_by_test_id('save-status')).to_contain_text('1')
    assert client.get('/api/dashboard').get_json()['show_logo'] is False
    expect(logo).not_to_be_visible()
    editor.get_by_test_id('save-button').click()
    expect(editor.get_by_test_id('save-status')).to_have_text('所有修改已保存')
    expect(logo).to_be_visible(timeout=10000)
    assert screen.locator('header h1').bounding_box() == title_box
    assert logo.evaluate('(image) => image.complete && image.naturalWidth > 0')
    screen.reload()
    expect(logo).to_be_visible()
    editor.reload()
    expect(editor.get_by_test_id('save-button')).to_be_enabled()
    editor.get_by_role('tab', name='流程与设置', exact=True).click()
    expect(toggle).to_be_checked()
    # 截图用于核对后台开关的位置及实际大屏标题布局。
    Path('output/playwright').mkdir(parents=True, exist_ok=True)
    editor.screenshot(path='output/playwright/logo-setting-editor.png', full_page=True)
    screen.screenshot(path='output/playwright/logo-setting-screen.png')
    editor.get_by_test_id('show-logo').click()
    editor.get_by_test_id('save-button').click()
    expect(editor.get_by_test_id('save-status')).to_have_text('所有修改已保存')
    expect(logo).not_to_be_visible(timeout=10000)
    assert screen.locator('header h1').bounding_box() == title_box
    assert not errors, errors
