"""通过真实浏览器验证日报计划时长输入、大屏联动及持久化。"""

from pathlib import Path

import pytest
from playwright.sync_api import expect

from tests.test_api import production_payload
from tests.test_browser import fill_control


@pytest.mark.parametrize('width,height', [(1440, 900), (375, 812)])
def test_planned_hours_edit_save_and_screen_rate(ui_context, live_server, client, width, height):
    """验证计划时长交互；上下文、服务、客户端及视口尺寸为参数；断言十二小时扣一小时、零小数及刷新持久化。"""
    draft = production_payload(client)
    draft['downtimes'] = [{'line_id': 1, 'start_at': '2026-11-21T09:00:00',
                           'end_at': '2026-11-21T10:00:00', 'kind': 'stopped'}]
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
    expect(screen.locator('.line-card[data-line-id="2"] .data-list > div').last).to_contain_text('—')
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
