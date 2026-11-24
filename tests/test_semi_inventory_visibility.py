"""验证半成品显示配置的保存、升级、导入及真实界面联动，使用隔离数据库。"""

from datetime import timedelta
from pathlib import Path

import pytest
from playwright.sync_api import expect

from backend import create_app
from backend.db import connect_database
from tests.test_inventory_visibility import seed_inventory
from tests.test_warehouse_import_browser import warehouse_upload


def test_semi_visibility_filters_totals_and_preserves_records(client, clock, app):
    """验证开关及持久化；参数为客户端、时钟和应用；断言合计、人员记录和恢复显示行为。"""
    original = seed_inventory(client)
    inventory = original['inventory']
    assert all(row['show_on_screen'] is True for row in inventory['semi'])
    first_audit = inventory['semi'][0]['_audit'].copy()
    clock['now'] += timedelta(minutes=1)
    inventory['semi'][0]['show_on_screen'] = False
    assert client.put('/api/inventory', json={'version': 1, **inventory}).status_code == 200
    screen = client.get('/api/dashboard').get_json()['inventory']
    assert [row['quantity'] for row in screen['semi']] == [2895, 4854]
    assert screen['semi_totals'] == [{'quantity': 7749, 'unit': '件'}]
    assert len(screen['raw']) == 3
    current = client.get('/api/editor').get_json()['inventory']
    assert len(current['semi']) == 3
    assert current['semi'][0]['quantity'] == 2977
    assert current['semi'][0]['_audit']['created_at'] == first_audit['created_at']
    assert current['semi'][0]['_audit']['modified_at'] == clock['now'].isoformat()
    assert current['semi'][1]['_audit'] == inventory['semi'][1]['_audit']
    restarted = create_app(dict(app.config)).test_client()
    assert restarted.get('/api/dashboard').get_json()['inventory'] == screen
    for row in current['semi']:
        row['show_on_screen'] = False
    assert client.put('/api/inventory', json={'version': 2, **current}).status_code == 200
    hidden = client.get('/api/dashboard').get_json()['inventory']
    assert hidden['semi'] == hidden['semi_totals'] == []
    assert hidden['raw'] == screen['raw']
    current['semi'][0]['show_on_screen'] = True
    assert client.put('/api/inventory', json={'version': 3, **current}).status_code == 200
    assert client.get('/api/dashboard').get_json()['inventory']['semi_totals'] == [{'quantity': 2977, 'unit': '件'}]


def test_semi_old_payload_keeps_visibility_and_new_rows_default_visible(client):
    """验证旧载荷兼容；client 为登录客户端；断言隐藏配置保留且新增半成品默认显示。"""
    inventory = seed_inventory(client)['inventory']
    inventory['semi'][0]['show_on_screen'] = False
    assert client.put('/api/inventory', json={'version': 1, **inventory}).status_code == 200
    for row in inventory['semi']:
        row.pop('show_on_screen')
    inventory['semi'].append({'category': '其他', 'material': '新增件', 'warehouse': '半成品仓库',
                              'specification': '2.0', 'quantity': 0, 'unit': '件'})
    assert client.put('/api/inventory', json={'version': 2, **inventory}).status_code == 200
    current = client.get('/api/editor').get_json()['inventory']['semi']
    assert [row['show_on_screen'] for row in current] == [False, True, True, True]


@pytest.mark.parametrize('invalid', [None, 0, 1, 'false', [], {}])
def test_semi_invalid_visibility_rolls_back_inventory(client, invalid):
    """验证类型及原子性；client/invalid 为客户端与非法值；断言两类库存、人员记录和版本保持原值。"""
    original = seed_inventory(client)
    inventory = client.get('/api/editor').get_json()['inventory']
    inventory['raw'][0]['quantity'] = 99
    inventory['semi'][0]['show_on_screen'] = invalid
    response = client.put('/api/inventory', json={'version': 1, **inventory})
    assert response.status_code == 400
    assert response.get_json()['error'] == '大屏显示必须为布尔值'
    current = client.get('/api/editor').get_json()
    assert (current['version'], current['inventory']) == (original['version'], original['inventory'])


def test_semi_stale_save_preserves_visibility(client):
    """验证版本冲突；client 为登录客户端；断言过期请求不能覆盖已保存的开关。"""
    inventory = seed_inventory(client)['inventory']
    inventory['semi'][0]['show_on_screen'] = False
    assert client.put('/api/inventory', json={'version': 1, **inventory}).status_code == 200
    original = client.get('/api/editor').get_json()
    inventory['semi'][0]['show_on_screen'] = True
    assert client.put('/api/inventory', json={'version': 1, **inventory}).status_code == 409
    current = client.get('/api/editor').get_json()
    assert (current['version'], current['inventory']) == (original['version'], original['inventory'])


def test_semi_legacy_database_defaults_visible_and_keeps_hidden_on_restart(tmp_path, clock):
    """验证旧库迁移；tmp_path/clock 为隔离目录与时钟；断言原库存保留及重复启动不重置开关。"""
    path = tmp_path / 'legacy-semi.sqlite3'
    schema = (Path(__file__).resolve().parents[1] / 'backend/schema.sql').read_text(encoding='utf-8')
    legacy = schema.replace('    show_on_screen INTEGER NOT NULL DEFAULT 1 CHECK (show_on_screen IN (0, 1)),\n', '')
    with connect_database(path) as db:
        db.executescript(legacy)
        db.execute('PRAGMA user_version=6')
        db.execute("INSERT INTO semi_finished_stock(id,category,material,warehouse,specification,quantity,unit,sort_order) "
                   "VALUES(7,'左件','门槛左件','半成品仓库','1.4',2977,'件',0)")
    config = {'TESTING': True, 'DATABASE': str(path), 'NOW_PROVIDER': lambda: clock['now']}
    migrated = create_app(config).test_client()
    row = migrated.get('/api/dashboard').get_json()['inventory']['semi'][0]
    assert (row['id'], row['quantity'], row['show_on_screen']) == (7, 2977, True)
    with connect_database(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 10
        db.execute('UPDATE semi_finished_stock SET show_on_screen=0 WHERE id=7')
    restarted = create_app(config).test_client()
    assert restarted.get('/api/dashboard').get_json()['inventory']['semi'] == []
    with connect_database(path) as db:
        row = db.execute('SELECT * FROM semi_finished_stock WHERE id=7').fetchone()
        assert (row['quantity'], row['show_on_screen']) == (2977, 0)


def test_semi_visibility_editor_save_reload_and_import(ui_context, live_server, client):
    """验证真实编辑流程；参数为浏览器、服务及客户端；断言草稿、同步、导入、复制和新增默认值。"""
    seed_inventory(client)
    editor = ui_context.new_page()
    screen = ui_context.new_page()
    errors = []
    editor.on('pageerror', lambda error: errors.append(str(error)))
    screen.on('pageerror', lambda error: errors.append(str(error)))
    editor.goto(live_server + '/edit')
    expect(editor.get_by_test_id('save-button')).to_be_enabled()
    editor.get_by_role('tab', name='库存明细', exact=True).click()
    toggles = editor.get_by_role('switch', name='半成品大屏显示', exact=True)
    expect(toggles).to_have_count(3)
    for toggle in toggles.all():
        expect(toggle).to_be_checked()
    screen.goto(live_server + '/screen')
    expect(screen.locator('#semiTableBody tr')).to_have_count(3)
    editor.get_by_test_id('semi-visible-0').click()
    expect(editor.get_by_test_id('save-status')).to_contain_text('1')
    assert len(client.get('/api/dashboard').get_json()['inventory']['semi']) == 3
    editor.get_by_test_id('save-button').click()
    expect(editor.get_by_test_id('save-status')).to_have_text('所有修改已保存')
    expect(screen.locator('#semiTableBody tr')).to_have_count(2, timeout=10000)
    expect(screen.locator('#semiTableBody')).not_to_contain_text('门槛左件')
    expect(screen.locator('#semiTotal')).to_have_text('7,749（件）')
    editor.reload()
    expect(editor.get_by_test_id('save-button')).to_be_enabled()
    editor.get_by_role('tab', name='库存明细', exact=True).click()
    expect(toggles.nth(0)).not_to_be_checked()
    # 重复导入同一份日报不能恢复已隐藏半成品，后台合计仍包含全部库存。
    expect(editor.get_by_test_id('import-warehouse')).to_be_enabled()
    editor.get_by_label('仓库日报 Excel 文件', exact=True).set_input_files(warehouse_upload())
    dialog = editor.get_by_role('dialog', name='仓库日报导入预览', exact=True)
    expect(dialog).to_be_visible()
    editor.get_by_test_id('confirm-warehouse-import').click()
    expect(dialog).not_to_be_visible()
    expect(toggles.nth(0)).not_to_be_checked()
    assert client.get('/api/editor').get_json()['inventory']['semi'][0]['show_on_screen'] is False
    expect(editor.locator('.heading-with-total').filter(has=editor.get_by_role('heading', name='半成品库存'))).to_contain_text('10,726')
    Path('output/playwright').mkdir(parents=True, exist_ok=True)
    editor.get_by_test_id('semi-editor-table').scroll_into_view_if_needed()
    editor.screenshot(path='output/playwright/semi-inventory-visibility-editor.png', full_page=True)
    screen.screenshot(path='output/playwright/semi-inventory-visibility-screen.png')
    editor.get_by_test_id('semi-visible-0').click()
    editor.get_by_test_id('save-button').click()
    expect(editor.get_by_test_id('save-status')).to_have_text('所有修改已保存')
    expect(screen.locator('#semiTableBody tr')).to_have_count(3, timeout=10000)
    editor.get_by_test_id('semi-visible-0').click()
    editor.get_by_test_id('semi-editor-table').get_by_role('button', name='复制', exact=True).first.click()
    expect(toggles.nth(1)).not_to_be_checked()
    editor.get_by_role('button', name='新增半成品', exact=True).click()
    expect(toggles.nth(4)).to_be_checked()
    assert not errors, errors
