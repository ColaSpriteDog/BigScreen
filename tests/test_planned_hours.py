"""验证按日期、产线保存的计划生产时长及固定分母运行率。"""

from datetime import datetime, timedelta, timezone

import pytest

from backend import create_app
from backend.db import connect_database
from backend.domain import line_runtime
from tests.test_api import production_payload
from tests.test_audit import worker_client


@pytest.mark.parametrize('hours,expected', [(24, 95.83), (12, 91.67), (7.5, 86.67), (0.5, 0), (0, None)])
def test_fixed_plan_denominator_and_zero_floor(hours, expected):
    """验证固定分母；hours 为计划小时、expected 为独立预期；断言一小时停机按计划计算且不低于零。"""
    stops = [{'start_at': '2026-11-21T09:00:00+00:00', 'end_at': '2026-11-21T10:00:00+00:00', 'kind': 'stopped'}]
    for hour in (10, 12, 23):
        result = line_runtime(datetime(2026, 11, 21, hour, tzinfo=timezone.utc), 'UTC', stops, hours)
        assert result['runtime_rate'] == expected
        assert result['downtime_minutes'] == 60


def test_historical_day_uses_whole_day_and_deduplicates_cross_day_stops():
    """验证历史日裁剪；无参数；断言跨天未结束和重叠停机只统计所选日期内实际时长。"""
    stops = [{'start_at': '2026-11-20T23:00:00+00:00', 'end_at': '2026-11-21T01:00:00+00:00', 'kind': 'stopped'},
             {'start_at': '2026-11-21T00:30:00+00:00', 'end_at': '2026-11-21T02:00:00+00:00', 'kind': 'fault'},
             {'start_at': '2026-11-21T23:00:00+00:00', 'end_at': None, 'kind': 'maintenance'}]
    result = line_runtime(datetime(2026, 11, 22, 12, tzinfo=timezone.utc), 'UTC', stops, 12, '2026-11-21')
    assert result['downtime_minutes'] == 180
    assert result['runtime_rate'] == 75


def test_each_date_and_line_persists_plan_and_updates_only_its_actor(client, app, clock):
    """验证时长持久化与归属；客户端、应用和时钟为参数；断言日期产线独立、零小数有效、只更新修改行。"""
    worker = worker_client(client, app)
    draft = production_payload(client)
    assert all(line['planned_hours'] == 24 for line in draft['lines'])
    for line, hours in zip(draft['lines'], [12, 7.5, 0, 24]):
        line['planned_hours'] = hours
    draft['downtimes'] = [{'line_id': 1, 'start_at': '2026-11-21T09:00:00', 'end_at': '2026-11-21T10:00:00', 'kind': 'stopped'}]
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


def test_historical_editor_rate_uses_selected_plan_and_stops(client, clock):
    """验证历史日报展示；client、clock 为客户端及时钟；断言历史运行率不混入今天的时长或停机。"""
    yesterday = production_payload(client, '2026-11-20')
    yesterday['lines'][0]['planned_hours'] = 12
    yesterday['downtimes'] = [{'line_id': 1, 'start_at': '2026-11-20T20:00:00', 'end_at': '2026-11-20T21:00:00', 'kind': 'stopped'}]
    assert client.put('/api/production/2026-11-20', json=yesterday).status_code == 200
    today = production_payload(client)
    today['lines'][0]['planned_hours'] = 6
    assert client.put('/api/production/2026-11-21', json=today).status_code == 200
    history = client.get('/api/editor?date=2026-11-20').get_json()['production']['lines'][0]
    current = client.get('/api/dashboard').get_json()['lines'][0]
    assert history['runtime_rate'] == 91.67 and history['downtime_minutes'] == 60 and history['planned_hours'] == 12
    assert current['runtime_rate'] == 100 and current['planned_hours'] == 6


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
        assert db.execute('PRAGMA user_version').fetchone()[0] == 4
