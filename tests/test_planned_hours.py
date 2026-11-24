"""验证按日期、产线保存的计划生产时长及固定分母运行率。"""

from datetime import timedelta

import pytest

from backend import create_app
from backend.db import connect_database
from backend.domain import line_availability
from tests.test_api import production_payload
from tests.test_audit import worker_client


@pytest.mark.parametrize('hours,expected', [(24, 95.83), (12, 91.67), (7.5, 86.67), (0.5, 0), (0, None)])
def test_fixed_plan_denominator_and_zero_floor(hours, expected):
    """验证日报时间开动率；hours/expected 为计划和预期；断言分母来自计划，全停机为零、无计划为空。"""
    assert line_availability(hours, min(1, hours)) == expected





def test_each_date_and_line_persists_plan_and_updates_only_its_actor(client, app, clock):
    """验证时长持久化与归属；客户端、应用和时钟为参数；断言日期产线独立、零小数有效、只更新修改行。"""
    worker = worker_client(client, app)
    draft = production_payload(client)
    assert all(line['planned_hours'] == 24 for line in draft['lines'])
    for line, hours in zip(draft['lines'], [12, 7.5, 0, 24]):
        line['planned_hours'] = hours
    draft['lines'][0]['abnormal_hours'] = 1
    assert client.put('/api/production/2026-11-21', json=draft).status_code == 200
    lines = client.get('/api/dashboard').get_json()['lines']
    assert [line['planned_hours'] for line in lines] == [12, 7.5, 0, 24]
    assert lines[0]['runtime_rate'] == 91.67 and lines[2]['runtime_rate'] is None
    original = client.get('/api/editor').get_json()['production']['lines']
    clock['now'] += timedelta(minutes=5)
    draft = production_payload(worker)
    draft['lines'][0]['planned_hours'] = 8
    assert worker.put('/api/production/2026-11-21', json=draft).status_code == 200
    fresh = client.get('/api/editor').get_json()['production']['lines']
    assert fresh[0]['_audit']['created_by_name'] == '管理员'
    assert fresh[0]['_audit']['modified_by_name'] == '张三'
    assert fresh[1]['_audit'] == original[1]['_audit']
    assert fresh[3]['_audit']['state'] == 'unsaved'
    assert worker.put('/api/production/2026-11-21', json=production_payload(worker)).status_code == 200
    assert client.get('/api/editor').get_json()['production']['lines'][0]['_audit'] == fresh[0]['_audit']
    previous = production_payload(client, '2026-11-20')
    assert all(line['planned_hours'] == 24 for line in previous['lines'])
    previous['lines'][0]['planned_hours'] = 6
    assert client.put('/api/production/2026-11-20', json=previous).status_code == 200
    assert client.get('/api/dashboard').get_json()['lines'][0]['planned_hours'] == 8
    restarted = create_app(dict(app.config)).test_client()
    assert restarted.get('/api/dashboard').get_json()['lines'][0]['planned_hours'] == 8


@pytest.mark.parametrize('invalid', [None, '', -1, 24.01, True, '12', [], {}, float('inf'), float('nan')])
def test_invalid_plan_rolls_back_entire_daily_batch(client, invalid):
    """验证时长边界及回滚；client 为客户端、invalid 为非法输入；断言产量、计划及版本均不部分写入。"""
    draft = production_payload(client)
    draft['lines'][0].update(actual_qty=100, planned_hours=12)
    draft['lines'][3]['planned_hours'] = invalid
    assert client.put('/api/production/2026-11-21', json=draft).status_code == 400
    data = client.get('/api/editor').get_json()
    assert data['version'] == 0
    assert all(line['planned_hours'] == 24 and line['actual_qty'] is None for line in data['production']['lines'])


def test_missing_field_in_old_client_preserves_existing_hours(client):
    """验证旧客户端兼容；client 为客户端；断言未提交时保留原计划，新日期默认为二十四小时。"""
    draft = production_payload(client)
    draft['lines'][0]['planned_hours'] = 12
    assert client.put('/api/production/2026-11-21', json=draft).status_code == 200
    draft = production_payload(client)
    for line in draft['lines']:
        line.pop('planned_hours')
    assert client.put('/api/production/2026-11-21', json=draft).status_code == 200
    assert client.get('/api/dashboard').get_json()['lines'][0]['planned_hours'] == 12
    yesterday = production_payload(client, '2026-11-20')
    for line in yesterday['lines']:
        line.pop('planned_hours')
    assert client.put('/api/production/2026-11-20', json=yesterday).status_code == 200
    history = client.get('/api/editor?date=2026-11-20').get_json()['production']['lines']
    assert all(line['planned_hours'] == 24 for line in history)


def test_historical_editor_rate_uses_selected_daily_hours(client, clock):
    """验证历史日报展示；client、clock 为客户端及时钟；断言历史时间开动率不混入今天的日报工时。"""
    yesterday = production_payload(client, '2026-11-20')
    yesterday['lines'][0]['planned_hours'] = 12
    yesterday['lines'][0]['abnormal_hours'] = 1
    assert client.put('/api/production/2026-11-20', json=yesterday).status_code == 200
    today = production_payload(client)
    today['lines'][0].update(planned_hours=6, abnormal_hours=0)
    assert client.put('/api/production/2026-11-21', json=today).status_code == 200
    history = client.get('/api/editor?date=2026-11-20').get_json()['production']['lines'][0]
    current = client.get('/api/dashboard').get_json()['lines'][0]
    assert history['runtime_rate'] == 91.67 and history['abnormal_hours'] == 1 and history['planned_hours'] == 12
    assert current['runtime_rate'] == 94.44 and current['planned_hours'] == 6
    assert current['monthly_planned_hours'] == 18 and current['monthly_abnormal_hours'] == 1


def test_legacy_database_migration_adds_default_without_changing_data_or_actor(client, app):
    """验证旧库升级；客户端及应用为参数；断言新增列默认为二十四小时且原产量、停机、归属和版本不变。"""
    draft = production_payload(client)
    draft['lines'][0]['actual_qty'] = 123
    assert client.put('/api/production/2026-11-21', json=draft).status_code == 200
    original = client.get('/api/editor').get_json()
    with connect_database(app.config['DATABASE']) as db:
        db.execute('ALTER TABLE production_daily DROP COLUMN planned_hours')
        db.execute('PRAGMA user_version=3')
    upgraded = create_app(dict(app.config)).test_client()
    upgraded.post('/api/auth/login', json={'username': 'admin', 'password': 'admin123'})
    restored = upgraded.get('/api/editor').get_json()
    assert restored == original
    with connect_database(app.config['DATABASE']) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 10
