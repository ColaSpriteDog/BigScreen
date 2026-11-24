"""验证运行状态百分比按本月已录入日报的累计工时计算。"""

from datetime import datetime

import pytest
from playwright.sync_api import expect

from tests.test_api import production_payload, settings_payload


def test_monthly_runtime_includes_today_and_backfills_without_counting_empty_rows(client):
    """验证本月累计；client 为隔离客户端；断言包含今天、零产量与纯工时，排除空行及上月，不平均日比例。"""
    for day, values in [
        ('2026-10-31', dict(actual_qty=0, planned_hours=24, abnormal_hours=24)),
        ('2026-11-01', dict(actual_qty=0, planned_hours=20, abnormal_hours=10)),
        ('2026-11-20', dict(actual_qty=None, planned_hours=10, abnormal_hours=1)),
        ('2026-11-21', dict(actual_qty=100, planned_hours=5, abnormal_hours=0)),
    ]:
        draft = production_payload(client, day)
        draft['lines'][0].update(values)
        assert client.put('/api/production/' + day, json=draft).status_code == 200
    dashboard = client.get('/api/dashboard').get_json()
    line = dashboard['lines'][0]
    assert line['monthly_planned_hours'] == 35
    assert line['monthly_abnormal_hours'] == 11
    assert line['runtime_rate'] == 68.57
    assert line['planned_hours'] == 5 and line['abnormal_hours'] == 0
    assert line['status'] == 'running'
    # 保存某条产线时自动生成的其他产线空行，不算有效月度数据。
    assert all(item['monthly_planned_hours'] is None and item['runtime_rate'] is None
               for item in dashboard['lines'][1:])
    history = production_payload(client, '2026-11-01')['lines'][0]
    assert history['runtime_rate'] == 50


@pytest.mark.parametrize('planned,abnormal,quantity,expected', [
    (12, None, 10, None), (12, 0, 10, 90), (0, None, 0, 50),
])
def test_monthly_runtime_preserves_missing_and_zero_hours(client, planned, abnormal, quantity, expected):
    """验证工时边界；参数为客户端及日报工时、产量和预期；断言缺停机不冒充零，无计划不稀释累计。"""
    draft = production_payload(client, '2026-11-20')
    draft['lines'][0].update(actual_qty=0, planned_hours=3, abnormal_hours=1.5)
    assert client.put('/api/production/2026-11-20', json=draft).status_code == 200
    draft = production_payload(client)
    draft['lines'][0].update(actual_qty=quantity, planned_hours=planned, abnormal_hours=abnormal)
    assert client.put('/api/production/2026-11-21', json=draft).status_code == 200
    line = client.get('/api/dashboard').get_json()['lines'][0]
    assert line['monthly_planned_hours'] == 3 + planned
    assert line['runtime_rate'] == expected


def test_monthly_runtime_rolls_over_in_business_timezone(client, clock):
    """验证业务月边界；client、clock 为客户端和时钟；断言按配置时区切月，旧月数据不延续到新月。"""
    clock['now'] = datetime.fromisoformat('2026-12-01T02:00:00+00:00')
    draft = production_payload(client, '2026-11-30')
    draft['lines'][0].update(planned_hours=8, abnormal_hours=2)
    assert client.put('/api/production/2026-11-30', json=draft).status_code == 200
    config = settings_payload(client)
    config['timezone'] = 'America/Detroit'
    assert client.put('/api/settings', json=config).status_code == 200
    november = client.get('/api/dashboard').get_json()
    assert november['business_date'] == '2026-11-30'
    assert november['lines'][0]['runtime_rate'] == 75
    clock['now'] = datetime.fromisoformat('2026-12-01T05:00:00+00:00')
    december = client.get('/api/dashboard').get_json()
    assert december['business_date'] == '2026-12-01'
    assert december['lines'][0]['runtime_rate'] is None
    assert december['lines'][0]['monthly_planned_hours'] is None


@pytest.mark.parametrize('width,height', [(1440, 900), (375, 812)])
def test_browser_runtime_keeps_label_and_shows_monthly_hours(ui_context, live_server, client, width, height):
    """验证月累计展示；参数为隔离浏览器、服务、客户端和尺寸；断言名称保留、百分比与提示均用月工时。"""
    for day, planned, abnormal in [('2026-11-20', 8, 4), ('2026-11-21', 12, 1)]:
        draft = production_payload(client, day)
        draft['lines'][0].update(planned_hours=planned, abnormal_hours=abnormal)
        assert client.put('/api/production/' + day, json=draft).status_code == 200
    page = ui_context.new_page()
    page.set_viewport_size({'width': width, 'height': height})
    page.goto(live_server + '/screen')
    card = page.locator('.line-card[data-line-id="1"]')
    state = card.locator('.data-list > div').last
    expect(state).to_have_text('运行状态（月）运行中 75%')
    expect(state).to_have_attribute('title', '本月累计计划生产 20（小时）；异常停机 5（小时）；运行率按本月已录入数据的累计工时计算，缺少异常停机工时或无生产计划时不显示百分比')
    expect(card.locator('.progress-inner')).to_have_attribute('style', 'width: 75%;')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
