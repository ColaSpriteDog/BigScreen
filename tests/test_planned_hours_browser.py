"""通过真实浏览器验证日报计划时长输入、大屏联动及持久化。"""

from pathlib import Path

import pytest
from playwright.sync_api import expect

from tests.test_api import production_payload
from tests.test_browser import fill_control


@pytest.mark.parametrize('width,height', [(1440, 900), (375, 812)])
def test_daily_full_stop_shows_zero_without_changing_line_status(ui_context, live_server, client, width, height):
    """验证日报全停机；参数为隔离环境及尺寸；断言开动率为零且独立状态保持运行，不再出现停机时段入口。"""
    draft = production_payload(client)
    draft['lines'][3].update(actual_qty=0, defect_qty=0, planned_hours=20.4, abnormal_hours=20.4)
    assert client.put('/api/production/2026-11-21', json=draft).status_code == 200
    line = client.get('/api/dashboard').get_json()['lines'][3]
    assert line['runtime_rate'] == 0 and line['status'] == 'running'
    page = ui_context.new_page()
    page.set_viewport_size({'width': width, 'height': height})
    page.goto(live_server + '/edit')
    expect(page.get_by_test_id('save-button')).to_be_enabled()
    expect(page.get_by_test_id('downtime-table')).to_have_count(0)
    expect(page.get_by_role('button', name='开始停机', exact=False)).to_have_count(0)
    row = page.get_by_test_id('production-table').locator('.el-table__body-wrapper tbody tr').nth(3)
    expect(row.locator('.status-pill')).to_have_text('运行中')
    expect(row.locator('td').nth(10)).to_have_text('0%')
    page.goto(live_server + '/screen')
    card = page.locator('.line-card[data-line-id="4"]')
    expect(card.locator('.data-list > div').last).to_have_text('运行状态（月）运行中 0%')


@pytest.mark.parametrize('width,height', [(1440, 900), (375, 812)])
def test_planned_hours_edit_save_and_screen_rate(ui_context, live_server, client, width, height):
    """验证计划时长交互；上下文、服务、客户端及视口尺寸为参数；断言十二小时扣一小时、零小数及刷新持久化。"""
    draft = production_payload(client)
    draft['lines'][0]['abnormal_hours'] = 1
    assert client.put('/api/production/2026-11-21', json=draft).status_code == 200
    editor = ui_context.new_page()
    screen = ui_context.new_page()
    editor.set_viewport_size({'width': width, 'height': height})
    screen.goto(live_server + '/screen')
    editor.goto(live_server + '/edit')
    expect(editor.get_by_test_id('save-button')).to_be_enabled()
    control = editor.get_by_test_id('planned-hours-1').locator('input')
    assert float(control.input_value()) == 24
    fill_control(editor, 'planned-hours-1', 12)
    editor.get_by_role('heading', level=1).click()
    expect(editor.get_by_test_id('save-status')).to_have_text('1 处修改未保存')
    editor.get_by_test_id('save-button').click()
    expect(editor.get_by_test_id('save-status')).to_have_text('所有修改已保存')
    expect(screen.locator('.line-card[data-line-id="1"]')).to_contain_text('91.7%')
    assert '12' in screen.locator('.line-card[data-line-id="1"] .data-list > div').last.get_attribute('title')
    fill_control(editor, 'planned-hours-2', 0)
    fill_control(editor, 'planned-hours-3', 7.5)
    editor.get_by_test_id('save-button').click()
    expect(editor.get_by_test_id('save-status')).to_have_text('所有修改已保存')
    data = client.get('/api/dashboard').get_json()['lines']
    assert [line['planned_hours'] for line in data] == [12, 0, 7.5, 24]
    assert data[1]['runtime_rate'] is None
    expect(screen.locator('.line-card[data-line-id="2"] .data-list > div').last).to_have_text('运行状态（月）运行中')
    editor.reload()
    expect(editor.get_by_test_id('save-button')).to_be_enabled()
    assert float(editor.get_by_test_id('planned-hours-1').locator('input').input_value()) == 12
    assert float(editor.get_by_test_id('planned-hours-2').locator('input').input_value()) == 0
    assert float(editor.get_by_test_id('planned-hours-3').locator('input').input_value()) == 7.5
    assert editor.evaluate('document.documentElement.scrollWidth') == width
    expect(editor.get_by_test_id('save-button')).to_be_in_viewport()
    Path('output/playwright').mkdir(parents=True, exist_ok=True)
    editor.get_by_test_id('planned-hours-1').scroll_into_view_if_needed()
    editor.screenshot(path=f'output/playwright/editor-planned-hours-{width}.png', full_page=True)
    screen.screenshot(path=f'output/playwright/screen-planned-hours-{width}.png')
