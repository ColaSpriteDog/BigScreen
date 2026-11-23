"""验证大屏昨日日报口径及跨日、跨月、时区边界，全部使用隔离数据库。"""

from datetime import datetime, timedelta

import pytest

from tests.test_api import production_payload, settings_payload


@pytest.mark.parametrize('instant,zone,today,yesterday', [
    ('2026-11-21T12:00:00+00:00', 'UTC', '2026-11-21', '2026-11-20'),
    ('2026-12-01T02:00:00+00:00', 'America/Detroit', '2026-11-30', '2026-11-29'),
    ('2027-01-01T05:00:00+00:00', 'America/Detroit', '2027-01-01', '2026-12-31'),
    ('2028-03-01T12:00:00+00:00', 'UTC', '2028-03-01', '2028-02-29'),
    ('2026-11-02T04:30:00+00:00', 'America/Detroit', '2026-11-01', '2026-10-31'),
])
def test_yesterday_metrics_follow_business_date_and_preserve_current_runtime(client, clock, instant, zone, today, yesterday):
    """验证昨日指标；参数为客户端、时钟和日期样例；断言取业务昨日、空值零值、当日运行及原始日报。"""
    clock['now'] = datetime.fromisoformat(instant)
    config = settings_payload(client)
    config['timezone'] = zone
    assert client.put('/api/settings', json=config).status_code == 200
    older = (datetime.fromisoformat(yesterday) - timedelta(days=1)).date().isoformat()
    draft = production_payload(client, older)
    for line in draft['lines']:
        line.update(actual_qty=500, defect_qty=5)
    assert client.put('/api/production/' + older, json=draft).status_code == 200
    draft = production_payload(client, yesterday)
    for line, actual, defect in zip(draft['lines'], [100.125, 0, None, 0], [0.25, 0, None, None]):
        line.update(actual_qty=actual, defect_qty=defect, planned_hours=8)
    assert client.put('/api/production/' + yesterday, json=draft).status_code == 200
    draft = production_payload(client, today)
    for line in draft['lines']:
        line.update(actual_qty=900, defect_qty=9, planned_hours=12)
    assert client.put('/api/production/' + today, json=draft).status_code == 200
    result = client.get('/api/dashboard').get_json()
    assert result['business_date'] == today and result['production_date'] == yesterday
    assert [line['yesterday_qty'] for line in result['lines']] == [100.125, 0, None, 0]
    assert [line['yesterday_defect_qty'] for line in result['lines']] == [0.25, 0, None, None]
    assert [line['yesterday_defect_rate'] for line in result['lines']] == [0.25, None, None, None]
    assert all(line['actual_qty'] == 900 and line['defect_qty'] == 9 and line['planned_hours'] == 12
               for line in result['lines'])
    original = production_payload(client, yesterday)
    assert original['lines'][0]['actual_qty'] == 100.125
    assert original['lines'][2]['actual_qty'] is None


def test_midnight_advances_yesterday_without_saving_again(client, clock):
    """验证零点自动换日；client、clock 为隔离环境；断言无新保存时昨日数据仍随业务日期更新。"""
    draft = production_payload(client)
    draft['lines'][0].update(actual_qty=120, defect_qty=3)
    assert client.put('/api/production/2026-11-21', json=draft).status_code == 200
    before = client.get('/api/dashboard').get_json()
    assert before['lines'][0]['yesterday_qty'] is None
    clock['now'] = datetime.fromisoformat('2026-11-22T00:00:00+00:00')
    after = client.get('/api/dashboard').get_json()
    assert after['version'] == before['version']
    assert after['production_date'] == '2026-11-21'
    assert after['lines'][0]['yesterday_qty'] == 120
    assert after['lines'][0]['yesterday_defect_qty'] == 3
    assert after['lines'][0]['actual_qty'] is None
