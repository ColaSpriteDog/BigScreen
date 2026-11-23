"""演示数据显式写入及原数据备份测试，使用临时 SQLite。"""

from datetime import timedelta

import pytest

from backend import create_app
from demo_data import apply_demo_data


def test_demo_data_populates_screen_and_keeps_previous_database_backup(client, app, clock):
    """验证完整演示数据；参数为隔离服务和时间；断言全模块数据、原版本备份及当前配置保留。"""
    original = client.get('/api/editor').get_json()
    config = {'version': original['version'], 'timezone': 'UTC', 'safety_start_date': '2026-01-01',
              'lines': original['production']['lines'],
              'processes': [node for node in original['processes'] if node['id'] in (1, 5)]}
    config['lines'][0]['name'] = '保留的产线名称'
    config['lines'][0]['show_in_trend'] = 1
    assert client.put('/api/settings', json=config).status_code == 200
    assert client.put('/api/sales/2026-11', json={'version': 1, 'order_qty': 500,
                                               'items': [{'part_no': 'ORIGINAL', 'quantity': 100}]}).status_code == 200
    result = apply_demo_data(app.config['DATABASE'], clock['now'])
    data = client.get('/api/dashboard').get_json()
    assert data['version'] == 3
    assert data['lines'][0]['name'] == '保留的产线名称'
    assert [item['line_id'] for item in data['trend']['series']] == [1, 3, 4]
    assert [item['actual_qty'] for item in data['lines']] == [1280, 960, 1420, 1360]
    assert data['lines'][3]['status'] == 'maintenance'
    assert data['trend']['reported_days'] == 21
    assert len(data['inventory']['raw']) == 8
    assert len(data['inventory']['semi']) == 10
    assert data['inventory']['raw_totals'] == [{'quantity': 41.5, 'unit': '卷'}]
    assert data['inventory']['semi_totals'] == [{'quantity': 29954, 'unit': '件'}]
    assert data['inventory']['zero_count'] == 2
    assert data['sales']['shipped_qty'] == 37400
    assert len(data['sales']['items']) == 8
    assert data['sales']['order_qty'] == 134490
    assert data['overview']['actual_qty'] == 6010
    backup_client = create_app({'TESTING': True, 'DATABASE': result['backup_path'], 'BUSINESS_TIMEZONE': 'UTC',
                                'NOW_PROVIDER': lambda: clock['now']}).test_client()
    restored = backup_client.get('/api/dashboard').get_json()
    assert restored['version'] == 2
    assert restored['sales']['order_qty'] == 500
    assert restored['sales']['items'][0]['part_no'] == 'ORIGINAL'
    assert restored['inventory']['raw'] == []


def test_demo_data_is_repeatable_without_duplicate_daily_rows(client, app, clock):
    """验证重复生成；参数为隔离服务和时间；断言重复执行覆盖同一周期、不累加假数量。"""
    first = apply_demo_data(app.config['DATABASE'], clock['now'])
    initial = client.get('/api/dashboard').get_json()
    second = apply_demo_data(app.config['DATABASE'], clock['now'] + timedelta(seconds=1))
    current = client.get('/api/dashboard').get_json()
    assert first['backup_path'] != second['backup_path']
    assert current['lines'][0]['monthly_qty'] == initial['lines'][0]['monthly_qty']
    assert current['sales']['shipped_qty'] == 37400
    assert len(client.get('/api/editor').get_json()['production']['downtimes']) == 1


def test_demo_data_refuses_missing_database(tmp_path, clock):
    """验证显式数据库边界；参数为临时目录与时间；断言不会偷偷创建并写入错误数据库。"""
    with pytest.raises(FileNotFoundError):
        apply_demo_data(str(tmp_path / 'missing.sqlite3'), clock['now'])
