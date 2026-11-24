"""月计划优先级、按月隔离及真实编辑保存链路测试，使用隔离数据库。"""

from datetime import datetime, timezone
from pathlib import Path

import pytest
from playwright.sync_api import expect

from backend import create_app
from backend.db import connect_database
from tests.test_api import production_payload, settings_payload
from tests.test_monthly_overview import save_overview, set_standards


def save_plan(client, value, month='2026-11'):
    """保存月计划；client 为客户端、value 为数量或空值、month 为月份；返回保存响应。"""
    data = client.get('/api/editor').get_json()
    draft = dict(data['production'], version=data['version'])
    draft['monthly_plan'] = {'month': month, 'planned_qty': value}
    return client.put('/api/production/' + data['date'], json=draft)


def test_monthly_plan_overrides_and_clearing_restores_daily_total(client):
    """验证优先级；client 为隔离客户端；断言设置、清空和显式零对应正确计划及达成率。"""
    set_standards(client, first=1000)
    save_overview(client, '2026-11-01', first=24528, first_defects=0)
    original = client.get('/api/dashboard').get_json()['overview']
    assert original['planned_qty'] == 1000
    assert save_plan(client, 28000).status_code == 200
    overview = client.get('/api/dashboard').get_json()['overview']
    assert overview['planned_qty'] == 28000
    assert overview['completion_rate'] == 87.6
    # 月计划仅替换大屏月计划和达成率，不影响其他指标和按日编辑的计划。
    for field in ('actual_qty', 'quality_percent', 'average_output', 'oee_percent'):
        assert overview[field] == original[field]
    assert production_payload(client, '2026-11-01')['overview']['planned_qty'] == 1000
    assert save_plan(client, 0).status_code == 200
    overview = client.get('/api/dashboard').get_json()['overview']
    assert overview['planned_qty'] == 0 and overview['completion_rate'] is None
    assert save_plan(client, None).status_code == 200
    assert client.get('/api/dashboard').get_json()['overview'] == original


def test_monthly_plan_without_daily_data_and_across_months(client, clock):
    """验证月边界；client/clock 为客户端和时钟；断言无日报仍显示计划且跨月不沿用。"""
    assert save_plan(client, 28000).status_code == 200
    overview = client.get('/api/dashboard').get_json()['overview']
    assert overview['planned_qty'] == 28000 and overview['completion_rate'] is None
    # 销售月份不干扰生产月计划；生产月份可以单独选择。
    plan = client.get('/api/editor?month=2026-10').get_json()['production']['monthly_plan']
    assert plan['month'] == '2026-11' and plan['planned_qty'] == 28000
    selected = client.get('/api/editor?production_month=2026-10').get_json()['production']['monthly_plan']
    assert selected['month'] == '2026-10' and selected['planned_qty'] is None
    assert client.get('/api/editor?production_month=2026-13').status_code == 400
    clock['now'] = datetime(2026, 12, 1, 12, tzinfo=timezone.utc)
    assert client.get('/api/dashboard').get_json()['overview']['planned_qty'] is None
    assert client.get('/api/editor').get_json()['production']['monthly_plan']['planned_qty'] is None
    assert save_plan(client, 31000, month='2026-12').status_code == 200
    assert client.get('/api/dashboard').get_json()['overview']['planned_qty'] == 31000
    clock['now'] = datetime(2026, 11, 21, 12, tzinfo=timezone.utc)
    assert client.get('/api/dashboard').get_json()['overview']['planned_qty'] == 28000


def test_monthly_plan_persists_and_legacy_settings_preserve_it(client, app, clock):
    """验证持久化及人员记录；参数为客户端、应用、时钟；断言重启和旧载荷保留月计划。"""
    assert save_plan(client, 28000).status_code == 200
    plan = client.get('/api/editor').get_json()['production']['monthly_plan']
    assert plan['_audit']['modified_by_name'] == '管理员'
    assert client.put('/api/settings', json=settings_payload(client)).status_code == 200
    assert client.get('/api/editor').get_json()['production']['monthly_plan'] == plan
    # 不含月计划的旧日报请求不清空已有计划。
    draft = production_payload(client)
    draft.pop('monthly_plan')
    assert client.put('/api/production/2026-11-21', json=draft).status_code == 200
    assert client.get('/api/editor').get_json()['production']['monthly_plan'] == plan
    restarted = create_app({'TESTING': True, 'DATABASE': app.config['DATABASE'],
                            'BUSINESS_TIMEZONE': 'UTC', 'NOW_PROVIDER': lambda: clock['now']})
    assert restarted.test_client().get('/api/dashboard').get_json()['overview']['planned_qty'] == 28000


def test_existing_database_adds_monthly_plan_without_changing_daily_data(client, app):
    """验证旧库升级；client/app 为隔离客户端和应用；断言补建月计划表且保留原日报。"""
    set_standards(client, first=1000)
    save_overview(client, '2026-11-01', first=200)
    original = client.get('/api/dashboard').get_json()
    # 仅在临时测试库中模拟尚无月计划表的旧版本。
    with connect_database(app.config['DATABASE']) as db:
        db.execute('DROP TABLE production_month_plan')
        db.execute('PRAGMA user_version=5')
    upgraded = create_app(dict(app.config)).test_client()
    assert upgraded.get('/api/dashboard').get_json() == original
    with connect_database(app.config['DATABASE']) as db:
        assert db.execute('SELECT COUNT(*) FROM production_month_plan').fetchone()[0] == 0
        assert db.execute('PRAGMA user_version').fetchone()[0] == 7


@pytest.mark.parametrize('value', [-1, 1.5, True, '28000', 1000000000001])
def test_invalid_monthly_plan_is_rejected_atomically(client, value):
    """验证输入边界；client/value 为客户端与非法数量；断言失败不改变配置和版本。"""
    assert save_plan(client, 28000).status_code == 200
    original = client.get('/api/editor').get_json()
    assert save_plan(client, value).status_code == 400
    current = client.get('/api/editor').get_json()
    assert current['version'] == original['version']
    assert current['production']['monthly_plan'] == original['production']['monthly_plan']


def test_monthly_plan_bad_month_conflict_and_production_failure_roll_back(client):
    """验证事务边界；client 为客户端；断言非法月份、版本冲突和后续日报校验失败均回滚。"""
    assert save_plan(client, 28000).status_code == 200
    original = client.get('/api/editor').get_json()
    assert save_plan(client, 31000, month='2026-13').status_code == 400
    stale = production_payload(client)
    stale.update(version=0, monthly_plan={'month': '2026-11', 'planned_qty': 31000})
    assert client.put('/api/production/2026-11-21', json=stale).status_code == 409
    draft = production_payload(client)
    draft['monthly_plan'] = {'month': '2026-11', 'planned_qty': 31000}
    draft['lines'][0]['actual_qty'] = -1
    assert client.put('/api/production/2026-11-21', json=draft).status_code == 400
    current = client.get('/api/editor').get_json()
    assert current['production']['monthly_plan'] == original['production']['monthly_plan']
    assert current['version'] == original['version']


def test_monthly_plan_editor_save_reload_and_clear(ui_context, live_server, client):
    """验证真实编辑链路；参数为浏览器、服务和客户端；断言填写、刷新持久化及清空回退。"""
    set_standards(client, first=1000)
    save_overview(client, '2026-11-01', first=24528, first_defects=0)
    editor = ui_context.new_page()
    screen = ui_context.new_page()
    errors = []
    editor.on('pageerror', lambda error: errors.append(str(error)))
    screen.on('pageerror', lambda error: errors.append(str(error)))
    editor.goto(live_server + '/edit')
    screen.goto(live_server + '/screen')
    field = editor.get_by_role('spinbutton', name='月计划产量', exact=True)
    expect(field).to_have_value('')
    field.fill('28000')
    field.blur()
    editor.get_by_test_id('save-button').click()
    expect(screen.locator('#plannedQty')).to_have_text('28,000（件）')
    expect(screen.locator('#completionRate')).to_have_text('87.6%')
    editor.reload()
    expect(field).to_have_value('28000')
    editor.get_by_role('tab', name='流程与设置', exact=True).click()
    expect(editor.get_by_test_id('settings-panel').get_by_test_id('monthly-plan')).to_have_count(0)
    editor.get_by_role('tab', name='生产日报', exact=True).click()
    Path('output/playwright').mkdir(parents=True, exist_ok=True)
    editor.screenshot(path='output/playwright/editor-monthly-plan.png', full_page=True)
    field.fill('')
    field.blur()
    editor.get_by_test_id('save-button').click()
    expect(screen.locator('#plannedQty')).to_have_text('1,000（件）')
    expect(screen.locator('#completionRate')).to_have_text('2,452.8%')
    editor.reload()
    expect(field).to_have_value('')
    assert not errors, errors


def select_production_month(page, month):
    """选择生产月份；page 为编辑页、month 为 YYYY-MM；无返回值，由调用方等待读取或草稿确认。"""
    control = page.get_by_label('生产月份', exact=True)
    control.fill(month)
    control.press('Enter')


@pytest.mark.parametrize('width', [1440, 375])
def test_production_month_switch_preserves_saved_plans_and_protects_drafts(ui_context, live_server, client, width):
    """验证多月编辑；参数为隔离环境及视口宽度；断言按月读取、保存和草稿保护，窄屏无横向溢出。"""
    assert save_plan(client, 28000).status_code == 200
    assert save_plan(client, 26000, month='2026-10').status_code == 200
    page = ui_context.new_page()
    page.set_viewport_size({'width': width, 'height': 812})
    page.goto(live_server + '/edit')
    field = page.get_by_role('spinbutton', name='月计划产量', exact=True)
    expect(field).to_have_value('28000')
    select_production_month(page, '2026-10')
    expect(field).to_have_value('26000')
    field.fill('27000')
    field.blur()
    select_production_month(page, '2026-11')
    dialog = page.get_by_role('dialog', name='未保存的修改')
    expect(dialog).to_be_visible()
    dialog.get_by_role('button', name='继续编辑', exact=True).click()
    expect(dialog).not_to_be_visible()
    expect(page.get_by_label('生产月份', exact=True)).to_have_value('2026-10')
    expect(field).to_have_value('27000')
    page.get_by_test_id('save-button').click()
    expect(page.get_by_test_id('save-status')).to_have_text('所有修改已保存')
    assert client.get('/api/editor?production_month=2026-10').get_json()['production']['monthly_plan']['planned_qty'] == 27000
    assert client.get('/api/dashboard').get_json()['overview']['planned_qty'] == 28000
    select_production_month(page, '2026-11')
    expect(field).to_have_value('28000')
    field.fill('29000')
    field.blur()
    select_production_month(page, '2026-10')
    dialog.get_by_role('button', name='放弃修改', exact=True).click()
    expect(field).to_have_value('27000')
    select_production_month(page, '2026-11')
    expect(field).to_have_value('28000')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    Path('output/playwright').mkdir(parents=True, exist_ok=True)
    page.screenshot(path=f'output/playwright/production-monthly-plan-{width}.png', full_page=True)
