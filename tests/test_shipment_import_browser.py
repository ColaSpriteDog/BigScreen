"""验证出货逐条导入、取消保护、重复导入及大屏按件号汇总排名。"""

from pathlib import Path

import pytest
from playwright.sync_api import expect

from tests.test_shipment_import import shipment_rows
from tests.test_warehouse_import import warehouse_workbook


@pytest.mark.parametrize('width,height', [(1440, 720), (1920, 1080), (375, 812)])
def test_import_each_shipment_and_rank_monthly_parts(ui_context, live_server, client, width, height):
    """验证完整出货链路；参数为浏览器、接口与尺寸；断言明细逐条保存、重复不累加、汇总排名持久。"""
    assert client.put('/api/sales/2026-11', json={'version': 0, 'order_qty': 50000,
        'items': [{'part_no': 'OLD', 'name': '原草稿', 'quantity': 10, 'shipment_date': '2026-11-01'}]}).status_code == 200
    rows = shipment_rows()[:8]
    rows += [['2026-11-17', '客户', 'LP000366', '门环右件', index, 0] for index in range(16)]
    upload = {'name': '仓库日报.xlsx', 'mimeType': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
              'buffer': warehouse_workbook(sales_rows=rows).getvalue()}
    page = ui_context.new_page()
    page.set_viewport_size({'width': width, 'height': height})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(live_server + '/edit')
    expect(page.get_by_test_id('save-button')).to_be_enabled()
    page.get_by_role('tab', name='销售出货', exact=True).click()
    expect(page.get_by_test_id('import-warehouse')).to_be_enabled()
    page.get_by_label('仓库日报 Excel 文件', exact=True).set_input_files(upload)
    dialog = page.get_by_role('dialog', name='仓库日报导入预览', exact=True)
    expect(dialog).to_be_visible()
    expect(page.get_by_test_id('import-shipment-preview').locator('.el-table__body tbody tr')).to_have_count(20)
    assert client.get('/api/dashboard').get_json()['sales']['shipped_qty'] == 10
    dialog.get_by_role('button', name='取消', exact=True).click()
    expect(dialog).not_to_be_visible()
    expect(page.locator('input[data-testid="part-0"], [data-testid="part-0"] input')).to_have_value('OLD')
    page.get_by_label('仓库日报 Excel 文件', exact=True).set_input_files(upload)
    expect(dialog).to_be_visible()
    page.get_by_test_id('confirm-warehouse-import').click()
    expect(dialog).not_to_be_visible()
    expect(page.get_by_test_id('save-status')).to_have_text('所有修改已保存')
    saved = client.get('/api/editor').get_json()['sales']
    assert saved['order_qty'] == 50000 and len(saved['items']) == 20
    assert len(client.get('/api/editor').get_json()['inventory']['raw']) == 3
    assert [row['quantity'] for row in saved['items'][:4]] == [800, 800, 400, 0]
    assert saved['items'][0]['id'] != saved['items'][1]['id']
    page.get_by_label('仓库日报 Excel 文件', exact=True).set_input_files(upload)
    expect(dialog).to_be_visible()
    page.get_by_test_id('confirm-warehouse-import').click()
    expect(dialog).not_to_be_visible()
    expect(page.get_by_test_id('save-status')).to_have_text('所有修改已保存')
    assert client.get('/api/editor').get_json()['sales'] == saved
    screen = ui_context.new_page()
    screen.set_viewport_size({'width': width, 'height': height})
    screen.on('pageerror', lambda error: errors.append(str(error)))
    screen.goto(live_server + '/screen')
    expect(screen.locator('#connectionStatus')).to_have_text('数据已同步')
    expect(screen.locator('.shipment-panel')).to_contain_text('TOP 8 型号')
    chart = screen.evaluate("echarts.getInstanceByDom(document.querySelector('#shipmentChart')).getOption()")
    assert chart['yAxis'][0]['data'] == ['LP000366', 'LP000365']
    assert chart['series'][0]['data'] == [1720, 400]
    # 检查真实图表的标签与提示，确保汇总后的名称传到渲染层。
    label = screen.evaluate("echarts.getInstanceByDom(document.querySelector('#shipmentChart')).getOption().yAxis[0].axisLabel.formatter('LP000366', 0)")
    assert label == '{leading|01}{part|LP000366}\n{indent| }{detail|门环右件}'
    tooltip = screen.evaluate("echarts.getInstanceByDom(document.querySelector('#shipmentChart')).getOption().tooltip[0].formatter([{name:'LP000366',dataIndex:0,value:1720}])")
    assert '门环右件' in tooltip
    expect(screen.locator('#shippedQty')).to_have_text('2,120')
    # 轮询复用图表实例，汇总展示不会改写逐条源记录。
    screen.evaluate('window.__shipmentChart=echarts.getInstanceByDom(document.querySelector("#shipmentChart"))')
    with screen.expect_response('**/api/dashboard'):
        screen.wait_for_timeout(5100)
    assert screen.evaluate('window.__shipmentChart === echarts.getInstanceByDom(document.querySelector("#shipmentChart"))')
    assert screen.evaluate('document.documentElement.scrollWidth <= innerWidth')
    screen.reload()
    expect(screen.locator('#connectionStatus')).to_have_text('数据已同步')
    assert screen.evaluate("echarts.getInstanceByDom(document.querySelector('#shipmentChart')).getOption().series[0].data") == [1720, 400]
    assert client.get('/api/editor').get_json()['sales'] == saved
    Path('output/playwright').mkdir(parents=True, exist_ok=True)
    screen.screenshot(path=f'output/playwright/shipment-ranking-{width}x{height}.png', full_page=width < 1201)
    assert not errors, errors


@pytest.mark.parametrize('width,height', [(1440, 720), (375, 812)])
def test_shipment_top_eight_preserves_complete_monthly_total(ui_context, live_server, client, width, height):
    """验证前八图表；参数为隔离环境和尺寸；断言重复件号汇总、降序前八和全部明细累计。"""
    items = [{'part_no': f'P-{index:02}', 'name': f'门环左件型号{index:02}', 'quantity': index * 100} for index in range(1, 11)]
    items.append({'part_no': 'P-01', 'quantity': 2000})
    assert client.put('/api/sales/2026-11', json={'version': 0, 'order_qty': 10000, 'items': items}).status_code == 200
    screen = ui_context.new_page()
    screen.set_viewport_size({'width': width, 'height': height})
    screen.goto(live_server + '/screen')
    expect(screen.locator('#connectionStatus')).to_have_text('数据已同步')
    expect(screen.locator('#shippedQty')).to_have_text('7,500')
    expect(screen.locator('#shipmentRate')).to_have_text('75%')
    chart = screen.evaluate("echarts.getInstanceByDom(document.querySelector('#shipmentChart')).getOption()")
    assert chart['yAxis'][0]['inverse'] is True
    assert chart['yAxis'][0]['data'] == ['P-01', 'P-10', 'P-09', 'P-08', 'P-07', 'P-06', 'P-05', 'P-04']
    assert chart['series'][0]['data'] == [2100, 1000, 900, 800, 700, 600, 500, 400]
    assert len(client.get('/api/editor').get_json()['sales']['items']) == 11
    assert screen.evaluate('document.documentElement.scrollWidth <= innerWidth')
    Path('output/playwright').mkdir(parents=True, exist_ok=True)
    screen.screenshot(path=f'output/playwright/shipment-top-eight-{width}.png', full_page=width < 1201)
