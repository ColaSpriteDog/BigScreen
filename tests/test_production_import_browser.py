"""验证生产导入界面与大屏单位分组，使用隔离数据库和内存上传。"""

from pathlib import Path

import pytest
from playwright.sync_api import expect

from tests.test_api import settings_payload
from tests.test_production_import import production_workbook


@pytest.mark.parametrize('width,height', [(1440, 720), (375, 812)])
def test_production_preview_cancel_save_and_mixed_units(ui_context, live_server, client, width, height):
    """验证导入全链路；参数为界面、接口和尺寸；断言预览不写库、取消保护、保存、单位及重复导入。"""
    page = ui_context.new_page()
    page.set_viewport_size({'width': width, 'height': height})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(live_server + '/edit')
    expect(page.get_by_test_id('save-button')).to_be_enabled()
    expect(page.get_by_label('生产月份', exact=True)).to_have_value('2026-11')
    upload = {'name': '生产日报.xlsx', 'mimeType': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
              'buffer': production_workbook().getvalue()}
    page.get_by_label('生产日报 Excel 文件', exact=True).set_input_files(upload)
    dialog = page.get_by_role('dialog', name='生产日报导入预览', exact=True)
    expect(dialog).to_be_visible()
    expect(page.get_by_test_id('production-import-preview').locator('.el-table__body tbody tr')).to_have_count(4)
    expect(page.get_by_test_id('save-production-import')).to_be_enabled()
    assert client.get('/api/dashboard').get_json()['version'] == 0
    Path('output/playwright').mkdir(parents=True, exist_ok=True)
    page.wait_for_function("document.getAnimations().every(animation=>animation.playState!=='running')")
    page.screenshot(path=f'output/playwright/production-import-preview-{width}.png', full_page=True)
    dialog.get_by_role('button', name='取消', exact=True).click()
    expect(dialog).not_to_be_visible()
    assert client.get('/api/dashboard').get_json()['version'] == 0
    page.get_by_label('生产日报 Excel 文件', exact=True).set_input_files(upload)
    expect(dialog).to_be_visible()
    page.get_by_test_id('save-production-import').click()
    expect(dialog).not_to_be_visible()
    expect(page.get_by_test_id('save-status')).to_have_text('所有修改已保存')
    first = client.get('/api/editor?date=2026-11-01').get_json()['production']['lines']
    assert first[0]['actual_qty'] == 100.125
    assert [line['standard_qty'] for line in first] == [500, 1500, 1000, 1000]
    config = settings_payload(client)
    for line in config['lines']:
        line['show_in_trend'] = True
    assert client.put('/api/settings', json=config).status_code == 200
    screen = ui_context.new_page()
    screen.set_viewport_size({'width': width, 'height': height})
    screen.on('pageerror', lambda error: errors.append(str(error)))
    screen.goto(live_server + '/screen')
    expect(screen.locator('#connectionStatus')).to_have_text('数据已同步')
    expect(screen.locator('#trendTotal')).to_have_text('本月累计：100.13（米） / 300（件）')
    expect(screen.locator('.line-card[data-line-id="1"] .data-list > div').nth(2)).to_have_text('本月产量（米）100.13')
    chart = screen.evaluate("echarts.getInstanceByDom(document.querySelector('#newTrendChart')).getOption()")
    assert [axis['name'] for axis in chart['yAxis']] == ['产量（米）', '产量（件）']
    assert len(chart['series']) == 6
    assert screen.evaluate('document.documentElement.scrollWidth <= innerWidth')
    screen.screenshot(path=f'output/playwright/production-import-units-{width}.png', full_page=width < 1201)
    first = client.get('/api/editor?date=2026-11-01').get_json()['production']['lines']
    page.reload()
    expect(page.get_by_test_id('save-button')).to_be_enabled()
    page.get_by_label('生产日报 Excel 文件', exact=True).set_input_files(upload)
    expect(dialog).to_be_visible()
    page.get_by_test_id('save-production-import').click()
    expect(dialog).not_to_be_visible()
    assert client.get('/api/editor?date=2026-11-01').get_json()['production']['lines'] == first
    assert not errors, errors


@pytest.mark.parametrize('width,height', [(1440, 720), (375, 812)])
def test_real_file_preview_scroll_and_standard_update(ui_context, live_server, client, width, height):
    """验证原表长预览；参数为隔离环境与视口；断言四线标准、末行可达及保存后图表单位正确。"""
    source = next((Path(__file__).resolve().parents[1] / 'docs').glob('5.*.xlsx'), None)
    if source is None:
        pytest.skip('当前环境没有用户提供的生产日报')
    page = ui_context.new_page()
    page.set_viewport_size({'width': width, 'height': height})
    requests = []
    page.on('request', lambda request: requests.append((request.method, request.url)))
    page.goto(live_server + '/edit')
    expect(page.get_by_test_id('save-button')).to_be_enabled()
    expect(page.get_by_test_id('import-warehouse')).not_to_be_visible()
    expect(page.get_by_role('button', name='导入生产日报推移图', exact=True)).to_be_visible()
    page.get_by_label('生产日报 Excel 文件', exact=True).set_input_files(str(source))
    dialog = page.get_by_role('dialog', name='生产日报导入预览', exact=True)
    expect(dialog).to_be_visible()
    rows = page.get_by_test_id('production-import-preview').locator('.el-table__body tbody tr')
    expect(rows).to_have_count(48)
    expect(page.get_by_test_id('production-standard-preview')).to_contain_text('18,000')
    expect(page.get_by_test_id('production-standard-preview')).to_contain_text('1,420')
    assert client.get('/api/dashboard').get_json()['version'] == 0
    rows.last.scroll_into_view_if_needed()
    expect(rows.last).to_be_in_viewport()
    save = page.get_by_test_id('save-production-import')
    save.scroll_into_view_if_needed()
    expect(save).to_be_in_viewport()
    Path('output/playwright').mkdir(parents=True, exist_ok=True)
    page.screenshot(path=f'output/playwright/production-import-real-preview-{width}.png', full_page=True)
    save.click()
    expect(dialog).not_to_be_visible()
    posts = [url for method, url in requests if method == 'POST' and '/api/' in url]
    assert posts == [live_server + '/api/production/2026-11/preview', live_server + '/api/production/2026-11/import']
    assert [line['standard_qty'] for line in client.get('/api/dashboard').get_json()['lines']] == [18000, 1420, 1000, 1000]
    config = settings_payload(client)
    for line in config['lines']:
        line['show_in_trend'] = True
    assert client.put('/api/settings', json=config).status_code == 200
    page.goto(live_server + '/screen')
    expect(page.locator('#connectionStatus')).to_have_text('数据已同步')
    expect(page.locator('#trendTotal')).to_have_text('本月累计：233,939.01（米） / 37,852（件）')
    page.screenshot(path=f'output/playwright/production-import-real-chart-{width}.png', full_page=width < 1201)
