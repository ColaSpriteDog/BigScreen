"""验证整月同步预览、取消和大屏刷新，覆盖桌面与手机视口。"""

from pathlib import Path

import pytest
from playwright.sync_api import expect

from tests.test_production_import import preview, production_workbook
from tests.test_production_month_sync import clear_source, sync


@pytest.mark.parametrize('width,height', [(1440, 720), (375, 812)])
def test_month_sync_preview_cancel_and_screen_update(ui_context, live_server, client, width, height):
    """验证 B 线删除；参数为隔离界面、服务、客户端与视口；断言清空可见、取消无写入、保存后大屏同步。"""
    assert sync(client, preview(client).get_json()).status_code == 200
    page = ui_context.new_page()
    page.set_viewport_size({'width': width, 'height': height})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(live_server + '/edit')
    expect(page.get_by_test_id('save-button')).to_be_enabled()
    screen = ui_context.new_page()
    screen.set_viewport_size({'width': width, 'height': height})
    screen.on('pageerror', lambda error: errors.append(str(error)))
    screen.goto(live_server + '/screen')
    b_output = screen.locator('.line-card[data-line-id="4"] .data-list > div').nth(2)
    expect(b_output).to_have_text('本月产量（件）100')
    upload = {'name': '清空B线.xlsx', 'mimeType': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
              'buffer': production_workbook(clear_source).getvalue()}
    page.get_by_label('生产日报 Excel 文件', exact=True).set_input_files(upload)
    dialog = page.get_by_role('dialog', name='生产日报导入预览', exact=True)
    expect(dialog).to_be_visible()
    expect(page.get_by_test_id('production-import-summary')).to_have_text('新增 0 条 · 更新 0 条 · 清空 1 条 · 不变 3 条')
    clear_row = page.get_by_test_id('production-import-preview').locator('.el-table__body tbody tr').filter(has_text='清空')
    expect(clear_row).to_contain_text('2026-11-01')
    expect(clear_row).to_contain_text('100')
    assert client.get('/api/dashboard').get_json()['lines'][3]['monthly_qty'] == 100
    dialog.get_by_role('button', name='取消', exact=True).click()
    expect(dialog).not_to_be_visible()
    assert client.get('/api/dashboard').get_json()['version'] == 1
    page.get_by_label('生产日报 Excel 文件', exact=True).set_input_files(upload)
    expect(dialog).to_be_visible()
    Path('output/playwright').mkdir(parents=True, exist_ok=True)
    page.wait_for_function("document.getAnimations().every(animation=>animation.playState!=='running')")
    page.screenshot(path=f'output/playwright/production-month-sync-{width}.png', full_page=True)
    page.get_by_test_id('save-production-import').click()
    expect(dialog).not_to_be_visible()
    expect(b_output).to_have_text('本月产量（件）—', timeout=10000)
    expect(screen.locator('#trendTotal')).to_have_text('本月累计：100（件）')
    assert client.get('/api/dashboard').get_json()['lines'][3]['monthly_planned_hours'] is None
    assert not errors, errors


def test_all_blank_month_can_be_saved_in_ui(ui_context, live_server, client):
    """验证空月保存入口；参数为隔离界面、服务和客户端；断言没有源日报时仍可预览四条清空项并保存。"""
    assert sync(client, preview(client).get_json()).status_code == 200
    page = ui_context.new_page()
    page.goto(live_server + '/edit')
    expect(page.get_by_test_id('save-button')).to_be_enabled()
    upload = {'name': '空白整月.xlsx', 'mimeType': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
              'buffer': production_workbook(lambda book: clear_source(book, (1, 2, 3, 4))).getvalue()}
    page.get_by_label('生产日报 Excel 文件', exact=True).set_input_files(upload)
    dialog = page.get_by_role('dialog', name='生产日报导入预览', exact=True)
    expect(dialog).to_be_visible()
    expect(page.get_by_test_id('production-import-summary')).to_have_text('新增 0 条 · 更新 0 条 · 清空 4 条 · 不变 0 条')
    expect(page.get_by_test_id('save-production-import')).to_be_enabled()
    page.get_by_test_id('save-production-import').click()
    expect(dialog).not_to_be_visible()
    assert all(line['monthly_qty'] is None for line in client.get('/api/dashboard').get_json()['lines'])
