"""验证点焊三条并行线的导入、保存、日月运行率及编辑输入口径。"""

from pathlib import Path

import pytest
from playwright.sync_api import expect

from backend.domain import ValidationError, line_availability
from tests.test_api import production_payload
from tests.test_browser import fill_control
from tests.test_production_import import preview


@pytest.mark.parametrize('planned,abnormal,expected', [
    (20.4, 27.75, 54.66), (20.4, 61.2, 0), (20, 60, 0), (20, None, None),
    (20, 0, 100), (0, 0, None),
])
def test_spot_welding_uses_three_parallel_plans(planned, abnormal, expected):
    """验证日运行率；参数为单线计划、三线停机及预期；断言总停机可超过单线计划，零空边界不变。"""
    assert line_availability(planned, abnormal, 2) == expected


def test_spot_welding_rejects_stops_above_all_three_plans():
    """验证三线总量上限；无参数；断言六十一点二小时以上的停机不能参与运行率计算。"""
    with pytest.raises(ValidationError):
        line_availability(20.4, 62, 2)


def test_real_november_19_import_preserves_total_and_recalculates_month(client, clock):
    """核对用户原表；参数为隔离客户端和时钟；断言十九日完整导入、重复不累加，月运行率按三线加权。"""
    source = next((Path(__file__).resolve().parents[1] / 'docs').glob('*11.25*.xlsx'), None)
    if source is None:
        pytest.skip('当前环境没有用户提供的十一月二十五日生产日报')
    clock['now'] = clock['now'].replace(day=25)
    with source.open('rb') as stream:
        response = preview(client, stream)
    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    item = next(item for item in data['items'] if item['line_id'] == 2 and item['date'] == '2026-11-19')
    assert item['planned_hours'] == 20.4 and item['abnormal_hours'] == 27.75
    assert item['actual_qty'] == 839 and not item['warnings']
    data['items'] = [item]
    assert client.post('/api/production/2026-11/import', json=data).status_code == 200
    # 每次保存使用最新版本，重复导入同一天不能累加设备工时。
    data['version'] = client.get('/api/editor').get_json()['version']
    assert client.post('/api/production/2026-11/import', json=data).status_code == 200
    saved = client.get('/api/editor?date=2026-11-19').get_json()['production']['lines'][1]
    assert saved['parallel_units'] == 3 and saved['planned_hours'] == 20.4
    assert saved['abnormal_hours'] == 27.75 and saved['runtime_rate'] == 54.66
    line = client.get('/api/dashboard').get_json()['lines'][1]
    assert line['monthly_planned_hours'] == pytest.approx(61.2)
    assert line['monthly_abnormal_hours'] == 27.75 and line['runtime_rate'] == 54.66
    draft = production_payload(client, '2026-11-20')
    draft['lines'][1].update(actual_qty=100, planned_hours=12, abnormal_hours=6)
    assert client.put('/api/production/2026-11-20', json=draft).status_code == 200
    line = client.get('/api/dashboard').get_json()['lines'][1]
    assert line['monthly_planned_hours'] == pytest.approx(97.2)
    assert line['monthly_abnormal_hours'] == 33.75 and line['runtime_rate'] == 65.28
    # 新增缺失停机的有效记录仍隐藏月运行率，不能借此次修正把未知值当零。
    draft = production_payload(client, '2026-11-21')
    draft['lines'][1].update(actual_qty=100, planned_hours=20.4, abnormal_hours=None)
    assert client.put('/api/production/2026-11-21', json=draft).status_code == 200
    assert client.get('/api/dashboard').get_json()['lines'][1]['runtime_rate'] is None


@pytest.mark.parametrize('abnormal,quantity,status', [(27.75, 839, 200), (62, 839, 400), (60, 839, 400), (60, 0, 200)])
def test_daily_save_validates_three_line_total(client, abnormal, quantity, status):
    """验证普通日报保存；参数为客户端、三线停机、产量及响应码；断言超单线可存、超三线或有产量全停不可存。"""
    draft = production_payload(client)
    draft['lines'][1].update(actual_qty=quantity, planned_hours=20, abnormal_hours=abnormal)
    assert client.put('/api/production/2026-11-21', json=draft).status_code == status


def test_browser_edits_spot_welding_total_above_single_plan(ui_context, live_server, client):
    """验证实际编辑保存；参数为登录浏览器、服务和客户端；断言输入不被单线计划截断且大屏显示正确百分比。"""
    draft = production_payload(client)
    draft['lines'][1].update(actual_qty=839, planned_hours=20.4, abnormal_hours=0)
    assert client.put('/api/production/2026-11-21', json=draft).status_code == 200
    page = ui_context.new_page()
    page.goto(live_server + '/edit')
    expect(page.get_by_test_id('save-button')).to_be_enabled()
    fill_control(page, 'abnormal-hours-2', 27.75)
    page.get_by_role('heading', level=1).click()
    expect(page.get_by_test_id('abnormal-feedback-2')).to_have_count(0)
    page.get_by_test_id('save-button').click()
    expect(page.get_by_test_id('save-status')).to_have_text('所有修改已保存')
    page.reload()
    expect(page.get_by_test_id('abnormal-hours-2').locator('input')).to_have_value('27.75')
    page.goto(live_server + '/screen')
    card = page.locator('.line-card[data-line-id="2"]')
    expect(card.locator('.data-list > div').last).to_have_text('运行状态（月）运行中 54.66%')
    expect(card.locator('.progress-inner')).to_have_attribute('style', 'width: 54.66%;')
