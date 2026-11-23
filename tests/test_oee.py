"""验证源表工时、异常停机校验及 A/B 拼焊 OEE，所有写入使用隔离数据库。"""

import pytest
from playwright.sync_api import expect

from backend import create_app
from backend.db import connect_database
from tests.test_api import production_payload
from tests.test_production_import import production_workbook, preview


def import_oee(client):
    """导入已知 OEE 样例；client 为隔离客户端；返回导入后快照，便于验证开动率与性能乘积。"""
    def change(book):
        """填写两条拼焊线；book 为内存工作簿；无返回值，已知日产量八百、报废四十、停机四小时。"""
        for name in ('激光焊接', '激光焊接 (B)'):
            sheet = book[name]
            sheet['C25'] = 800
            sheet['C26'] = 40
            sheet['C28'] = 4
    draft = preview(client, production_workbook(change)).get_json()
    assert client.post('/api/production/2026-11/import', json=draft).status_code == 200
    return client.get('/api/dashboard').get_json()


def test_oee_uses_source_hours_and_ideal_cycle(client):
    """验证三因素；client 为客户端；断言开动率八成、性能百分百、品质九成五得到七成六。"""
    # 每条线工时20、异常停机4、实际800、标准1000、报废40：0.8×1×0.95=76%。
    dashboard = import_oee(client)
    assert dashboard['overview']['oee_percent'] == 76
    assert dashboard['overview']['average_output'] == 40
    draft = production_payload(client, '2026-11-01')
    assert draft['lines'][2]['planned_hours'] == 20
    assert draft['lines'][2]['abnormal_hours'] == 4
    assert draft['lines'][2]['ideal_cycle_hours'] == 0.02
    # 不重复扣除停机；理想节拍在补录日报工时后保持原导入值。
    draft['lines'][2]['planned_hours'] = 10
    assert client.put('/api/production/2026-11-01', json=draft).status_code == 200
    assert client.get('/api/dashboard').get_json()['overview']['oee_percent'] == 101.33
    # 若月内任一已录入产线缺少停机信息，不能仅汇总其余记录冒充整月 OEE。
    draft = production_payload(client, '2026-11-01')
    draft['lines'][2]['abnormal_hours'] = None
    assert client.put('/api/production/2026-11-01', json=draft).status_code == 200
    assert client.get('/api/dashboard').get_json()['overview']['oee_percent'] is None


@pytest.mark.parametrize('kind', ['too_long', 'rate_formula', 'negative', 'partial_hours'])
def test_source_timing_errors_warn_without_inventing_oee(client, kind):
    """验证源错误；client/kind 为客户端和错误类型；断言预览标注且不影响有效产量导入、无错误 OEE。"""
    def change(book):
        """注入工时错误；book 为内存工作簿；无返回值。"""
        sheet = book['激光焊接']
        if kind == 'too_long': sheet['C28'] = 10.5
        elif kind == 'rate_formula': sheet['C45'] = '=C44/C43'
        elif kind == 'negative': sheet['C28'] = -1
        else: sheet['C36'] = None
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
