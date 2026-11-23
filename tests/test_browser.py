from pathlib import Path

import pytest

from playwright.sync_api import expect


def fill_control(page, identifier, value):
    """填写组件输入；page 为页面，identifier 为测试ID，value 为文本；无返回值。"""
    page.locator(f'input[id="{identifier}"], input[data-testid="{identifier}"], [data-testid="{identifier}"] input').fill(str(value))


def set_trend_visibility(page, identifier, selected):
    """操作可见复选框标签；identifier 为产线ID、selected 为目标状态；无返回值。"""
    checkbox = page.get_by_role('checkbox', name=f'趋势图显示产线 {identifier:02d}', exact=True)
    if checkbox.is_checked() != selected:
        page.get_by_test_id(f'trend-toggle-{identifier}').click()
    if selected:
        expect(checkbox).to_be_checked()
    else:
        expect(checkbox).not_to_be_checked()


def test_editor_save_updates_screen_and_survives_reload(ui_context, live_server):
    """验证完整保存链路；上下文与真实服务为参数；断言库存落库、大屏更新和刷新持久化。"""
    screen = ui_context.new_page()
    editor = ui_context.new_page()
    errors = []
    def collect_error(error):
        """记录浏览器异常；error 为页面错误；无返回值。"""
        errors.append(str(error))
    screen.on('pageerror', collect_error)
    editor.on('pageerror', collect_error)
    screen.goto(live_server + '/screen')
    editor.goto(live_server + '/edit')
    expect(editor.get_by_test_id('save-button')).to_be_enabled()
    editor.get_by_role('tab', name='库存明细').click()
    editor.get_by_test_id('add-raw').click()
    fill_control(editor, 'raw-material-0', '钢卷')
    fill_control(editor, 'raw-spec-0', '1.6*1430')
    fill_control(editor, 'raw-position-0', 'A柱')
    fill_control(editor, 'raw-quantity-0', '0.5')
    fill_control(editor, 'raw-cuttable-0', '123')
    editor.get_by_test_id('save-button').click()
    expect(editor.get_by_text('保存成功', exact=True)).to_be_visible()
    expect(screen.locator('#rawTableBody')).to_contain_text('1.6*1430')
    expect(screen.locator('#rawTotal')).to_have_text('0.5（卷）')
    expect(screen.locator('#rawTableBody tr td').nth(4)).to_have_text('123')
    screen.reload()
    expect(screen.locator('#rawTableBody')).to_contain_text('1.6*1430')
    expect(screen.locator('#connectionStatus')).to_have_text('数据已同步')
    assert not errors, errors
    Path('output/playwright').mkdir(parents=True, exist_ok=True)
    screen.screenshot(path='output/playwright/screen-1440x720.png')
    editor.screenshot(path='output/playwright/editor-inventory.png', full_page=True)


def test_production_and_sales_forms_update_actual_charts(ui_context, live_server, client):
    """验证日报销售联动；参数为浏览器、服务和客户端；断言真实图表、汇总和状态使用保存数据。"""
    from tests.test_api import production_payload, settings_payload
    config = settings_payload(client)
    config['lines'][2]['standard_qty'] = 500
    config['lines'][3]['standard_qty'] = 500
    assert client.put('/api/settings', json=config).status_code == 200
    editor = ui_context.new_page()
    screen = ui_context.new_page()
    editor.goto(live_server + '/edit')
    screen.goto(live_server + '/screen')
    expect(editor.get_by_test_id('save-button')).to_be_enabled()
    for identifier, amount in enumerate([100, 200, 300, 400], 1):
        fill_control(editor, f'actual-{identifier}', amount)
        fill_control(editor, f'defect-{identifier}', identifier)
    editor.get_by_test_id('save-button').click()
    expect(screen.locator('#actualQty')).to_have_text('700（件）')
    expect(screen.locator('#completionRate')).to_have_text('70%')
    expect(screen.locator('.chart-heading')).to_have_text('当月产线产量趋势图')
    assert screen.evaluate("echarts.getInstanceByDom(document.querySelector('#newTrendChart')).getOption().xAxis[0].data") == [f'2026-11-{day:02d}' for day in range(1, 22)]
    Path('output/playwright').mkdir(parents=True, exist_ok=True)
    editor.screenshot(path='output/playwright/editor-production.png', full_page=True)
    editor.get_by_role('tab', name='销售出货', exact=True).click()
    fill_control(editor, 'order-input', 10000)
    for index, (part, qty) in enumerate([('P-300', 3200), ('P-200', 2100), ('P-100', 0)]):
        editor.get_by_test_id('add-shipment').click()
        fill_control(editor, f'part-{index}', part)
        fill_control(editor, f'shipment-quantity-{index}', qty)
    expect(editor.locator('input[data-testid="order-input"], [data-testid="order-input"] input')).to_have_value('10000')
    editor.get_by_test_id('save-button').click()
    expect(screen.locator('#shippedQty')).to_have_text('5,300')
    expect(screen.locator('#shipmentRate')).to_have_text('53%')
    screen.wait_for_function("echarts.getInstanceByDom(document.querySelector('#shipmentChart')).getOption().series[0].data[0] === 3200")
    assert screen.evaluate("echarts.getInstanceByDom(document.querySelector('#shipmentChart')).getOption().yAxis[0].data") == ['P-300', 'P-200', 'P-100']
    draft = production_payload(client)
    draft['downtimes'] = [{'line_id': 4, 'start_at': '2026-11-21T11:00:00', 'end_at': '2026-11-21T13:00:00', 'kind': 'maintenance'}]
    assert client.put('/api/production/2026-11-21', json=draft).status_code == 200
    expect(screen.locator('.line-card[data-line-id="4"]')).to_have_attribute('data-state', 'maintenance')
    expect(screen.locator('.process-step[data-process-id="4"] .step-status')).to_have_text('B线维护')
    assert screen.evaluate('document.documentElement.scrollWidth') == 1440
    assert screen.evaluate('document.documentElement.scrollHeight') == 720
    Path('output/playwright').mkdir(parents=True, exist_ok=True)
    screen.screenshot(path='output/playwright/screen-data-and-maintenance.png')
    editor.screenshot(path='output/playwright/editor-sales.png', full_page=True)


def test_settings_form_saves_safety_start(ui_context, live_server):
    """验证设置界面；参数为浏览器及服务；断言选择的安全起算日期联动到大屏。"""
    editor = ui_context.new_page()
    screen = ui_context.new_page()
    editor.goto(live_server + '/edit')
    screen.goto(live_server + '/screen')
    expect(editor.get_by_test_id('save-button')).to_be_enabled()
    editor.get_by_role('tab', name='流程与设置', exact=True).click()
    fill_control(editor, 'safety-date', '2026-11-01')
    editor.locator('input#safety-date').press('Enter')
    editor.get_by_role('spinbutton', name='标准日产量 01', exact=True).fill('1320')
    editor.get_by_test_id('save-button').click()
    expect(screen.locator('#safetyDays')).to_have_text('20')
    expect(screen.locator('.line-card[data-line-id="1"] .data-list > div').first).to_have_text('标准产量（米/天）1,320')


def test_quick_maintenance_and_resume_from_editor(ui_context, live_server, clock):
    """验证快捷停机操作；参数为页面、服务和时钟；断言维护、恢复同步到产线和流程。"""
    from datetime import timedelta
    editor = ui_context.new_page()
    screen = ui_context.new_page()
    editor.goto(live_server + '/edit')
    screen.goto(live_server + '/screen')
    expect(editor.get_by_test_id('save-button')).to_be_enabled()
    editor.get_by_role('button', name='开始停机 ▾', exact=True).nth(3).click()
    editor.get_by_role('menuitem', name='维护', exact=True).click()
    expect(screen.locator('.line-card[data-line-id="4"]')).to_have_attribute('data-state', 'maintenance')
    expect(editor.get_by_role('button', name='恢复运行', exact=True)).to_be_visible()
    clock['now'] += timedelta(minutes=10)
    editor.get_by_role('button', name='恢复运行', exact=True).click()
    expect(screen.locator('.line-card[data-line-id="4"]')).to_have_attribute('data-state', 'running')
    expect(screen.locator('.line-card[data-line-id="4"]')).to_contain_text('99.3%')


def test_validation_failure_keeps_draft_and_does_not_publish(ui_context, live_server):
    """验证无效保存；参数为页面和服务；断言错误保留草稿且大屏没有部分数据。"""
    editor = ui_context.new_page()
    screen = ui_context.new_page()
    editor.goto(live_server + '/edit')
    screen.goto(live_server + '/screen')
    expect(editor.get_by_test_id('save-button')).to_be_enabled()
    editor.get_by_role('tab', name='库存明细', exact=True).click()
    editor.get_by_test_id('add-raw').click()
    fill_control(editor, 'raw-material-0', '待保存钢卷')
    fill_control(editor, 'raw-quantity-0', '0')
    editor.get_by_test_id('save-button').click()
    expect(editor.locator('.request-error')).to_contain_text('规格不能为空')
    expect(editor.locator('input[data-testid="raw-material-0"], [data-testid="raw-material-0"] input')).to_have_value('待保存钢卷')
    expect(editor.get_by_test_id('save-status')).to_contain_text('处修改未保存')
    expect(screen.locator('#rawTableBody')).to_contain_text('暂无原材料记录')
    fill_control(editor, 'raw-spec-0', '1.6*1200')
    editor.get_by_test_id('save-button').click()
    expect(screen.locator('#rawTableBody')).to_contain_text('待保存钢卷')
    expect(screen.locator('#zeroCount')).to_contain_text('零库存')


def test_unsaved_tab_switch_can_be_cancelled(ui_context, live_server):
    """验证草稿保护；参数为页面和服务；断言切换取消保留原模块，确认放弃才切换。"""
    editor = ui_context.new_page()
    editor.goto(live_server + '/edit')
    expect(editor.get_by_test_id('save-button')).to_be_enabled()
    fill_control(editor, 'actual-1', 123)
    editor.get_by_role('tab', name='库存明细', exact=True).click()
    expect(editor.get_by_role('dialog', name='未保存的修改')).to_be_visible()
    editor.get_by_role('button', name='继续编辑', exact=True).click()
    expect(editor.get_by_role('tab', name='生产日报', exact=True)).to_have_attribute('aria-selected', 'true')
    expect(editor.locator('[data-testid="actual-1"] input')).to_have_value('123')
    editor.get_by_role('tab', name='库存明细', exact=True).click()
    editor.get_by_role('dialog', name='未保存的修改').get_by_role('button', name='放弃修改', exact=True).click()
    expect(editor.get_by_role('tab', name='库存明细', exact=True)).to_have_attribute('aria-selected', 'true')


def test_polling_updates_other_browser_context_without_reload(browser, ui_context, live_server, client):
    """验证跨设备等价链路；参数为浏览器、页面及接口；断言独立上下文按轮询更新而不依赖广播。"""
    isolated = browser.new_context(viewport={'width': 1440, 'height': 720})
    try:
        screen = isolated.new_page()
        screen.goto(live_server + '/screen')
        expect(screen.locator('#connectionStatus')).to_have_text('数据已同步')
        response = client.put('/api/sales/2026-11', json={'version': 0, 'order_qty': 500,
                              'items': [{'part_no': 'REMOTE-1', 'quantity': 200}]})
        assert response.status_code == 200
        expect(screen.locator('#shippedQty')).to_have_text('200', timeout=10000)
        expect(screen.locator('#shipmentRate')).to_have_text('40%')
    finally:
        isolated.close()


def test_failed_poll_keeps_last_successful_values(ui_context, live_server, client):
    """验证断网显示；参数为页面、服务及客户端；断言连接中断时保留最后成功数据。"""
    assert client.put('/api/sales/2026-11', json={'version': 0, 'order_qty': 500, 'items': [{'part_no': 'P', 'quantity': 200}]}).status_code == 200
    page = ui_context.new_page()
    page.goto(live_server + '/screen')
    expect(page.locator('#shippedQty')).to_have_text('200')
    page.route('**/api/dashboard', lambda route: route.abort())
    expect(page.locator('#connectionStatus')).to_have_text('连接中断', timeout=10000)
    expect(page.locator('#shippedQty')).to_have_text('200')


def test_overflowing_inventory_scrolls_automatically_without_controls(ui_context, live_server, client):
    """验证无按钮自动滚动；参数为页面及接口；断言溢出自动滚动、内容不足时保持原位。"""
    raw = [dict(material='钢卷', warehouse='原材料仓库', specification=f'规格-{index}', position='A柱',
                quantity=index, unit='卷', weight_kg=None, cuttable_qty=index * 10) for index in range(16)]
    assert client.put('/api/inventory', json={'version': 0, 'raw': raw, 'semi': []}).status_code == 200
    page = ui_context.new_page()
    page.goto(live_server + '/screen')
    page.mouse.move(700, 30)
    expect(page.locator('#rawTableBody tr')).to_have_count(16)
    page.wait_for_function("document.querySelector('.material-table-wrap').scrollTop > 8", timeout=6000)
    assert page.get_by_role('button', name='暂停滚动', exact=True).count() == 0
    assert page.get_by_role('button', name='继续滚动', exact=True).count() == 0
    table = page.locator('.material-table-wrap').first
    before = table.evaluate('(element)=>element.scrollTop')
    table.hover()
    page.wait_for_function('(previous)=>document.querySelector(".material-table-wrap").scrollTop > previous + 2', arg=before)
    assert page.locator('.material-table-wrap').nth(1).evaluate('(element)=>element.scrollTop') == 0


def test_inventory_and_sales_edges_match_outer_lines_and_safety_icon_is_larger(ui_context, live_server):
    """验证主尺寸对齐；参数为页面与服务；断言左右面板与产线等宽且安全图标放大。"""
    page = ui_context.new_page()
    page.goto(live_server + '/screen')
    expect(page.locator('#connectionStatus')).to_have_text('数据已同步')
    for panel, card in [('.inventory-panel', '.line-card[data-line-id="1"]'),
                        ('.shipment-panel', '.line-card[data-line-id="4"]')]:
        panel_box = page.locator(panel).bounding_box()
        card_box = page.locator(card).bounding_box()
        assert abs(panel_box['x'] - card_box['x']) < 1
        assert abs(panel_box['width'] - card_box['width']) < 1
    assert page.locator('.safety-icon').bounding_box()['width'] >= 64
    assert page.locator('.safety-icon svg').bounding_box()['width'] >= 36
    center_box = page.locator('.chart-panel').bounding_box()
    second_box = page.locator('.line-card[data-line-id="2"]').bounding_box()
    third_box = page.locator('.line-card[data-line-id="3"]').bounding_box()
    assert abs(center_box['x'] - second_box['x']) < 1
    assert abs(center_box['x'] + center_box['width'] - third_box['x'] - third_box['width']) < 1
    assert page.evaluate('document.documentElement.scrollWidth') == 1440
    assert page.evaluate('document.documentElement.scrollHeight') == 720


@pytest.mark.parametrize('width,height', [(1440, 720), (1920, 1080), (375, 812)])
def test_screen_layout_and_local_images(ui_context, live_server, width, height):
    """验证响应布局；参数为页面、服务及尺寸；断言稳定画面不横向溢出且五张切图加载成功。"""
    page = ui_context.new_page()
    page.set_viewport_size({'width': width, 'height': height})
    page.goto(live_server + '/screen')
    expect(page.locator('#connectionStatus')).to_have_text('数据已同步')
    page.wait_for_function('document.querySelectorAll(".process-illustration").length===5 && [...document.querySelectorAll(".process-illustration")].every(image=>image.complete&&image.naturalWidth>0)')
    page.wait_for_function('document.documentElement.scrollWidth <= innerWidth')
    if width >= 1201:
        assert page.evaluate('document.documentElement.scrollHeight') == height


def test_manual_stop_time_entry_drives_runtime(ui_context, live_server):
    """验证手工时间录入；参数为页面和服务；断言用户填写的时段直接参与运行率计算。"""
    editor = ui_context.new_page()
    screen = ui_context.new_page()
    editor.goto(live_server + '/edit')
    screen.goto(live_server + '/screen')
    expect(editor.get_by_test_id('save-button')).to_be_enabled()
    editor.get_by_role('button', name='新增时段', exact=True).click()
    fill_control(editor, 'stop-start-0', '2026-11-21 09:00')
    editor.locator('input#stop-start-0').press('Enter')
    fill_control(editor, 'stop-end-0', '2026-11-21 10:00')
    editor.locator('input#stop-end-0').press('Enter')
    editor.get_by_test_id('save-button').click()
    expect(screen.locator('.line-card[data-line-id="1"]')).to_contain_text('95.8%')
    expect(screen.locator('.line-card[data-line-id="1"]')).to_have_attribute('data-state', 'running')


def test_concurrent_save_keeps_ui_draft(ui_context, live_server, client):
    """验证并发保存界面；参数为页面、服务及客户端；断言冲突提示出现且未保存数据不丢失。"""
    page = ui_context.new_page()
    page.goto(live_server + '/edit')
    expect(page.get_by_test_id('save-button')).to_be_enabled()
    fill_control(page, 'actual-1', '456')
    assert client.put('/api/sales/2026-11', json={'version': 0, 'order_qty': 500, 'items': []}).status_code == 200
    page.get_by_test_id('save-button').click()
    expect(page.locator('.request-error')).to_contain_text('请重新加载后保存')
    expect(page.locator('[data-testid="actual-1"] input')).to_have_value('456')
    expect(page.get_by_test_id('save-status')).to_contain_text('处修改未保存')


def test_business_text_is_rendered_as_text_not_html(ui_context, live_server, client):
    """验证录入文本安全；参数为页面和接口；断言文本不会创建 HTML 图片或执行脚本。"""
    content = '<img src=x onerror="window.__injected=1">'
    raw = dict(material='钢卷', warehouse='原材料仓库', specification=content, position='A柱', quantity=1, unit='卷')
    assert client.put('/api/inventory', json={'version': 0, 'raw': [raw], 'semi': []}).status_code == 200
    page = ui_context.new_page()
    page.goto(live_server + '/screen')
    expect(page.locator('#rawTableBody')).to_contain_text(content)
    assert page.locator('#rawTableBody img').count() == 0
    assert page.evaluate('window.__injected === undefined')


def test_copy_delete_and_semi_finished_entry(ui_context, live_server):
    """验证库存常用操作；参数为页面和服务；断言复制、删除、半成品录入与汇总共同生效。"""
    editor = ui_context.new_page()
    screen = ui_context.new_page()
    editor.goto(live_server + '/edit')
    screen.goto(live_server + '/screen')
    expect(editor.get_by_test_id('save-button')).to_be_enabled()
    editor.get_by_role('tab', name='库存明细', exact=True).click()
    editor.get_by_test_id('add-raw').click()
    fill_control(editor, 'raw-material-0', '钢卷')
    fill_control(editor, 'raw-spec-0', '1.6')
    fill_control(editor, 'raw-quantity-0', 10)
    editor.get_by_test_id('raw-editor-table').get_by_role('button', name='复制', exact=True).click()
    fill_control(editor, 'raw-spec-1', '2.0')
    fill_control(editor, 'raw-quantity-1', 0)
    editor.get_by_role('button', name='新增半成品', exact=True).click()
    editor.get_by_role('textbox', name='半成品物料', exact=True).fill('门槛左件')
    editor.get_by_role('textbox', name='半成品规格', exact=True).fill('1.4')
    editor.get_by_role('spinbutton', name='半成品库存', exact=True).fill('5')
    editor.get_by_test_id('save-button').click()
    expect(screen.locator('#rawTotal')).to_have_text('10（卷）')
    expect(screen.locator('#semiTotal')).to_have_text('5（件）')
    expect(screen.locator('#zeroCount')).to_have_text('1项零库存')
    editor.get_by_test_id('raw-editor-table').get_by_role('button', name='删除', exact=True).first.click()
    editor.get_by_test_id('save-button').click()
    expect(screen.locator('#rawTotal')).to_have_text('0（卷）')
    expect(screen.locator('#rawTableBody tr')).to_have_count(1)


def test_editable_names_and_trend_selection_update_chart_at_1440(ui_context, live_server, client):
    """验证配置到图表链路；参数为页面与接口；断言四线全选、改单线、改名和颜色持久一致。"""
    from tests.test_api import production_payload
    draft = production_payload(client)
    for line, amount in zip(draft['lines'], [100, 200, 300, 400]):
        line.update(actual_qty=amount, defect_qty=0)
    assert client.put('/api/production/2026-11-21', json=draft).status_code == 200
    editor = ui_context.new_page()
    screen = ui_context.new_page()
    editor.goto(live_server + '/edit')
    screen.goto(live_server + '/screen')
    expect(editor.get_by_test_id('save-button')).to_be_enabled()
    editor.get_by_role('tab', name='流程与设置', exact=True).click()
    for index, prefix in enumerate(['一号', '二号', '三号', '四号'], 1):
        editor.get_by_role('textbox', name=f'产线名称 {index:02d}', exact=True).fill(prefix + '生产线' * 6)
        set_trend_visibility(editor, index, True)
    editor.get_by_test_id('save-button').click()
    expect(screen.locator('#trendLegend > span')).to_have_count(5)
    expect(screen.locator('#trendTotal')).to_have_text('本月累计：1,000（件）')
    expect(screen.locator('.line-card[data-line-id="1"] .line-header')).to_contain_text('一号生产线')
    screen.wait_for_function("echarts.getInstanceByDom(document.querySelector('#newTrendChart')).getOption().series.length === 5")
    colors = screen.evaluate("echarts.getInstanceByDom(document.querySelector('#newTrendChart')).getOption().series.slice(1).map(series=>series.itemStyle.color)")
    assert len(set(colors)) == 4
    assert screen.evaluate('document.documentElement.scrollWidth') == 1440
    assert screen.evaluate('document.documentElement.scrollHeight') == 720
    assert screen.locator('#newTrendChart').evaluate('(element)=>element.clientHeight') >= 120
    assert screen.locator('#trendLegend').evaluate('(element)=>element.scrollHeight<=element.clientHeight+1')
    Path('output/playwright').mkdir(parents=True, exist_ok=True)
    screen.screenshot(path='output/playwright/screen-four-selected-lines.png')
    editor.screenshot(path='output/playwright/editor-trend-settings.png', full_page=True)
    for index in [1, 3, 4]:
        set_trend_visibility(editor, index, False)
    editor.get_by_test_id('save-button').click()
    expect(screen.locator('#trendLegend > span')).to_have_count(2)
    expect(screen.locator('#trendTotal')).to_have_text('本月累计：200（件）')
    screen.wait_for_function("echarts.getInstanceByDom(document.querySelector('#newTrendChart')).getOption().series.length === 2")
    assert screen.evaluate("echarts.getInstanceByDom(document.querySelector('#newTrendChart')).getOption().series[1].itemStyle.color") == colors[1]
    editor.reload()
    expect(editor.get_by_test_id('save-button')).to_be_enabled()
    expect(editor.get_by_role('spinbutton', name='今日产量 二号生产线生产线生产线生产线生产线生产线')).to_have_value('200')
    editor.get_by_role('tab', name='流程与设置', exact=True).click()
    expect(editor.get_by_role('checkbox', name='趋势图显示产线 02', exact=True)).to_be_checked()
    expect(editor.get_by_role('checkbox', name='趋势图显示产线 01', exact=True)).not_to_be_checked()


def test_editor_requires_at_least_one_trend_line(ui_context, live_server):
    """验证编辑端空选提示；参数为页面与服务；断言保留草稿且大屏继续使用已保存选择。"""
    editor = ui_context.new_page()
    screen = ui_context.new_page()
    editor.goto(live_server + '/edit')
    screen.goto(live_server + '/screen')
    expect(editor.get_by_test_id('save-button')).to_be_enabled()
    editor.get_by_role('tab', name='流程与设置', exact=True).click()
    for index in [1, 2, 3, 4]:
        set_trend_visibility(editor, index, False)
    editor.get_by_test_id('save-button').click()
    expect(editor.locator('.request-error')).to_contain_text('趋势图至少选择一条产线')
    expect(editor.get_by_test_id('save-status')).to_contain_text('处修改未保存')
    expect(screen.locator('#trendLegend > span')).to_have_count(3)


def test_slow_module_loading_blocks_input_until_snapshot_is_applied(ui_context, live_server, monkeypatch):
    """验证慢请求输入保护；参数为页面、服务及替换工具；断言加载时禁用，完成后首个输入不会丢失。"""
    import time
    from flask import request
    from backend import service
    original_snapshot = service.editor_snapshot

    def delay_module_read(*arguments, **keywords):
        """模拟模块读取延迟；arguments、keywords 为快照参数；返回原始快照，仅切换模块时延迟。"""
        if request.path == '/api/editor' and request.args.get('date'):
            time.sleep(0.7)
        return original_snapshot(*arguments, **keywords)

    monkeypatch.setattr(service, 'editor_snapshot', delay_module_read)

    page = ui_context.new_page()
    page.goto(live_server + '/edit')
    expect(page.get_by_test_id('save-button')).to_be_enabled()
    page.get_by_role('tab', name='销售出货', exact=True).click()
    order = page.locator('input[data-testid="order-input"], [data-testid="order-input"] input')
    expect(order).to_be_disabled()
    order.fill('10000')
    page.get_by_role('heading', name='件号出货明细', exact=True).click()
    expect(order).to_have_value('10000')
    expect(page.get_by_test_id('save-status')).to_contain_text('处修改未保存')


@pytest.mark.parametrize('width,height', [(1440, 720), (1920, 1080), (375, 812)])
def test_demo_screen_full_data_alignment_and_overflow(ui_context, live_server, app, clock, width, height):
    """验证完整演示页面；参数为隔离应用、时间及尺寸；断言字段完整、滚动和边缘对齐。"""
    from demo_data import apply_demo_data
    apply_demo_data(app.config['DATABASE'], clock['now'])
    page = ui_context.new_page()
    page.set_viewport_size({'width': width, 'height': height})
    page.goto(live_server + '/screen')
    expect(page.locator('#connectionStatus')).to_have_text('数据已同步')
    expect(page.locator('#rawTableBody tr')).to_have_count(8)
    expect(page.locator('#semiTableBody tr')).to_have_count(10)
    expect(page.locator('#shippedQty')).to_have_text('37,400')
    expect(page.locator('#trendLegend > span')).to_have_count(3)
    expect(page.locator('#rawTableBody tr').first.locator('td')).to_have_count(5)
    expect(page.locator('#rawTableHeader')).not_to_contain_text('仓库')
    expect(page.locator('#rawTableHeader')).to_contain_text('剩余数量（卷）')
    expect(page.locator('#semiTableHeader')).to_contain_text('剩余数量（件）')
    assert page.locator('#rawTableBody .inventory-unit, #semiTableBody .inventory-unit').count() == 0
    page.wait_for_function('document.documentElement.scrollWidth<=innerWidth')
    table_sizes = page.locator('.material-table-wrap').evaluate_all('(elements)=>elements.map(e=>({width:e.clientWidth,scrollWidth:e.scrollWidth,height:e.clientHeight,scrollHeight:e.scrollHeight}))')
    assert all(item['scrollWidth'] <= item['width'] + 1 for item in table_sizes), table_sizes
    if width >= 1201:
        assert page.evaluate('document.documentElement.scrollHeight') == height
        for panel, card in [('.inventory-panel', '.line-card[data-line-id="1"]'),
                            ('.shipment-panel', '.line-card[data-line-id="4"]')]:
            panel_box = page.locator(panel).bounding_box()
            card_box = page.locator(card).bounding_box()
            assert abs(panel_box['x'] - card_box['x']) < 1
            assert abs(panel_box['width'] - card_box['width']) < 1
    Path('output/playwright').mkdir(parents=True, exist_ok=True)
    page.screenshot(path=f'output/playwright/screen-demo-{width}x{height}.png', full_page=width<1201)


def test_process_connectors_flow_without_reset_on_unchanged_poll(ui_context, live_server):
    """验证正常连接动画；参数为页面与服务；断言向右流动，重复读取不重建相同工序节点。"""
    page = ui_context.new_page()
    page.emulate_media(reduced_motion='no-preference')
    page.goto(live_server + '/screen')
    expect(page.locator('#connectionStatus')).to_have_text('数据已同步')
    connectors = page.locator('.process-connector')
    expect(connectors).to_have_count(4)
    assert connectors.evaluate_all('(items)=>items.every(item=>item.dataset.state==="running")')
    stream = connectors.first.locator('.connector-stream')
    assert stream.evaluate('(element)=>getComputedStyle(element).animationName') == 'process-flow-dash'
    initial = stream.evaluate('(element)=>getComputedStyle(element).strokeDashoffset')
    page.wait_for_function('(offset)=>getComputedStyle(document.querySelector(".connector-stream")).strokeDashoffset !== offset', arg=initial)
    page.evaluate('window.__connectorBeforePoll=document.querySelector(".process-connector")')
    with page.expect_response('**/api/dashboard'):
        page.wait_for_timeout(5500)
    assert page.evaluate('window.__connectorBeforePoll === document.querySelector(".process-connector")')


@pytest.mark.parametrize('kind', ['maintenance', 'fault', 'stopped'])
def test_abnormal_process_stops_only_adjacent_connectors_and_resumes(ui_context, live_server, client, clock, kind):
    """验证异常联动和恢复；kind 为停机类型；断言两侧静止橙色、其他连接正常且时间结束自动恢复。"""
    from datetime import datetime, timezone
    from tests.test_api import production_payload
    draft = production_payload(client)
    draft['downtimes'] = [{'line_id': 4, 'start_at': '2026-11-21T11:00:00',
                           'end_at': '2026-11-21T13:00:00', 'kind': kind}]
    assert client.put('/api/production/2026-11-21', json=draft).status_code == 200
    page = ui_context.new_page()
    page.emulate_media(reduced_motion='no-preference')
    page.goto(live_server + '/screen')
    expect(page.locator('#connectionStatus')).to_have_text('数据已同步')
    states = page.locator('.process-connector').evaluate_all('(items)=>items.map(item=>item.dataset.state)')
    assert states == ['running', 'running', 'stopped', 'stopped']
    for connector in page.locator('.process-connector[data-state="stopped"]').all():
        assert connector.evaluate('(element)=>getComputedStyle(element).color') == 'rgb(255, 157, 35)'
        assert connector.locator('.connector-stream').evaluate('(element)=>getComputedStyle(element).animationName') == 'none'
        assert connector.locator('.connector-spark').evaluate('(element)=>getComputedStyle(element).display') == 'none'
    assert page.locator('.process-connector').first.locator('.connector-stream').evaluate('(element)=>getComputedStyle(element).animationName') == 'process-flow-dash'
    assert page.evaluate('document.documentElement.scrollWidth') == 1440
    assert page.evaluate('document.documentElement.scrollHeight') == 720
    clock['now'] = datetime(2026, 11, 21, 13, tzinfo=timezone.utc)
    expect(page.locator('.process-connector[data-state="running"]')).to_have_count(4, timeout=10000)
    assert page.locator('.process-connector[data-state="stopped"]').count() == 0


def test_process_connection_animation_respects_reduced_motion(ui_context, live_server):
    """验证减少动态效果；参数为页面与服务；断言连接保持可见而流动和光点停止。"""
    page = ui_context.new_page()
    page.goto(live_server + '/screen')
    expect(page.locator('#connectionStatus')).to_have_text('数据已同步')
    assert page.locator('.connector-stream').count() == 4
    assert page.locator('.connector-stream').evaluate_all('(elements)=>elements.every(element=>getComputedStyle(element).animationName==="none")')
    assert page.locator('.connector-spark').evaluate_all('(elements)=>elements.every(element=>getComputedStyle(element).display==="none")')
    assert page.locator('.process-connector').first.is_visible()


def test_editor_sidebar_and_fixed_savebar_follow_current_module(ui_context, live_server):
    """验证编辑框架；参数为浏览器和服务；断言侧栏切换标题、保存入口且底栏固定可见。"""
    page = ui_context.new_page()
    page.set_viewport_size({'width': 1440, 'height': 900})
    page.goto(live_server + '/edit')
    expect(page.get_by_test_id('save-button')).to_be_enabled()
    tabs = page.get_by_role('tablist', name='编辑模块')
    expect(tabs).to_have_attribute('aria-orientation', 'vertical')
    sidebar = page.locator('.editor-sidebar').bounding_box()
    assert sidebar['x'] == 0 and sidebar['width'] == 184
    for title, action in [('库存明细', '保存库存'), ('销售出货', '保存出货'),
                          ('流程与设置', '保存设置'), ('生产日报', '保存日报')]:
        page.get_by_role('tab', name=title, exact=True).click()
        expect(page.get_by_role('heading', level=1, name=title, exact=True)).to_be_visible()
        expect(page.get_by_test_id('save-button')).to_have_text(action)
        expect(page.get_by_test_id('save-button')).to_be_enabled()
    page.evaluate('window.scrollTo(0, document.documentElement.scrollHeight)')
    footer = page.get_by_test_id('save-bar').bounding_box()
    assert footer['x'] == 184 and footer['y'] + footer['height'] == 900
    assert page.evaluate('document.documentElement.scrollWidth') == 1440


def test_editor_counts_highlights_and_discards_only_actual_changes(ui_context, live_server, client):
    """验证修改反馈和放弃；参数为页面、服务及客户端；断言计数随还原消失，取消保留、确认恢复。"""
    page = ui_context.new_page()
    page.goto(live_server + '/edit')
    expect(page.get_by_test_id('save-button')).to_be_enabled()
    fill_control(page, 'actual-1', 123)
    fill_control(page, 'defect-1', 2)
    page.get_by_role('heading', level=1).click()
    expect(page.get_by_test_id('save-status')).to_have_text('2 处修改未保存')
    changed = page.get_by_test_id('actual-1').locator('.el-input__wrapper')
    expect(changed).to_have_css('background-color', 'rgb(241, 249, 246)')
    fill_control(page, 'actual-1', '')
    fill_control(page, 'defect-1', '')
    page.get_by_role('heading', level=1).click()
    expect(page.get_by_test_id('save-status')).to_have_text('所有修改已保存')
    expect(page.get_by_role('button', name='放弃修改', exact=True)).to_be_disabled()
    fill_control(page, 'actual-1', 456)
    page.get_by_role('button', name='放弃修改', exact=True).click()
    dialog = page.get_by_role('dialog', name='未保存的修改')
    dialog.get_by_role('button', name='继续编辑', exact=True).click()
    expect(page.get_by_test_id('actual-1').locator('input')).to_have_value('456')
    expect(page.get_by_test_id('save-status')).to_have_text('1 处修改未保存')
    page.get_by_role('button', name='放弃修改', exact=True).click()
    dialog.get_by_role('button', name='放弃修改', exact=True).click()
    expect(page.get_by_test_id('save-status')).to_have_text('所有修改已保存')
    expect(page.get_by_test_id('actual-1').locator('input')).to_have_value('')
    assert client.get('/api/editor').get_json()['production']['lines'][0]['actual_qty'] is None


def test_editor_sales_share_uses_all_rows_when_searching(ui_context, live_server, client):
    """验证出货占比；参数为页面与接口；断言搜索不改变统计口径，编辑数量后占比同步计算。"""
    payload = {'version': 0, 'order_qty': 200,
               'items': [{'part_no': 'PART-A', 'quantity': 75}, {'part_no': 'PART-B', 'quantity': 25}]}
    assert client.put('/api/sales/2026-11', json=payload).status_code == 200
    page = ui_context.new_page()
    page.goto(live_server + '/edit')
    expect(page.get_by_test_id('save-button')).to_be_enabled()
    page.get_by_role('tab', name='销售出货', exact=True).click()
    share = page.get_by_role('meter', name='件号出货占比 PART-A', exact=True)
    expect(share).to_have_attribute('aria-valuetext', '75%')
    page.get_by_role('textbox', name='搜索件号', exact=True).fill('PART-A')
    expect(page.get_by_test_id('shipment-editor-table').locator('.el-table__body tbody tr')).to_have_count(1)
    expect(share).to_have_attribute('aria-valuetext', '75%')
    fill_control(page, 'shipment-quantity-0', 150)
    page.get_by_role('heading', level=1).click()
    expect(share).to_have_attribute('aria-valuetext', '85.7%')
    expect(page.get_by_test_id('save-status')).to_have_text('1 处修改未保存')
    page.get_by_test_id('save-button').click()
    expect(page.get_by_test_id('save-status')).to_have_text('所有修改已保存')
    expect(share).to_have_attribute('aria-valuetext', '85.7%')
    assert client.get('/api/dashboard').get_json()['sales']['shipped_qty'] == 175


def test_editor_save_error_is_visible_from_bottom_of_long_form(ui_context, live_server, client):
    """验证长表保存失败反馈；参数为浏览器与接口；断言底部保存后错误进入视口，原输入保留。"""
    raw = [dict(material='钢卷', warehouse='原材料仓库', specification=f'规格-{index}',
                position='', quantity=10, unit='卷') for index in range(16)]
    assert client.put('/api/inventory', json={'version': 0, 'raw': raw, 'semi': []}).status_code == 200
    page = ui_context.new_page()
    page.goto(live_server + '/edit')
    expect(page.get_by_test_id('save-button')).to_be_enabled()
    page.get_by_role('tab', name='库存明细', exact=True).click()
    fill_control(page, 'raw-spec-15', '')
    page.evaluate('window.scrollTo(0, document.documentElement.scrollHeight)')
    page.get_by_test_id('save-button').click()
    error = page.locator('.request-error')
    expect(error).to_contain_text('规格不能为空')
    expect(error).to_be_focused()
    box = error.bounding_box()
    assert 0 <= box['y'] and box['y'] + box['height'] < page.get_by_test_id('save-bar').bounding_box()['y']
    expect(page.locator('input[data-testid="raw-spec-15"], [data-testid="raw-spec-15"] input')).to_have_value('')
    expect(page.get_by_test_id('save-status')).to_have_text('1 处修改未保存')


def test_editor_inventory_counts_changes_by_record_after_deleting_rows(ui_context, live_server, client):
    """验证库存行移动后的修改计数；参数为页面与接口；断言删行不会把后续未改字段误判为修改。"""
    raw = [dict(material='钢卷', warehouse='原材料仓库', specification=f'规格-{index}',
                position='', quantity=10, unit='卷') for index in range(2)]
    assert client.put('/api/inventory', json={'version': 0, 'raw': raw, 'semi': []}).status_code == 200
    page = ui_context.new_page()
    page.goto(live_server + '/edit')
    expect(page.get_by_test_id('save-button')).to_be_enabled()
    page.get_by_role('tab', name='库存明细', exact=True).click()
    table = page.get_by_test_id('raw-editor-table')
    page.get_by_test_id('add-raw').click()
    table.get_by_role('button', name='删除', exact=True).last.click()
    expect(page.get_by_test_id('save-status')).to_have_text('所有修改已保存')
    fill_control(page, 'raw-quantity-1', 25)
    table.get_by_role('button', name='删除', exact=True).first.click()
    expect(page.get_by_test_id('save-status')).to_have_text('2 处修改未保存')
    expect(page.get_by_role('textbox', name='原材料规格', exact=True)).to_have_value('规格-1')
    unchanged = table.locator('.el-table__body td').nth(2).locator('.el-input__wrapper')
    expect(unchanged).to_have_css('background-color', 'rgb(255, 255, 255)')
    fill_control(page, 'raw-quantity-0', 10)
    page.get_by_role('heading', level=1).click()
    expect(page.get_by_test_id('save-status')).to_have_text('1 处修改未保存')


def test_editor_keyboard_module_navigation_preserves_cancelled_draft(ui_context, live_server):
    """验证侧栏键盘导航；参数为页面和服务；断言首尾键切换、取消后焦点与草稿留在原模块。"""
    page = ui_context.new_page()
    page.goto(live_server + '/edit')
    expect(page.get_by_test_id('save-button')).to_be_enabled()
    production = page.get_by_role('tab', name='生产日报', exact=True)
    employees = page.get_by_role('tab', name='员工管理', exact=True)
    production.focus()
    production.press('End')
    expect(employees).to_be_focused()
    expect(employees).to_have_attribute('aria-selected', 'true')
    employees.press('Home')
    expect(production).to_be_focused()
    fill_control(page, 'actual-1', 321)
    production.focus()
    production.press('ArrowDown')
    dialog = page.get_by_role('dialog', name='未保存的修改')
    dialog.get_by_role('button', name='继续编辑', exact=True).click()
    expect(production).to_be_focused()
    expect(production).to_have_attribute('aria-selected', 'true')
    expect(page.get_by_test_id('actual-1').locator('input')).to_have_value('321')


@pytest.mark.parametrize('width,height', [(1440, 720), (1440, 900), (1920, 1080), (375, 812)])
def test_editor_demo_layout_keeps_save_actions_and_last_row_accessible(ui_context, live_server, app, clock, width, height):
    """验证完整数据编辑布局；参数为隔离应用、时钟与尺寸；断言四模块无页面横向溢出、底栏不遮最后一行。"""
    from demo_data import apply_demo_data
    apply_demo_data(app.config['DATABASE'], clock['now'])
    page = ui_context.new_page()
    page.set_viewport_size({'width': width, 'height': height})
    page.goto(live_server + '/edit')
    expect(page.get_by_test_id('save-button')).to_be_enabled()
    Path('output/playwright').mkdir(parents=True, exist_ok=True)
    for label, module in [('生产日报', 'production'), ('库存明细', 'inventory'),
                          ('销售出货', 'sales'), ('流程与设置', 'settings')]:
        page.get_by_role('tab', name=label, exact=True).click()
        expect(page.get_by_test_id('save-button')).to_be_enabled()
        assert page.evaluate('document.documentElement.scrollWidth') == width
        assert page.evaluate('window.scrollY') == 0
        expect(page.get_by_test_id('save-button')).to_be_in_viewport()
        page.screenshot(path=f'output/playwright/editor-ui-{module}-{width}x{height}.png')
        page.evaluate('window.scrollTo(0, document.documentElement.scrollHeight)')
        footer = page.get_by_test_id('save-bar').bounding_box()
        assert abs(footer['y'] + footer['height'] - height) < 1
        assert footer['x'] == (184 if width > 800 else 0)
        last_card = page.get_by_test_id(f'{module}-panel').locator('.editor-card').last.bounding_box()
        assert last_card['y'] + last_card['height'] <= footer['y']
    expect(page.get_by_role('link', name='打开大屏', exact=True)).to_have_attribute('href', '/screen')
