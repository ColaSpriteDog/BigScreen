"""验证库存导入预览、取消、保存和用户指定的大屏字段。"""

from pathlib import Path

import pytest
from playwright.sync_api import expect

from tests.test_inventory_import import summary_rows, workbook_bytes


@pytest.mark.parametrize('width,height', [(1440, 720), (375, 812)])
def test_excel_preview_cancel_apply_save_and_screen_columns(ui_context, live_server, client, width, height):
    """验证完整导入流程；上下文、服务、客户端和尺寸为参数；断言预览不写库、取消不改草稿和精简字段。"""
    page = ui_context.new_page()
    page.set_viewport_size({'width': width, 'height': height})
    failures = []
    page.on('pageerror', lambda error: failures.append(str(error)))
    page.goto(live_server + '/edit')
    expect(page.get_by_test_id('save-button')).to_be_enabled()
    page.get_by_role('tab', name='库存明细', exact=True).click()
    expect(page.get_by_test_id('import-inventory')).to_be_enabled()
    # 先保留一条未保存输入，验证取消导入不会丢失手工草稿。
    page.get_by_test_id('add-raw').click()
    page.get_by_role('textbox', name='原材料物料', exact=True).fill('保留草稿')
    upload = {'name': '仓库日报.xlsx', 'mimeType': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
              'buffer': workbook_bytes().getvalue()}
    page.get_by_label('库存 Excel 文件', exact=True).set_input_files(upload)
    dialog = page.get_by_role('dialog', name='库存导入预览')
    expect(dialog).to_be_visible()
    expect(dialog).to_contain_text('原材料 3 条')
    expect(dialog).to_contain_text('半成品 3 条')
    expect(dialog.get_by_test_id('import-raw-preview')).to_contain_text('重量（吨）')
    expect(dialog.get_by_test_id('import-raw-preview')).to_contain_text('185.37')
    expect(dialog.get_by_test_id('import-semi-preview')).to_contain_text('件')
    assert client.get('/api/editor').get_json()['version'] == 0
    dialog.get_by_role('button', name='取消', exact=True).click()
    expect(dialog).not_to_be_visible()
    expect(page.get_by_role('textbox', name='原材料物料', exact=True)).to_have_value('保留草稿')
    page.get_by_label('库存 Excel 文件', exact=True).set_input_files(upload)
    expect(dialog).to_be_visible()
    page.get_by_test_id('apply-inventory-import').click()
    expect(dialog).not_to_be_visible()
    expect(page.get_by_test_id('save-status')).to_contain_text('处修改未保存')
    assert client.get('/api/editor').get_json()['inventory']['raw'] == []
    page.get_by_test_id('save-button').click()
    expect(page.get_by_test_id('save-status')).to_have_text('所有修改已保存')
    saved = client.get('/api/editor').get_json()
    assert len(saved['inventory']['raw']) == 3 and len(saved['inventory']['semi']) == 3
    first_id = saved['inventory']['raw'][0]['id']
    first_audit = saved['inventory']['raw'][0]['_audit']
    # 再次导入相同数据，已有标识和录入人信息不能被当成新记录。
    page.get_by_label('库存 Excel 文件', exact=True).set_input_files(upload)
    expect(dialog).to_be_visible()
    page.get_by_test_id('apply-inventory-import').click()
    expect(page.get_by_test_id('save-status')).to_have_text('所有修改已保存')
    page.get_by_test_id('save-button').click()
    expect(page.get_by_test_id('save-status')).to_have_text('所有修改已保存')
    current = client.get('/api/editor').get_json()['inventory']['raw'][0]
    assert current['id'] == first_id and current['_audit'] == first_audit
    screen = ui_context.new_page()
    screen.set_viewport_size({'width': width, 'height': height})
    screen.goto(live_server + '/screen')
    expect(screen.locator('#connectionStatus')).to_have_text('数据已同步')
    expect(screen.locator('#rawTableHeader th')).to_have_text(['产品名称', '规格型号', '剩余数量（卷）', '重量（吨）', '可切（件）'])
    expect(screen.locator('#semiTableHeader th')).to_have_text(['类别', '产品名称', '规格型号', '剩余数量（件）'])
    expect(screen.locator('#rawTableBody tr').first.locator('td')).to_have_text(['A柱', '1.6*1430/1.6*1500', '16', '185.37', '19,200'])
    expect(screen.locator('#rawTableBody tr').nth(2).locator('td')).to_have_text(['试机料', '1.2*1250', '0.5', '—', '—'])
    expect(screen.locator('#semiTableBody tr').first.locator('td')).to_have_text(['左件', '门槛左件', '1.4', '2,977'])
    expect(screen.locator('#semiTableBody .semi-group')).to_have_text(['左件', '右件'])
    assert screen.locator('#semiTableBody .warehouse-field').count() == 0
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert screen.evaluate('document.documentElement.scrollWidth <= innerWidth')
    Path('output/playwright').mkdir(parents=True, exist_ok=True)
    screen.screenshot(path=f'output/playwright/inventory-import-screen-{width}x{height}.png', full_page=True)
    assert failures == []


def test_weight_edit_uses_tonnes_and_preserves_kilogram_storage(ui_context, live_server, client):
    """验证重量输入换算；上下文、服务和客户端为参数；断言吨数保存精确、空白仍为空。"""
    response = client.post('/api/inventory/preview', data={'file': (workbook_bytes(), '日报.xlsx')})
    inventory = response.get_json()
    assert client.put('/api/inventory', json={'version': 0, 'raw': inventory['raw'], 'semi': inventory['semi']}).status_code == 200
    page = ui_context.new_page()
    page.goto(live_server + '/edit')
    expect(page.get_by_test_id('save-button')).to_be_enabled()
    page.get_by_role('tab', name='库存明细', exact=True).click()
    weights = page.get_by_role('spinbutton', name='原材料重量', exact=True)
    expect(weights.first).to_have_value('185.37000')
    weights.first.fill('185.37123')
    weights.first.press('Tab')
    page.get_by_test_id('save-button').click()
    expect(page.get_by_test_id('save-status')).to_have_text('所有修改已保存')
    stored = client.get('/api/editor').get_json()['inventory']['raw']
    assert stored[0]['weight_kg'] == 185371.23
    assert stored[1]['weight_kg'] == 0 and stored[2]['weight_kg'] is None
    weights.first.fill('')
    weights.first.press('Tab')
    page.get_by_test_id('save-button').click()
    expect(page.get_by_test_id('save-status')).to_have_text('所有修改已保存')
    assert client.get('/api/editor').get_json()['inventory']['raw'][0]['weight_kg'] is None


def test_failed_import_keeps_draft_and_reports_source_row(ui_context, live_server):
    """验证失败导入保留输入；参数为上下文和服务；断言源行错误可见、原草稿仍可编辑。"""
    page = ui_context.new_page()
    page.goto(live_server + '/edit')
    expect(page.get_by_test_id('save-button')).to_be_enabled()
    page.get_by_role('tab', name='库存明细', exact=True).click()
    page.get_by_test_id('add-raw').click()
    page.get_by_role('textbox', name='原材料物料', exact=True).fill('待保存草稿')
    rows = summary_rows()
    rows[4][6] = -1
    page.get_by_label('库存 Excel 文件', exact=True).set_input_files({'name': '错误日报.xlsx',
        'mimeType': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'buffer': workbook_bytes(rows).getvalue()})
    expect(page.locator('.request-error')).to_contain_text('第 5 行')
    expect(page.get_by_role('textbox', name='原材料物料', exact=True)).to_have_value('待保存草稿')
    expect(page.get_by_role('dialog', name='库存导入预览')).not_to_be_visible()
    expect(page.get_by_test_id('save-button')).to_be_enabled()
