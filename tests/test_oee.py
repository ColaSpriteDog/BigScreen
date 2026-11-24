"""验证源表工时、异常停机校验及 A/B 拼焊 OEE，所有写入使用隔离数据库。"""

from datetime import timedelta

import pytest
from playwright.sync_api import expect

from backend import create_app
from backend.db import connect_database
from tests.test_api import production_payload, settings_payload
from tests.test_production_import import production_workbook, preview


def configure_capacity(client, capacity=50):
    """配置两条拼焊线理论产能；client 为隔离客户端、capacity 为件/小时或空值；返回保存响应。"""
    payload = settings_payload(client)
    for line in payload['lines'][2:]:
        line['theoretical_max_per_hour'] = capacity
    response = client.put('/api/settings', json=payload)
    assert response.status_code == 200, response.get_json()
    return response


def import_oee(client):
    """导入已知 OEE 样例；client 为隔离客户端；返回导入后快照，便于验证开动率与性能乘积。"""
    def change(book):
        """填写两条拼焊线；book 为内存工作簿；无返回值，已知日产量八百、报废四十、停机四小时。"""
        for name in ('激光焊接', '激光焊接 (B)'):
            sheet = book[name]
            sheet['C25'] = 800
            sheet['C26'] = 40
            sheet['C28'] = 4
    configure_capacity(client)
    draft = preview(client, production_workbook(change)).get_json()
    assert client.post('/api/production/2026-11/import', json=draft).status_code == 200
    return client.get('/api/dashboard').get_json()


def test_oee_uses_source_hours_and_ideal_cycle(client):
    """验证三因素；client 为客户端；断言开动率八成、性能百分百、品质九成五得到七成六。"""
    # 每条线工时20、异常停机4、实际800、理论每小时50件、报废40：0.8×1×0.95=76%。
    dashboard = import_oee(client)
    assert dashboard['overview']['oee_percent'] == 76
    assert dashboard['overview']['average_output'] == 40
    draft = production_payload(client, '2026-11-01')
    assert draft['lines'][2]['planned_hours'] == 20
    assert draft['lines'][2]['abnormal_hours'] == 4
    assert draft['lines'][2]['ideal_cycle_hours'] == 0.02
    # 不重复扣除停机；理论小时产能不随日报计划工时变动。
    draft['lines'][2]['planned_hours'] = 10
    assert client.put('/api/production/2026-11-01', json=draft).status_code == 200
    assert client.get('/api/dashboard').get_json()['overview']['oee_percent'] == 101.33
    # 若月内任一已录入产线缺少停机信息，不能仅汇总其余记录冒充整月 OEE。
    draft = production_payload(client, '2026-11-01')
    draft['lines'][2]['abnormal_hours'] = None
    assert client.put('/api/production/2026-11-01', json=draft).status_code == 200
    assert client.get('/api/dashboard').get_json()['overview']['oee_percent'] is None


@pytest.mark.parametrize('stops,expected', [
    ((None, None, None, None), 0),
    ((0, 0, 0, 0), 0),
    ((None, 1.5, None, 0), 1.5),
    ((None, '6.5H', None, 0), 6.5),
    ((None, '6.5', None, 0), 6.5),
    ((None, ' 6.5 h ', None, 0), 6.5),
    (('2H', '1.5h', None, 0), 3.5),
])
def test_blank_source_stops_count_as_zero(client, stops, expected):
    """验证空白停机口径；client、stops、expected 为客户端、四处停机及预期合计；断言预览和保存后 OEE 有效。"""
    def change(book):
        """设置拼焊停机明细；book 为内存工作簿；无返回值，保留空白以验证按零汇总。"""
        for name in ('激光焊接', '激光焊接 (B)'):
            for row, value in zip((28, 33, 41, 45), stops):
                book[name].cell(row, 3).value = value
    configure_capacity(client)
    response = preview(client, production_workbook(change))
    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    welding = [item for item in data['items'] if item['line_id'] in (3, 4)]
    assert len(welding) == 2
    assert all(item['abnormal_hours'] == expected and not item['warnings'] for item in welding)
    assert client.post('/api/production/2026-11/import', json=data).status_code == 200
    saved = client.get('/api/editor?date=2026-11-01').get_json()['production']
    assert all(line['abnormal_hours'] == expected for line in saved['lines'][2:])
    assert saved['overview']['oee_percent'] == 9.9
    assert client.get('/api/dashboard').get_json()['overview']['oee_percent'] == 9.9


@pytest.mark.parametrize('kind', ['too_long', 'rate_formula', 'negative', 'partial_hours', 'invalid_cell', 'invalid_text',
                                  'negative_unit', 'unit_only', 'unit_with_note'])
def test_source_timing_errors_warn_without_inventing_oee(client, kind):
    """验证源错误；client/kind 为客户端和错误类型；断言预览标注且不影响有效产量导入、无错误 OEE。"""
    def change(book):
        """注入工时错误；book 为内存工作簿；无返回值。"""
        sheet = book['激光焊接']
        if kind == 'too_long': sheet['C28'] = 10.5
        elif kind == 'rate_formula': sheet['C45'] = '=C44/C43'
        elif kind == 'negative': sheet['C28'] = -1
        elif kind == 'invalid_cell': sheet['C28'] = '#DIV/0!'
        elif kind == 'invalid_text': sheet['C28'] = '待核实'
        elif kind == 'negative_unit': sheet['C28'] = '-1H'
        elif kind == 'unit_only': sheet['C28'] = 'H'
        elif kind == 'unit_with_note': sheet['C28'] = '6.5H repair'
        else: sheet['C36'] = None
    configure_capacity(client)
    data = preview(client, production_workbook(change)).get_json()
    item = next(item for item in data['items'] if item['line_id'] == 3)
    assert item['warnings']
    assert item['abnormal_hours'] is None
    assert client.post('/api/production/2026-11/import', json=data).status_code == 200
    assert client.get('/api/dashboard').get_json()['overview']['actual_qty'] == 200
    assert client.get('/api/dashboard').get_json()['overview']['oee_percent'] is None


def test_timing_conflict_atomicity_and_full_stop(client):
    """验证保存边界；client 为客户端；断言工时差异需覆盖、非法停机回滚、全停机无产量得零。"""
    import_oee(client)
    data = preview(client).get_json()
    assert next(item for item in data['items'] if item['line_id'] == 3)['conflict']
    data['items'][2]['abnormal_hours'] = 21
    data['overwrite'] = True
    assert client.post('/api/production/2026-11/import', json=data).status_code == 400
    assert client.get('/api/dashboard').get_json()['overview']['oee_percent'] == 76
    draft = production_payload(client, '2026-11-01')
    for line in draft['lines'][2:]:
        line.update(actual_qty=0, defect_qty=0, abnormal_hours=20)
    assert client.put('/api/production/2026-11-01', json=draft).status_code == 200
    assert client.get('/api/dashboard').get_json()['overview']['oee_percent'] == 0


def test_legacy_oee_columns_keep_business_data(client, app):
    """验证升级；client/app 为隔离环境；断言新增两列为空且原数量、版本和人员信息不改变。"""
    draft = production_payload(client)
    draft['lines'][2]['actual_qty'] = 200
    assert client.put('/api/production/2026-11-21', json=draft).status_code == 200
    before = client.get('/api/dashboard').get_json()
    with connect_database(app.config['DATABASE']) as db:
        db.execute('ALTER TABLE production_daily DROP COLUMN abnormal_hours')
        db.execute('ALTER TABLE production_daily DROP COLUMN ideal_cycle_hours')
    restarted = create_app(dict(app.config)).test_client()
    assert restarted.get('/api/dashboard').get_json() == before


def test_capacity_recalculates_history_and_ignores_daily_target_and_client_cycle(client, app):
    """验证理论产能独立；client/app 为隔离环境；断言配置重算历史且目标、日报及导入节拍不影响 OEE。"""
    import_oee(client)
    with connect_database(app.config['DATABASE']) as db:
        daily_before = [tuple(row) for row in db.execute('SELECT * FROM production_daily ORDER BY work_date,line_id')]
    configure_capacity(client, 100)
    assert client.get('/api/dashboard').get_json()['overview']['oee_percent'] == 38
    payload = settings_payload(client)
    payload['lines'][2]['standard_qty'] = 99999
    payload['lines'][3]['standard_qty'] = 88888
    assert client.put('/api/settings', json=payload).status_code == 200
    assert client.get('/api/dashboard').get_json()['overview']['oee_percent'] == 38
    with connect_database(app.config['DATABASE']) as db:
        assert [tuple(row) for row in db.execute('SELECT * FROM production_daily ORDER BY work_date,line_id')] == daily_before
    data = preview(client).get_json()
    for item in data['items']:
        item['ideal_cycle_hours'] = 999
    data['overwrite'] = True
    assert client.post('/api/production/2026-11/import', json=data).status_code == 200
    assert client.get('/api/dashboard').get_json()['overview']['oee_percent'] == 4.95
    configure_capacity(client, None)
    assert client.get('/api/dashboard').get_json()['overview']['oee_percent'] is None


@pytest.mark.parametrize('capacity', [0, -1, True, '50', 1e13])
def test_invalid_capacity_rolls_back_settings_and_attribution(client, capacity):
    """验证非法产能原子回滚；client 为隔离客户端、capacity 为非法值；断言设置、版本及人员信息保持原样。"""
    import_oee(client)
    before = client.get('/api/editor').get_json()
    payload = settings_payload(client)
    payload['lines'][0]['name'] = '回滚测试'
    payload['lines'][3]['theoretical_max_per_hour'] = capacity
    assert client.put('/api/settings', json=payload).status_code == 400
    assert client.get('/api/editor').get_json() == before


def test_different_capacities_use_good_ideal_hours_and_missing_line_is_incomplete(client):
    """验证两线不同节拍；client 为隔离客户端；断言按合格品理想工时加权且任一产能缺失返回空值。"""
    import_oee(client)
    payload = settings_payload(client)
    payload['lines'][3]['theoretical_max_per_hour'] = 100
    assert client.put('/api/settings', json=payload).status_code == 200
    assert client.get('/api/dashboard').get_json()['overview']['oee_percent'] == 57
    payload = settings_payload(client)
    payload['lines'][3]['theoretical_max_per_hour'] = None
    assert client.put('/api/settings', json=payload).status_code == 200
    assert client.get('/api/dashboard').get_json()['overview']['oee_percent'] is None


def test_capacity_updates_only_changed_configuration_attribution(client, clock):
    """验证产能修改归属；client/clock 为隔离客户端和时钟；断言仅变化的产线配置更新人员时间。"""
    import_oee(client)
    before = client.get('/api/editor?date=2026-11-01').get_json()['production']['lines']
    clock['now'] += timedelta(minutes=1)
    payload = settings_payload(client)
    payload['lines'][2]['theoretical_max_per_hour'] = 60
    assert client.put('/api/settings', json=payload).status_code == 200
    after = client.get('/api/editor?date=2026-11-01').get_json()['production']['lines']
    assert after[2]['_configuration_audit']['modified_at'] != before[2]['_configuration_audit']['modified_at']
    assert after[3]['_configuration_audit'] == before[3]['_configuration_audit']
    assert [line['_audit'] for line in after] == [line['_audit'] for line in before]


def test_capacity_migration_preserves_legacy_rows_and_old_settings_clients(client, app):
    """验证旧库迁移与旧客户端；client/app 为隔离环境；断言不推算产能、不改日报且省略新字段保留原值。"""
    import_oee(client)
    with connect_database(app.config['DATABASE']) as db:
        daily = [tuple(row) for row in db.execute('SELECT * FROM production_daily ORDER BY work_date,line_id')]
        attribution = [tuple(row) for row in db.execute('SELECT * FROM record_attribution ORDER BY record_type,record_key')]
        version = db.execute('SELECT version FROM screen_settings').fetchone()[0]
        db.execute('ALTER TABLE production_line DROP COLUMN theoretical_max_per_hour')
    restarted = create_app(dict(app.config)).test_client()
    assert restarted.post('/api/auth/login', json={'username': 'admin', 'password': 'admin123'}).status_code == 200
    assert restarted.get('/api/dashboard').get_json()['overview']['oee_percent'] is None
    with connect_database(app.config['DATABASE']) as db:
        assert all(row[0] is None for row in db.execute('SELECT theoretical_max_per_hour FROM production_line'))
        assert [tuple(row) for row in db.execute('SELECT * FROM production_daily ORDER BY work_date,line_id')] == daily
        assert [tuple(row) for row in db.execute('SELECT * FROM record_attribution ORDER BY record_type,record_key')] == attribution
        assert db.execute('SELECT version FROM screen_settings').fetchone()[0] == version
    configure_capacity(restarted, 50.5)
    payload = settings_payload(restarted)
    for line in payload['lines']:
        line.pop('theoretical_max_per_hour')
    assert restarted.put('/api/settings', json=payload).status_code == 200
    assert restarted.get('/api/editor').get_json()['production']['lines'][2]['theoretical_max_per_hour'] == 50.5


def test_browser_capacity_setting_persists_and_updates_oee(ui_context, live_server, client):
    """验证后台配置闭环；参数为浏览器上下文、服务和客户端；断言保存持久化、草稿计算和大屏同步。"""
    import_oee(client)
    editor = ui_context.new_page()
    editor.goto(live_server + '/edit')
    editor.get_by_role('tab', name='流程与设置', exact=True).click()
    for identifier in (3, 4):
        control = editor.get_by_test_id(f'theoretical-max-{identifier}').locator('input')
        control.fill('100')
        control.press('Tab')
    editor.get_by_test_id('save-button').click()
    expect(editor.get_by_test_id('save-status')).to_have_text('所有修改已保存')
    editor.reload()
    editor.get_by_role('tab', name='流程与设置', exact=True).click()
    expect(editor.get_by_test_id('theoretical-max-3').locator('input')).to_have_value('100.000')
    assert editor.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    editor.screenshot(path='output/playwright/settings-theoretical-capacity.png', full_page=True)
    screen = ui_context.new_page()
    screen.goto(live_server + '/screen')
    expect(screen.locator('#oeePercent')).to_have_text('38%')
    editor.get_by_role('tab', name='生产日报', exact=True).click()
    editor.get_by_role('combobox', name='日报日期', exact=True).fill('2026-11-01')
    editor.get_by_role('combobox', name='日报日期', exact=True).press('Enter')
    expect(editor.get_by_test_id('overview-oee')).to_have_text('38%')


def test_browser_oee_is_derived_and_preview_shows_hours(ui_context, live_server, client):
    """验证界面；参数为浏览器、服务和客户端；断言工时可核对且 OEE 自动展示并随草稿更新。"""
    import_oee(client)
    page = ui_context.new_page()
    page.goto(live_server + '/screen')
    expect(page.locator('#oeePercent')).to_have_text('76%')
    editor = ui_context.new_page()
    editor.goto(live_server + '/edit')
    expect(editor.get_by_role('spinbutton', name='设备综合效率', exact=True)).to_have_count(0)
    upload = {'name': '日报.xlsx', 'mimeType': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
              'buffer': production_workbook().getvalue()}
    editor.get_by_label('生产日报 Excel 文件', exact=True).set_input_files(upload)
    dialog = editor.get_by_role('dialog', name='生产日报导入预览', exact=True)
    expect(dialog).to_be_visible()
    expect(dialog.get_by_text('异常停机（小时）', exact=True)).to_be_visible()
    expect(dialog.get_by_text('覆盖所选记录中已有的不同数量和工时')).to_be_visible()
