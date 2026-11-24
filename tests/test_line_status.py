"""验证手动产线状态与日报工时分离。"""

from pathlib import Path

import pytest
from playwright.sync_api import expect

from backend import create_app
from tests.test_api import production_payload, settings_payload


@pytest.mark.parametrize('state,label,alert', [
    ('running', '运行中', False), ('construction', '建设中', False),
    ('stopped', '停机', True), ('maintenance', '维护', True), ('fault', '故障', True),
])
def test_line_status_is_independent_of_oee_and_daily_hours(client, app, state, label, alert):
    """验证状态独立；参数为客户端、应用、状态及预期显示；断言状态不改日报、开动率和 OEE，并持久化。"""
    config = settings_payload(client)
    config['lines'][3]['theoretical_max_per_hour'] = 50
    assert client.put('/api/settings', json=config).status_code == 200
    draft = production_payload(client)
    draft['lines'][3].update(actual_qty=800, defect_qty=40, planned_hours=20, abnormal_hours=4)
    assert client.put('/api/production/2026-11-21', json=draft).status_code == 200
    before = client.get('/api/editor').get_json()['production']
    config = settings_payload(client)
    config['lines'][3]['manual_status'] = state
    assert client.put('/api/settings', json=config).status_code == 200
    dashboard = client.get('/api/dashboard').get_json()
    line, process = dashboard['lines'][3], dashboard['processes'][3]
    assert (line['status'], line['status_label'], line['runtime_rate']) == (state, label, 80)
    assert dashboard['overview']['oee_percent'] == 76
    assert process['status'] == state and process['alert'] is alert
    saved = client.get('/api/editor').get_json()['production']
    assert saved['lines'][3]['_audit'] == before['lines'][3]['_audit']
    # 日报保存不能覆盖独立的产线展示状态。
    assert client.put('/api/production/2026-11-21', json=production_payload(client)).status_code == 200
    restarted = create_app(dict(app.config)).test_client()
    assert restarted.get('/api/dashboard').get_json()['lines'][3]['status'] == state


@pytest.mark.parametrize('invalid', [None, '', 'normal', 'building', True, 1, [], {}])
def test_invalid_line_status_rolls_back_settings(client, invalid):
    """验证非法状态；client/invalid 为接口与错误输入；断言整批配置及版本不发生部分写入。"""
    before = client.get('/api/editor').get_json()
    config = settings_payload(client)
    config['lines'][0]['name'] = '不得保存的名称'
    config['lines'][3]['manual_status'] = invalid
    response = client.put('/api/settings', json=config)
    assert response.status_code == 400 and response.get_json()['error'] == '产线状态不正确'
    assert client.get('/api/editor').get_json() == before


@pytest.mark.parametrize('width,height', [(1440, 900), (375, 812)])
def test_browser_construction_status_saves_without_fabricating_downtime(ui_context, live_server, client, width, height):
    """验证建设中标记；参数为隔离环境和尺寸；断言保存后同步、刷新保留、无故障告警和停机入口。"""
    editor, screen = ui_context.new_page(), ui_context.new_page()
    editor.set_viewport_size({'width': width, 'height': height})
    screen.set_viewport_size({'width': width, 'height': height})
    errors = []
    editor.on('pageerror', lambda error: errors.append(str(error)))
    screen.on('pageerror', lambda error: errors.append(str(error)))
    screen.goto(live_server + '/screen')
    editor.goto(live_server + '/edit')
    expect(editor.get_by_test_id('save-button')).to_be_enabled()
    expect(editor.get_by_role('heading', name='停机时段', exact=True)).to_have_count(0)
    expect(editor.get_by_role('button', name='新增时段', exact=True)).to_have_count(0)
    editor.get_by_role('tab', name='流程与设置', exact=True).click()
    editor.get_by_test_id('line-status-4').click()
    editor.get_by_role('option', name='建设中', exact=True).click()
    card = screen.locator('.line-card[data-line-id="4"]')
    expect(card.locator('.line-state')).to_have_text('运行中')
    editor.get_by_test_id('save-button').click()
    expect(editor.get_by_test_id('save-status')).to_have_text('所有修改已保存')
    expect(card.locator('.line-state')).to_have_text('建设中', timeout=10000)
    expect(card).to_have_attribute('data-state', 'construction')
    expect(card.locator('.data-list > div').last).to_have_text('运行状态建设中')
    expect(screen.get_by_text('时间开动率', exact=True)).to_have_count(0)
    expect(editor.get_by_text('时间开动率', exact=True)).to_have_count(0)
    expect(card.locator('.progress-bar')).not_to_be_visible()
    process = screen.locator('.process-step[data-process-id="4"]')
    expect(process.locator('.step-status')).to_have_text('B线建设中')
    expect(process.locator('.process-alert')).to_have_count(0)
    expect(screen.locator('#flowWarning')).not_to_be_visible()
    editor.reload()
    expect(editor.get_by_test_id('save-button')).to_be_enabled()
    row = editor.get_by_test_id('production-table').locator('.el-table__body-wrapper tbody tr').nth(3)
    expect(row.locator('.status-pill')).to_have_text('建设中')
    # 补录历史工时不会让建设状态自动恢复，也不会伪造其他日期的日报。
    draft = production_payload(client, '2026-11-20')
    draft['lines'][3].update(planned_hours=20, abnormal_hours=4)
    assert client.put('/api/production/2026-11-20', json=draft).status_code == 200
    assert client.get('/api/editor?date=2026-11-20').get_json()['production']['lines'][3]['runtime_rate'] == 80
    screen.reload()
    expect(card.locator('.line-state')).to_have_text('建设中 80%')
    assert editor.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert screen.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert not errors, errors
    Path('output/playwright').mkdir(parents=True, exist_ok=True)
    editor.get_by_role('tab', name='流程与设置', exact=True).click()
    editor.get_by_test_id('line-status-4').scroll_into_view_if_needed()
    editor.screenshot(path=f'output/playwright/manual-line-status-editor-{width}.png', full_page=True)
    card.screenshot(path=f'output/playwright/manual-line-status-screen-{width}.png')
