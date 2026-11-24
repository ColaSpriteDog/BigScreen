"""验证登录页及后台侧栏 Logo 的独立配置、升级兼容和保存生效。"""

from pathlib import Path

import pytest
from playwright.sync_api import expect

from backend import create_app
from backend.db import connect_database
from tests.test_api import settings_payload


@pytest.mark.parametrize('field', ['show_login_logo', 'show_sidebar_logo'])
def test_backend_logo_configuration_is_independent_and_persistent(client, app, clock, field):
    """验证配置保存；参数为接口、应用、时钟及字段；断言默认显示、独立控制、旧请求兼容及重启保留。"""
    before = client.get('/api/editor').get_json()['settings']
    assert before['show_login_logo'] is True and before['show_sidebar_logo'] is True
    draft = settings_payload(client)
    draft[field] = False
    assert client.put('/api/settings', json=draft).status_code == 200
    current = client.get('/api/editor').get_json()['settings']
    assert current[field] is False
    for other in {'show_logo', 'show_login_logo', 'show_sidebar_logo'} - {field}:
        assert current[other] is before[other]
    assert current['_audit']['modified_by_name'] == '管理员'
    assert client.put('/api/settings', json=settings_payload(client)).status_code == 200
    assert client.get('/api/editor').get_json()['settings'] == {**current, 'version': current['version'] + 1}
    restarted = create_app({'TESTING': True, 'DATABASE': app.config['DATABASE'],
                            'NOW_PROVIDER': lambda: clock['now']}).test_client()
    assert restarted.post('/api/auth/login', json={'username': 'admin', 'password': 'admin123'}).status_code == 200
    assert restarted.get('/api/editor').get_json()['settings'][field] is False
    draft = settings_payload(client)
    draft[field] = True
    assert client.put('/api/settings', json=draft).status_code == 200
    assert client.get('/api/editor').get_json()['settings'][field] is True


@pytest.mark.parametrize('field', ['show_login_logo', 'show_sidebar_logo'])
@pytest.mark.parametrize('invalid', [None, 0, 1, 'false', [], {}])
def test_invalid_backend_logo_setting_keeps_all_data(client, field, invalid):
    """验证类型校验；client、field、invalid 为接口、字段及非法值；断言配置、版本和人员记录不被修改。"""
    before = client.get('/api/editor').get_json()
    draft = settings_payload(client)
    draft.update({field: invalid, 'show_logo': True, 'safety_start_date': '2026-11-01'})
    draft['lines'][0]['name'] = '不能保存的名称'
    assert client.put('/api/settings', json=draft).status_code == 400
    assert client.get('/api/editor').get_json() == before


def test_previous_database_adds_backend_logo_defaults_without_resetting_screen(tmp_path, clock):
    """验证旧库升级；tmp_path、clock 为隔离目录和时钟；断言后台默认显示并保留大屏选择及其他配置。"""
    path = tmp_path / 'previous.sqlite3'
    schema = (Path(__file__).resolve().parents[1] / 'backend/schema.sql').read_text(encoding='utf-8')
    for field in ('show_login_logo', 'show_sidebar_logo'):
        schema = schema.replace(f'    {field} INTEGER NOT NULL DEFAULT 1 CHECK ({field} IN (0, 1)),\n', '')
    with connect_database(path) as db:
        db.executescript(schema)
        db.execute("INSERT INTO screen_settings(id,timezone,safety_start_date,show_logo,version,updated_at) "
                   "VALUES(1,'UTC','2026-11-01',1,12,?)", (clock['now'].isoformat(),))
        db.execute('PRAGMA user_version=8')
    config = {'TESTING': True, 'DATABASE': str(path), 'NOW_PROVIDER': lambda: clock['now']}
    migrated = create_app(config).test_client()
    assert migrated.post('/api/auth/login', json={'username': 'admin', 'password': 'admin123'}).status_code == 200
    data = migrated.get('/api/editor').get_json()
    assert data['settings']['show_login_logo'] is True
    assert data['settings']['show_sidebar_logo'] is True
    assert (data['settings']['show_logo'], data['version'], data['settings']['safety_start_date']) == (True, 12, '2026-11-01')
    draft = settings_payload(migrated)
    draft.update(show_login_logo=False, show_sidebar_logo=False)
    assert migrated.put('/api/settings', json=draft).status_code == 200
    restarted = create_app(config).test_client()
    assert restarted.post('/api/auth/login', json={'username': 'admin', 'password': 'admin123'}).status_code == 200
    current = restarted.get('/api/editor').get_json()['settings']
    assert current['show_login_logo'] is False and current['show_sidebar_logo'] is False
    assert current['show_logo'] is True
    with connect_database(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 9


def test_backend_logo_switches_apply_only_after_save_and_preserve_layout(ui_context, browser, live_server, client):
    """验证真实页面；参数为已登录上下文、浏览器、服务及接口；断言草稿隔离、显示切换和隐藏时可正常登录。"""
    editor = ui_context.new_page()
    errors = []
    editor.on('pageerror', lambda error: errors.append(str(error)))
    editor.goto(live_server + '/edit')
    expect(editor.get_by_test_id('save-button')).to_be_enabled()
    sidebar_logo = editor.locator('.sidebar-brand img')
    expect(sidebar_logo).to_be_visible()
    menu_box = editor.get_by_role('tab', name='生产日报', exact=True).bounding_box()
    editor.get_by_role('tab', name='流程与设置', exact=True).click()
    login_toggle = editor.get_by_role('switch', name='显示登录页 Logo', exact=True)
    sidebar_toggle = editor.get_by_role('switch', name='显示侧栏 Logo', exact=True)
    expect(login_toggle).to_be_checked()
    expect(sidebar_toggle).to_be_checked()
    with browser.new_context(viewport={'width': 1440, 'height': 900}) as anonymous:
        login = anonymous.new_page()
        login.on('pageerror', lambda error: errors.append(str(error)))
        login.goto(live_server + '/login')
        login_logo = login.locator('.brand-logo')
        expect(login_logo).to_be_visible()
        slogan_box = login.locator('.slogan').bounding_box()
        editor.get_by_test_id('show-login-logo').click()
        editor.get_by_test_id('show-sidebar-logo').click()
        expect(editor.get_by_test_id('save-status')).to_contain_text('2')
        expect(sidebar_logo).to_be_visible()
        login.reload()
        expect(login_logo).to_be_visible()
        editor.get_by_test_id('save-button').click()
        expect(editor.get_by_test_id('save-status')).to_have_text('所有修改已保存')
        expect(sidebar_logo).not_to_be_visible()
        assert editor.get_by_role('tab', name='生产日报', exact=True).bounding_box() == menu_box
        login.reload()
        expect(login_logo).not_to_be_visible()
        assert login.locator('.slogan').bounding_box() == slogan_box
        assert 'visibility: hidden;' in login.locator('.logo-box').get_attribute('style')
        assert client.get('/api/dashboard').get_json()['show_logo'] is False
        editor.reload()
        expect(editor.get_by_test_id('save-button')).to_be_enabled()
        expect(sidebar_logo).not_to_be_visible()
        editor.get_by_role('tab', name='流程与设置', exact=True).click()
        expect(login_toggle).not_to_be_checked()
        expect(sidebar_toggle).not_to_be_checked()
        Path('output/playwright').mkdir(parents=True, exist_ok=True)
        editor.screenshot(path='output/playwright/backend-logo-settings.png', full_page=True)
        login.screenshot(path='output/playwright/login-logo-hidden.png')
        # 只恢复登录页 Logo，侧栏选择保持关闭，证明两个开关独立。
        editor.get_by_test_id('show-login-logo').click()
        editor.get_by_test_id('save-button').click()
        expect(editor.get_by_test_id('save-status')).to_have_text('所有修改已保存')
        login.reload()
        expect(login_logo).to_be_visible()
        expect(sidebar_logo).not_to_be_visible()
        editor.get_by_test_id('show-login-logo').click()
        editor.get_by_test_id('save-button').click()
        expect(editor.get_by_test_id('save-status')).to_have_text('所有修改已保存')
        login.reload()
        expect(login_logo).not_to_be_visible()
        login.get_by_label('登录账号', exact=True).fill('admin')
        login.get_by_label('密码', exact=True).fill('admin123')
        login.get_by_role('button', name='登录', exact=True).click()
        expect(login.get_by_test_id('save-button')).to_be_enabled()
        expect(login.locator('.sidebar-brand img')).not_to_be_visible()
        # 单独恢复侧栏后，未登录页面仍保持隐藏。
        editor.get_by_test_id('show-sidebar-logo').click()
        editor.get_by_test_id('save-button').click()
        expect(editor.get_by_test_id('save-status')).to_have_text('所有修改已保存')
        expect(sidebar_logo).to_be_visible()
        assert client.get('/api/editor').get_json()['settings']['show_login_logo'] is False
    assert not errors, errors
