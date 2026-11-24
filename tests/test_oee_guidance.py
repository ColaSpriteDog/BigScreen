"""验证 OEE 填写引导、矛盾拦截及跨日字段定位，全部使用隔离数据库。"""

from pathlib import Path

import pytest
from playwright.sync_api import expect

from tests.test_api import production_payload
from tests.test_oee import configure_capacity
from tests.test_production_import import production_workbook, preview


@pytest.mark.parametrize('planned,abnormal', [(20.4, 20.4), (0, None)])
def test_conflicting_hours_reject_whole_daily_save(client, planned, abnormal):
    """验证原子校验；client/planned/abnormal 为客户端及工时；断言矛盾被拒绝且其他行不落库。"""
    draft = production_payload(client)
    draft['lines'][0]['actual_qty'] = 100
    draft['lines'][3].update(actual_qty=510, defect_qty=0, planned_hours=planned, abnormal_hours=abnormal)
    result = client.put('/api/production/2026-11-21', json=draft)
    assert result.status_code == 400
    assert '已有产量' in result.get_json()['error']
    fresh = client.get('/api/editor').get_json()
    assert fresh['version'] == 0
    assert all(line['actual_qty'] is None for line in fresh['production']['lines'])


def test_import_conflict_is_rejected_without_partial_write(client):
    """验证导入入口；client 为客户端；断言全停机却有产量的文件提交整批回滚。"""
    data = preview(client, production_workbook()).get_json()
    line = next(item for item in data['items'] if item['line_id'] == 3)
    line['abnormal_hours'] = line['planned_hours']
    result = client.post('/api/production/2026-11/import', json=data)
    assert result.status_code == 400
    assert '异常停机工时必须小于计划工时' in result.get_json()['error']
    assert client.get('/api/editor').get_json()['version'] == 0


def test_monthly_issues_keep_unknowns_and_locate_only_selected_month(client):
    """验证诊断范围；client 为客户端；断言未知值不补零、历史日期可定位且其他月份不混入。"""
    for day in ('2026-10-31', '2026-11-20'):
        draft = production_payload(client, day)
        draft['lines'][3].update(actual_qty=510, defect_qty=None, planned_hours=20.4, abnormal_hours=None)
        assert client.put('/api/production/' + day, json=draft).status_code == 200
    snapshot = client.get('/api/editor?date=2026-11-21').get_json()
    issues = snapshot['oee_issues']
    assert {issue['date'] for issue in issues} == {'2026-11-20'}
    assert {issue['field'] for issue in issues} == {'abnormal_hours', 'defect_qty', 'theoretical_max_per_hour'}
    assert all(issue['severity'] == 'warning' and issue['line_id'] == 4 for issue in issues)
    saved = client.get('/api/editor?date=2026-11-20').get_json()['production']['lines'][3]
    assert saved['abnormal_hours'] is None and saved['defect_qty'] is None
    configure_capacity(client, 60)
    draft = production_payload(client, '2026-11-20')
    draft['lines'][3].update(actual_qty=0, defect_qty=0, abnormal_hours=20.4)
    assert client.put('/api/production/2026-11-20', json=draft).status_code == 200
    assert client.get('/api/editor').get_json()['oee_issues'] == []
    assert client.get('/api/dashboard').get_json()['overview']['oee_percent'] == 0


@pytest.mark.parametrize('width,height', [(1440, 900), (375, 812)])
def test_browser_inline_validation_and_missing_field_navigation(ui_context, live_server, client, width, height):
    """验证交互引导；参数为隔离环境和尺寸；断言错误拦截、缺项可存、跨日跳转及草稿保护。"""
    configure_capacity(client, 60)
    draft = production_payload(client, '2026-11-20')
    draft['lines'][3].update(actual_qty=510, defect_qty=0, planned_hours=20.4, abnormal_hours=None)
    assert client.put('/api/production/2026-11-20', json=draft).status_code == 200
    page = ui_context.new_page()
    page.set_viewport_size({'width': width, 'height': height})
    page.goto(live_server + '/edit')
    expect(page.get_by_test_id('save-button')).to_be_enabled()
    page.get_by_test_id('oee-issues-trigger').click()
    page.get_by_test_id('oee-issue-list').get_by_role('button').click()
    actual = page.get_by_test_id('actual-4').locator('input')
    abnormal = page.get_by_test_id('abnormal-hours-4').locator('input')
    expect(actual).to_have_value('510')
    expect(abnormal).to_be_focused()
    expect(page.get_by_test_id('abnormal-feedback-4')).to_contain_text('确认无异常停机请填 0')
    abnormal.fill('20.4')
    abnormal.press('Tab')
    expect(page.get_by_test_id('abnormal-feedback-4')).to_contain_text('异常停机工时必须小于')
    version = client.get('/api/editor').get_json()['version']
    page.get_by_test_id('save-button').click()
    expect(abnormal).to_be_focused()
    assert client.get('/api/editor').get_json()['version'] == version
    assert 'error' in page.get_by_test_id('abnormal-feedback-4').locator('..').get_attribute('class')
    # 缺项允许保存，保留未知值并持续提供定位入口。
    abnormal.fill('')
    abnormal.press('Tab')
    page.get_by_test_id('planned-hours-4').locator('input').fill('21')
    page.get_by_test_id('planned-hours-4').locator('input').press('Tab')
    page.get_by_test_id('save-button').click()
    expect(page.get_by_test_id('save-status')).to_have_text('所有修改已保存')
    saved = client.get('/api/editor?date=2026-11-20').get_json()['production']['lines'][3]
    assert saved['abnormal_hours'] is None
    expect(page.get_by_test_id('oee-issues-trigger')).to_be_visible()
    Path('output/playwright').mkdir(parents=True, exist_ok=True)
    page.get_by_test_id('abnormal-hours-4').scroll_into_view_if_needed()
    page.screenshot(path=f'output/playwright/oee-guidance-{width}.png', full_page=True)
    abnormal.fill('0')
    abnormal.press('Tab')
    expect(page.get_by_test_id('oee-issues-trigger')).to_have_count(0)
    page.get_by_test_id('save-button').click()
    expect(page.get_by_test_id('save-status')).to_have_text('所有修改已保存')
    expect(page.get_by_test_id('overview-oee')).not_to_have_text('—')


def test_browser_issue_navigation_protects_drafts_and_opens_capacity(ui_context, live_server, client):
    """验证导航保护；参数为浏览器、服务及客户端；断言取消跳转保留草稿，产能缺项定位到设置字段。"""
    draft = production_payload(client, '2026-11-20')
    draft['lines'][3].update(actual_qty=510, defect_qty=0, abnormal_hours=None)
    assert client.put('/api/production/2026-11-20', json=draft).status_code == 200
    page = ui_context.new_page()
    page.goto(live_server + '/edit')
    expect(page.get_by_test_id('save-button')).to_be_enabled()
    actual = page.get_by_test_id('actual-1').locator('input')
    actual.fill('123')
    actual.press('Tab')
    page.get_by_test_id('oee-issues-trigger').click()
    page.get_by_test_id('oee-issue-list').get_by_role('button').filter(has_text='未填写异常停机').click()
    page.get_by_role('button', name='继续编辑', exact=True).click()
    expect(actual).to_have_value('123')
    expect(page.get_by_role('combobox', name='日报日期', exact=True)).to_have_value('2026-11-21')
    page.get_by_test_id('save-button').click()
    expect(page.get_by_test_id('save-status')).to_have_text('所有修改已保存')
    page.get_by_test_id('oee-issues-trigger').click()
    page.get_by_test_id('oee-issue-list').get_by_role('button').filter(has_text='未配置设备理论最大产量').click()
    expect(page.get_by_role('tab', name='流程与设置', exact=True)).to_have_attribute('aria-selected', 'true')
    expect(page.get_by_test_id('theoretical-max-4').locator('input')).to_be_focused()
