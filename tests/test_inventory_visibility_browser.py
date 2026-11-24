"""验证原材料开关的真实界面交互、保存联动与日报导入保留。"""

from pathlib import Path

from playwright.sync_api import expect

from tests.test_inventory_visibility import seed_inventory
from tests.test_warehouse_import_browser import warehouse_upload


def test_material_visibility_save_refresh_and_import(ui_context, live_server, client):
    """验证逐行控制；参数为浏览器、服务和客户端；断言草稿、自动同步、刷新及重新导入行为。"""
    seed_inventory(client)
    page = ui_context.new_page()
    screen = ui_context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    screen.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(live_server + '/edit')
    expect(page.get_by_test_id('save-button')).to_be_enabled()
    page.get_by_role('tab', name='库存明细', exact=True).click()
    toggles = page.get_by_role('switch', name='原材料大屏显示', exact=True)
    expect(toggles).to_have_count(3)
    for toggle in toggles.all():
        expect(toggle).to_be_checked()
    screen.goto(live_server + '/screen')
    expect(screen.locator('#rawTableBody tr')).to_have_count(3)
    page.get_by_test_id('raw-visible-0').click()
    expect(page.get_by_test_id('save-status')).to_contain_text('1')
    assert len(client.get('/api/dashboard').get_json()['inventory']['raw']) == 3
    page.get_by_test_id('save-button').click()
    expect(page.get_by_test_id('save-status')).to_have_text('所有修改已保存')
    expect(screen.locator('#rawTableBody tr')).to_have_count(2, timeout=10000)
    expect(screen.locator('#rawTableBody')).not_to_contain_text('1.6*1430')
    expect(screen.locator('#rawTotal')).to_have_text('0.5（卷）')
    expect(screen.locator('#rawWeight')).to_have_text('0（吨）')
    page.reload()
    expect(page.get_by_test_id('save-button')).to_be_enabled()
    page.get_by_role('tab', name='库存明细', exact=True).click()
    expect(toggles.nth(0)).not_to_be_checked()
    # 同一份仓库日报重复导入不能把隐藏物料重新打开。
    expect(page.get_by_test_id('import-warehouse')).to_be_enabled()
    page.get_by_label('仓库日报 Excel 文件', exact=True).set_input_files(warehouse_upload())
    dialog = page.get_by_role('dialog', name='仓库日报导入预览', exact=True)
    expect(dialog).to_be_visible()
    page.get_by_test_id('confirm-warehouse-import').click()
    expect(dialog).not_to_be_visible()
    expect(toggles.nth(0)).not_to_be_checked()
    assert client.get('/api/editor').get_json()['inventory']['raw'][0]['show_on_screen'] is False
    Path('output/playwright').mkdir(parents=True, exist_ok=True)
    page.screenshot(path='output/playwright/inventory-visibility-editor.png', full_page=True)
    screen.screenshot(path='output/playwright/inventory-visibility-screen.png')
    page.get_by_test_id('raw-visible-0').click()
    page.get_by_test_id('save-button').click()
    expect(page.get_by_test_id('save-status')).to_have_text('所有修改已保存')
    expect(screen.locator('#rawTableBody tr')).to_have_count(3, timeout=10000)
    page.get_by_test_id('add-raw').click()
    expect(toggles.nth(3)).to_be_checked()
    assert not errors, errors
