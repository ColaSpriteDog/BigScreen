"""通过真实浏览器验证登录和员工表单。"""

from pathlib import Path

import pytest
from playwright.sync_api import expect

from tests.test_auth import add_employee, employee_payload


@pytest.mark.parametrize('width,height', [(1440, 900), (375, 812)])
def test_login_page_responsive_and_rejects_wrong_password(browser, live_server, width, height):
    """验证登录页与响应布局；browser、live_server、尺寸为参数；断言错误保留输入、密码显示及正确登录。"""
    with browser.new_context(viewport={'width': width, 'height': height}) as context:
        page = context.new_page()
        page.goto(live_server + '/edit')
        expect(page).to_have_url(live_server + '/login')
        Path('output/playwright').mkdir(parents=True, exist_ok=True)
        page.screenshot(path=f'output/playwright/login-{width}.png', full_page=True)
        page.get_by_label('登录账号', exact=True).fill('admin')
        page.get_by_label('密码', exact=True).fill('wrong')
        page.get_by_role('button', name='登录', exact=True).click()
        expect(page.get_by_role('alert')).to_contain_text('账号或密码错误')
        expect(page.get_by_label('登录账号', exact=True)).to_have_value('admin')
        page.get_by_label('密码', exact=True).fill('admin123')
        page.get_by_role('button', name='显示密码').click()
        expect(page.get_by_label('密码', exact=True)).to_have_attribute('type', 'text')
        assert page.evaluate('document.documentElement.scrollWidth') == width
        page.get_by_role('button', name='登录', exact=True).click()
        expect(page.get_by_test_id('save-button')).to_be_enabled()
        page.get_by_role('button', name='退出登录', exact=True).click()
        expect(page).to_have_url(live_server + '/login')
        page.goto(live_server + '/edit')
        expect(page).to_have_url(live_server + '/login')


def test_employee_management_create_edit_disable_delete(ui_context, live_server):
    """验证员工管理流程；ui_context 与服务为参数；断言新增可登录、改密停用、删除及当前账号保护。"""
    page = ui_context.new_page()
    page.goto(live_server + '/edit')
    expect(page.get_by_test_id('save-button')).to_be_enabled()
    page.get_by_role('tab', name='员工管理', exact=True).click()
    expect(page.get_by_test_id('save-button')).to_be_enabled()
    expect(page.get_by_role('switch', name='启用员工 1', exact=True)).to_be_disabled()
    expect(page.get_by_text('当前账号', exact=True)).to_be_visible()
    page.get_by_test_id('add-employee').click()
    page.get_by_label('员工姓名 2', exact=True).fill('张三')
    page.get_by_label('登录账号 2', exact=True).fill('worker')
    page.get_by_label('员工密码 2', exact=True).fill('plain123')
    page.get_by_test_id('save-button').click()
    expect(page.get_by_test_id('save-status')).to_have_text('所有修改已保存')
    expect(page.get_by_label('员工密码 2', exact=True)).to_have_value('')
    page.reload()
    expect(page.get_by_test_id('save-button')).to_be_enabled()
    page.get_by_role('tab', name='员工管理', exact=True).click()
    expect(page.get_by_label('员工姓名 2', exact=True)).to_have_value('张三')
    page.get_by_label('员工姓名 2', exact=True).fill('李四')
    page.get_by_label('员工密码 2', exact=True).fill('new123')
    page.get_by_test_id('save-button').click()
    expect(page.get_by_test_id('save-status')).to_have_text('所有修改已保存')
    page.get_by_role('switch', name='启用员工 2', exact=True).locator('..').click()
    page.get_by_test_id('save-button').click()
    expect(page.get_by_test_id('save-status')).to_have_text('所有修改已保存')
    Path('output/playwright').mkdir(parents=True, exist_ok=True)
    expect(page.locator('.el-message')).to_have_count(0)
    page.screenshot(path='output/playwright/employees-1440.png', full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth') == 1440
    page.set_viewport_size({'width': 375, 'height': 812})
    assert page.evaluate('document.documentElement.scrollWidth') == 375
    tab_tops = page.get_by_role('tab').evaluate_all('(items)=>items.map(item=>item.getBoundingClientRect().top)')
    assert max(tab_tops) - min(tab_tops) < 1
    expect(page.get_by_test_id('save-button')).to_be_in_viewport()
    page.screenshot(path='output/playwright/employees-375.png', full_page=True)
    page.set_viewport_size({'width': 1440, 'height': 900})
    page.get_by_role('button', name='删除员工 2', exact=True).click()
    page.get_by_role('dialog', name='删除员工').get_by_role('button', name='删除', exact=True).click()
    page.get_by_test_id('save-button').click()
    expect(page.get_by_test_id('save-status')).to_have_text('所有修改已保存')
    expect(page.get_by_test_id('employees-table').locator('.el-table__body tr')).to_have_count(1)


def test_expired_session_retains_unsaved_draft(browser, live_server, client):
    """验证会话失效草稿；browser、服务和管理客户端为参数；断言停用后保存失败保留字段并显示登录入口。"""
    add_employee(client)
    with browser.new_context() as context:
        context.request.post(live_server + '/api/auth/login', data={'username': 'worker', 'password': 'plain123'})
        page = context.new_page()
        page.goto(live_server + '/edit')
        expect(page.get_by_test_id('save-button')).to_be_enabled()
        page.get_by_test_id('actual-1').locator('input').fill('321')
        page.get_by_role('heading', name='生产日报', exact=True).click()
        payload = employee_payload(client)
        payload['employees'][-1]['enabled'] = False
        assert client.put('/api/employees', json=payload).status_code == 200
        page.get_by_test_id('save-button').click()
        expect(page.locator('.request-error')).to_contain_text('登录已失效')
        expect(page.get_by_test_id('actual-1').locator('input')).to_have_value('321')
        expect(page.get_by_role('link', name='重新登录', exact=True)).to_be_visible()
        expect(page).to_have_url(live_server + '/edit')


def test_logout_cancel_preserves_draft(ui_context, live_server):
    """验证退出时草稿确认；上下文、服务为参数；断言取消继续编辑，确认后退出登录。"""
    page = ui_context.new_page()
    page.goto(live_server + '/edit')
    expect(page.get_by_test_id('save-button')).to_be_enabled()
    page.get_by_test_id('actual-1').locator('input').fill('123')
    page.get_by_role('button', name='退出登录', exact=True).click()
    dialog = page.get_by_role('dialog', name='未保存的修改')
    dialog.get_by_role('button', name='继续编辑', exact=True).click()
    expect(page.get_by_test_id('actual-1').locator('input')).to_have_value('123')
    page.get_by_role('button', name='退出登录', exact=True).click()
    dialog.get_by_role('button', name='放弃修改', exact=True).click()
    expect(page).to_have_url(live_server + '/login')
