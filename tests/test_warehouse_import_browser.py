"""验证统一导入入口、一次选择两表填充、取消及失败时保留草稿。"""

from pathlib import Path

import pytest
from playwright.sync_api import expect

from tests.test_warehouse_import import warehouse_workbook


def warehouse_upload():
    """构造一次文件选择；无参数；返回包含库存和出货两表的浏览器上传载荷。"""
    return {'name': '仓库日报.xlsx', 'mimeType': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'buffer': warehouse_workbook().getvalue()}


@pytest.mark.parametrize('width,height', [(1440, 720), (375, 812)])
def test_single_global_upload_and_confirm_populates_both_modules(ui_context, live_server, client, width, height):
    """验证单入口导入；参数为浏览器、服务和客户端；断言一次上传与一次确认填充两模块且切换无需再保存。"""
    page = ui_context.new_page()
    page.set_viewport_size({'width': width, 'height': height})
    requests = []
    page.on('request', lambda request: requests.append((request.method, request.url)))
    page.goto(live_server + '/edit')
    expect(page.get_by_test_id('save-button')).to_be_enabled()
    expect(page.get_by_test_id('import-warehouse')).not_to_be_visible()
    page.get_by_role('tab', name='库存明细', exact=True).click()
    expect(page.get_by_test_id('import-warehouse')).to_be_enabled()
    assert page.get_by_test_id('import-inventory').count() == 0
    assert page.get_by_test_id('import-shipments').count() == 0
    expect(page.locator('.warehouse-import-toolbar input').first).to_have_value('2026 年 11 月')
    page.get_by_label('仓库日报 Excel 文件', exact=True).set_input_files(warehouse_upload())
    dialog = page.get_by_role('dialog', name='仓库日报导入预览', exact=True)
    expect(dialog).to_be_visible()
    expect(dialog.get_by_test_id('import-raw-preview').locator('.el-table__body tbody tr')).to_have_count(3)
    expect(dialog.get_by_test_id('import-semi-preview').locator('.el-table__body tbody tr')).to_have_count(3)
    expect(dialog.get_by_test_id('import-shipment-preview').locator('.el-table__body tbody tr')).to_have_count(4)
    assert client.get('/api/editor').get_json()['version'] == 0
    expect(page.get_by_test_id('confirm-warehouse-import')).to_be_in_viewport()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    Path('output/playwright').mkdir(parents=True, exist_ok=True)
    page.screenshot(path=f'output/playwright/warehouse-unified-preview-{width}.png', animations='disabled')
    page.get_by_test_id('confirm-warehouse-import').click()
    expect(dialog).not_to_be_visible()
    assert client.get('/api/editor').get_json()['version'] == 1
    for name, table, count in [('库存明细', 'raw-editor-table', 3), ('销售出货', 'shipment-editor-table', 4)]:
        page.get_by_role('tab', name=name, exact=True).click()
        expect(page.get_by_test_id('save-button')).to_be_enabled()
        expect(page.get_by_test_id(table).locator('.el-table__body tbody tr')).to_have_count(count)
        expect(page.get_by_test_id('save-status')).to_have_text('所有修改已保存')
    posts = [url for method, url in requests if method == 'POST' and '/api/warehouse/' in url]
    assert posts == [live_server + '/api/warehouse/2026-11/preview', live_server + '/api/warehouse/2026-11/import']
    page.reload()
    expect(page.get_by_test_id('save-button')).to_be_enabled()
    assert len(client.get('/api/editor').get_json()['sales']['items']) == 4


def test_conflict_keeps_both_business_tables_and_unsaved_inventory_draft(ui_context, live_server, client):
    """验证统一导入冲突；参数为浏览器、服务和客户端；断言失败保留预览、原草稿及两类数据。"""
    page = ui_context.new_page()
    page.goto(live_server + '/edit')
    expect(page.get_by_test_id('save-button')).to_be_enabled()
    page.get_by_role('tab', name='库存明细', exact=True).click()
    expect(page.get_by_test_id('import-warehouse')).to_be_enabled()
    page.get_by_test_id('add-raw').click()
    page.get_by_role('textbox', name='原材料物料', exact=True).fill('待保存草稿')
    page.get_by_label('仓库日报 Excel 文件', exact=True).set_input_files(warehouse_upload())
    dialog = page.get_by_role('dialog', name='仓库日报导入预览', exact=True)
    expect(dialog).to_be_visible()
    assert client.put('/api/sales/2026-11', json={'version': 0, 'order_qty': 12, 'items': []}).status_code == 200
    page.get_by_test_id('confirm-warehouse-import').click()
    expect(dialog.get_by_text('数据已被其他操作更新，请重新加载后保存', exact=True)).to_be_visible()
    saved = client.get('/api/editor').get_json()
    assert saved['version'] == 1 and saved['inventory']['raw'] == [] and saved['sales']['items'] == []
    dialog.get_by_role('button', name='取消', exact=True).click()
    expect(dialog).not_to_be_visible()
    expect(page.get_by_role('textbox', name='原材料物料', exact=True)).to_have_value('待保存草稿')
