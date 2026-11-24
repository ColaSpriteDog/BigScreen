"""验证逐条原材料的大屏显示配置、兼容保存、人员记录与旧库升级。"""

from datetime import timedelta
from pathlib import Path

import pytest

from backend import create_app
from backend.db import connect_database
from tests.test_inventory_import import workbook_bytes


def seed_inventory(client):
    """初始化三条原材料与半成品；client 为登录客户端；返回保存后的编辑快照。"""
    inventory = client.post('/api/inventory/preview', data={'file': (workbook_bytes(), '日报.xlsx')}).get_json()
    assert client.put('/api/inventory', json={'version': 0, **inventory}).status_code == 200
    return client.get('/api/editor').get_json()


def test_visibility_filters_screen_and_totals_but_keeps_editor_records(client, clock, app):
    """验证默认与逐行开关；参数为客户端、时钟和应用；断言筛选口径、人员记录及重启持久化。"""
    original = seed_inventory(client)
    assert all(row['show_on_screen'] is True for row in original['inventory']['raw'])
    assert len(client.get('/api/dashboard').get_json()['inventory']['raw']) == 3
    original_audit = original['inventory']['raw'][0]['_audit'].copy()
    clock['now'] += timedelta(minutes=1)
    # 隐藏有重量的原材料和零库存原材料，检验汇总确实只取显示行。
    for row in original['inventory']['raw'][:2]:
        row['show_on_screen'] = False
    assert client.put('/api/inventory', json={'version': original['version'], **original['inventory']}).status_code == 200
    screen = client.get('/api/dashboard').get_json()['inventory']
    assert [row['position'] for row in screen['raw']] == ['试机料']
    assert screen['raw_totals'] == [{'quantity': 0.5, 'unit': '卷'}]
    assert screen['zero_count'] == 0
    assert screen['semi_totals'] == [{'quantity': 10726, 'unit': '件'}]
    current = client.get('/api/editor').get_json()['inventory']
    assert len(current['raw']) == 3
    changed_audit = current['raw'][0]['_audit']
    assert changed_audit['created_at'] == original_audit['created_at']
    assert changed_audit['modified_at'] == clock['now'].isoformat()
    assert current['raw'][2]['_audit'] == original['inventory']['raw'][2]['_audit']
    restarted = create_app({'TESTING': True, 'DATABASE': app.config['DATABASE'],
                            'NOW_PROVIDER': lambda: clock['now']}).test_client()
    assert restarted.get('/api/dashboard').get_json()['inventory'] == screen
    # 全部关闭时保持明确空表，半成品明细仍完整。
    for row in current['raw']:
        row['show_on_screen'] = False
    assert client.put('/api/inventory', json={'version': 2, **current}).status_code == 200
    hidden = client.get('/api/dashboard').get_json()['inventory']
    assert hidden['raw'] == hidden['raw_totals'] == []
    assert hidden['zero_count'] == 0 and len(hidden['semi']) == 3
    current['raw'][0]['show_on_screen'] = True
    assert client.put('/api/inventory', json={'version': 3, **current}).status_code == 200
    assert len(client.get('/api/dashboard').get_json()['inventory']['raw']) == 1


def test_omitted_visibility_preserves_existing_setting(client):
    """验证未传开关的旧客户端；client 为登录客户端；断言已有隐藏项不会被普通保存重置。"""
    data = seed_inventory(client)
    data['inventory']['raw'][0]['show_on_screen'] = False
    assert client.put('/api/inventory', json={'version': 1, **data['inventory']}).status_code == 200
    for row in data['inventory']['raw']:
        row.pop('show_on_screen')
    assert client.put('/api/inventory', json={'version': 2, **data['inventory']}).status_code == 200
    current = client.get('/api/editor').get_json()['inventory']['raw']
    assert [row['show_on_screen'] for row in current] == [False, True, True]


@pytest.mark.parametrize('invalid', [None, 0, 1, 'false', [], {}])
def test_invalid_visibility_rejects_entire_save(client, invalid):
    """验证开关类型；client 为登录客户端、invalid 为非法值；断言失败不改变库存、版本及人员记录。"""
    data = seed_inventory(client)
    data['inventory']['raw'][0]['show_on_screen'] = invalid
    before = client.get('/api/editor').get_json()
    result = client.put('/api/inventory', json={'version': 1, **data['inventory']})
    assert result.status_code == 400
    assert result.get_json()['error'] == '大屏显示必须为布尔值'
    after = client.get('/api/editor').get_json()
    assert (after['version'], after['inventory']) == (before['version'], before['inventory'])


def test_legacy_database_defaults_existing_materials_to_visible(tmp_path, clock):
    """验证旧库升级；tmp_path 为隔离目录、clock 为时钟；断言默认显示且重复启动保留隐藏配置。"""
    path = tmp_path / 'legacy.sqlite3'
    schema = (Path(__file__).resolve().parents[1] / 'backend/schema.sql').read_text(encoding='utf-8')
    legacy_schema = schema.replace('    show_on_screen INTEGER NOT NULL DEFAULT 1 CHECK (show_on_screen IN (0, 1)),\n', '')
    with connect_database(path) as db:
        db.executescript(legacy_schema)
        db.execute("INSERT INTO raw_material_stock(id,material,warehouse,specification,position,quantity,unit,sort_order) "
                   "VALUES(7,'钢卷','原材料仓库','旧规格','A柱',2.5,'卷',0)")
    config = {'TESTING': True, 'DATABASE': str(path), 'NOW_PROVIDER': lambda: clock['now']}
    migrated = create_app(config).test_client()
    row = migrated.get('/api/dashboard').get_json()['inventory']['raw'][0]
    assert (row['id'], row['quantity'], row['show_on_screen']) == (7, 2.5, True)
    with connect_database(path) as db:
        db.execute('UPDATE raw_material_stock SET show_on_screen=0 WHERE id=7')
    restarted = create_app(config).test_client()
    assert restarted.get('/api/dashboard').get_json()['inventory']['raw'] == []
    with connect_database(path) as db:
        row = db.execute('SELECT * FROM raw_material_stock WHERE id=7').fetchone()
        assert (row['quantity'], row['show_on_screen']) == (2.5, 0)
