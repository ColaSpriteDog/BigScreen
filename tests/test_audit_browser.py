"""验证编辑页只读人员信息、草稿状态和桌面及手机布局。"""

from pathlib import Path

import pytest
from playwright.sync_api import expect

from tests.test_api import production_payload, settings_payload
from tests.test_audit import inventory_example, save_inventory


def test_inventory_attribution_tooltip_and_unsaved_copy(ui_context, live_server, client):
    """验证表格人员展示；上下文、服务及客户端为参数；断言详情键盘可访问，复制及新增行不继承原归属。"""
    assert save_inventory(client, inventory_example()).status_code == 200
    page = ui_context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(live_server + '/edit')
    expect(page.get_by_test_id('save-button')).to_be_enabled()
    page.get_by_role('tab', name='库存明细', exact=True).click()
    table = page.get_by_test_id('raw-editor-table')
    meta = table.locator('.el-table__body tr').first.locator('.record-audit')
    expect(meta).to_contain_text('管理员')
    meta.focus()
    expect(page.get_by_role('tooltip')).to_contain_text('录入：管理员')
    expect(page.get_by_role('tooltip')).to_contain_text('修改：管理员')
    assert '2026' in meta.get_attribute('aria-label')
    table.get_by_role('button', name='复制', exact=True).first.click()
    expect(table.locator('.el-table__body tr').nth(1).locator('.record-audit')).to_have_text('未保存')
    expect(page.get_by_test_id('save-status')).to_have_text('1 处修改未保存')
    table.locator('.el-table__body tr').nth(1).get_by_role('button', name='删除', exact=True).click()
    expect(page.get_by_test_id('save-status')).to_have_text('所有修改已保存')
    page.get_by_test_id('add-raw').click()
    expect(table.locator('.el-table__body tr').last.locator('.record-audit')).to_have_text('未保存')
    assert errors == []


@pytest.mark.parametrize('width,height', [(1440, 900), (375, 812)])
def test_attribution_all_modules_and_responsive_layout(ui_context, live_server, client, width, height):
    """验证各模块归属布局；上下文、服务、客户端及尺寸为参数；断言人员只读、业务时区正确且无页面溢出。"""
    inventory = inventory_example()
    inventory['semi'] = [{'category': '左件', 'material': '门槛', 'warehouse': '半成品仓库', 'specification': '1.4', 'quantity': 2, 'unit': '个'}]
    assert save_inventory(client, inventory).status_code == 200
    production = production_payload(client)
    production['lines'][0]['actual_qty'] = 100
    production['lines'][0]['abnormal_hours'] = 1
    production['downtimes'] = [{'line_id': 1, 'start_at': '2026-11-21T10:00:00', 'end_at': '2026-11-21T11:00:00', 'kind': 'stopped'}]
    assert client.put('/api/production/2026-11-21', json=production).status_code == 200
    version = client.get('/api/editor').get_json()['version']
    assert client.put('/api/sales/2026-11', json={'version': version, 'order_qty': 1000, 'items': [{'part_no': 'A01', 'quantity': 100}]}).status_code == 200
    settings = settings_payload(client)
    settings['timezone'] = 'Asia/Shanghai'
    settings['lines'][0]['name'] = '测试产线'
    settings['processes'][0]['manual_status'] = 'maintenance'
    assert client.put('/api/settings', json=settings).status_code == 200
    page = ui_context.new_page()
    page.set_viewport_size({'width': width, 'height': height})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(live_server + '/edit')
    expect(page.get_by_test_id('save-button')).to_be_enabled()
    output = Path('output/playwright')
    output.mkdir(parents=True, exist_ok=True)
    for label, module in [('生产日报', 'production'), ('库存明细', 'inventory'), ('销售出货', 'sales'), ('流程与设置', 'settings'), ('员工管理', 'employees')]:
        page.get_by_role('tab', name=label, exact=True).click()
        expect(page.get_by_test_id('save-button')).to_be_enabled()
        panel = page.get_by_test_id(module + '-panel')
        expect(panel.locator('.record-audit').first).to_be_attached()
        assert page.evaluate('document.documentElement.scrollWidth') == width
        expect(page.get_by_test_id('save-button')).to_be_in_viewport()
        page.screenshot(path=str(output / f'editor-audit-{module}-{width}.png'), full_page=True)
        if module == 'production':
            expect(panel.get_by_test_id('production-table').locator('.record-audit').first).to_contain_text('管理员')
        if module == 'sales':
            expect(panel.locator('.period-toolbar .record-audit')).to_contain_text('订单最后修改：管理员')
        if module == 'settings':
            expect(panel.locator('.section-toolbar .record-audit').first).to_contain_text('最后修改：管理员')
            expect(panel.locator('.process-attribution .record-audit').first).to_contain_text('管理员')
        expect(page.get_by_test_id('save-status')).to_have_text('所有修改已保存')
    assert errors == []
