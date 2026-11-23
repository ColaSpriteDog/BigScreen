"""验证当月产量趋势的月份边界、业务时区与自动跨月，全部使用隔离数据库。"""

from datetime import datetime, timedelta

import pytest

from tests.test_api import production_payload, settings_payload


@pytest.mark.parametrize('instant,zone,day', [
    ('2026-02-28T12:00:00+00:00', 'UTC', '2026-02-28'),
    ('2028-02-29T12:00:00+00:00', 'UTC', '2028-02-29'),
    ('2026-03-31T12:00:00+00:00', 'UTC', '2026-03-31'),
    ('2026-12-01T02:00:00+00:00', 'America/Detroit', '2026-11-30'),
    ('2027-01-01T05:00:00+00:00', 'America/Detroit', '2027-01-01'),
    ('2026-11-02T04:30:00+00:00', 'America/Detroit', '2026-11-01'),
])
def test_trend_includes_only_business_month(client, clock, instant, zone, day):
    """验证月度取数；参数为客户端、时钟及时区样例；断言日期连续、排除上月及按月累计。"""
    clock['now'] = datetime.fromisoformat(instant)
    config = settings_payload(client)
    config['timezone'] = zone
    assert client.put('/api/settings', json=config).status_code == 200
    today = datetime.fromisoformat(day).date()
    first = today.replace(day=1)
    previous = (first - timedelta(days=1)).isoformat()
    historical = production_payload(client, previous)
    historical['lines'][3]['actual_qty'] = 900
    assert client.put('/api/production/' + previous, json=historical).status_code == 200
    # 月初和当天都纳入累计；两条所选产线完整录入，单独验证月份边界。
    for work_day, quantity in dict([(first.isoformat(), 25), (day, 100)]).items():
        draft = production_payload(client, work_day)
        draft['lines'][2]['actual_qty'] = quantity
        draft['lines'][3]['actual_qty'] = 0
        assert client.put('/api/production/' + work_day, json=draft).status_code == 200
    data = client.get('/api/dashboard').get_json()
    trend = data['trend']
    assert data['business_date'] == day
    assert trend['dates'] == [(first + timedelta(days=index)).isoformat() for index in range(today.day)]
    assert all(len(item['data']) == today.day for item in trend['series'])
    assert trend['totals'][-1] == 100
    assert trend['period_total'] == (100 if today.day == 1 else 125)
    assert trend['reported_days'] == (1 if today.day == 1 else 2)
    assert all(value in (None, 0) for value in trend['series'][1]['data'])
    assert trend['period_total'] == data['lines'][2]['monthly_qty']
    assert production_payload(client, previous)['lines'][3]['actual_qty'] == 900


def test_trend_resets_automatically_at_business_month_start(client, clock):
    """验证跨月刷新；client、clock 为隔离环境；断言不重新保存也会清空上月趋势累计。"""
    clock['now'] = datetime.fromisoformat('2026-12-31T23:59:00+00:00')
    draft = production_payload(client, '2026-12-31')
    draft['lines'][2]['actual_qty'] = 100
    draft['lines'][3]['actual_qty'] = 0
    assert client.put('/api/production/2026-12-31', json=draft).status_code == 200
    assert client.get('/api/dashboard').get_json()['trend']['period_total'] == 100
    clock['now'] += timedelta(minutes=1)
    trend = client.get('/api/dashboard').get_json()['trend']
    assert trend['dates'] == ['2027-01-01']
    assert all(value in (None, 0) for value in trend['totals'])
    assert trend['period_total'] in (None, 0)
    assert trend['reported_days'] == 0
