"""验证生产整月同步的清空范围、零值、回滚与大屏联动，所有操作使用隔离数据库。"""

import pytest

from backend.db import get_db
from backend.production_import import LAYOUTS
from tests.test_api import production_payload
from tests.test_production_import import preview, production_workbook


def clear_source(book, identifiers=(4,)):
    """清空源数量；book 为内存工作簿、identifiers 为产线集合；无返回值，保留有效模板和标准。"""
    for identifier in identifiers:
        sheet = book.worksheets[identifier - 1]
        # 测试模板仅在首日录入数量，其他日期保持空白。
        for row, _ in LAYOUTS[identifier - 1][3] + LAYOUTS[identifier - 1][4]:
            sheet.cell(row, 3).value = None


def sync(client, data):
    """提交整月同步；client 为客户端、data 为完整预览；返回保存响应，不修改原预览。"""
    return client.post('/api/production/' + data['month'] + '/import', json={**data, 'sync_month': True})


def test_sync_clears_b_and_timing_preserves_other_month_and_unchanged_a(client, app):
    """验证删除 B 线全链路；client/app 为隔离环境；断言预览只读、整条清空、其他月份与 A 线归属不变。"""
    other = preview(client, month='2026-10').get_json()
    assert sync(client, other).status_code == 200
    original = preview(client).get_json()
    assert sync(client, original).status_code == 200
    before = client.get('/api/editor?date=2026-11-01').get_json()
    october = client.get('/api/editor?date=2026-10-01').get_json()['production']
    data = preview(client, production_workbook(clear_source)).get_json()
    assert len(data['clear_items']) == 1
    deleted = data['clear_items'][0]
    assert (deleted['date'], deleted['line_id'], deleted['action']) == ('2026-11-01', 4, 'clear')
    assert deleted['previous_actual'] == 100 and deleted['previous_hours'] == 20
    assert client.get('/api/editor?date=2026-11-01').get_json() == before
    assert sync(client, data).status_code == 200
    dashboard = client.get('/api/dashboard').get_json()
    b_line = dashboard['lines'][3]
    assert all(b_line[field] is None for field in ('monthly_qty', 'monthly_planned_hours', 'monthly_abnormal_hours'))
    assert not next(series for series in dashboard['trend']['series'] if series['line_id'] == 4)['has_data']
    assert dashboard['overview']['actual_qty'] == 100
    after = client.get('/api/editor?date=2026-11-01').get_json()['production']
    assert after['lines'][2] == before['production']['lines'][2]
    assert after['lines'][3]['actual_qty'] is None and after['lines'][3]['planned_hours'] == 24
    assert after['lines'][3]['_audit']['state'] == 'unsaved'
    assert client.get('/api/editor?date=2026-10-01').get_json()['production'] == october
    with app.app_context():
        assert get_db().execute("SELECT * FROM production_daily WHERE work_date='2026-11-01' AND line_id=4").fetchone() is None
    repeated = preview(client, production_workbook(clear_source)).get_json()
    assert repeated['clear_items'] == []
    assert sync(client, repeated).status_code == 200
    assert client.get('/api/editor?date=2026-11-01').get_json()['production'] == after


def test_sync_all_blank_template_clears_month(client):
    """验证全月清空；client 为隔离客户端；断言完整空模板可预览并移除旧日报，而非写入零产量。"""
    assert sync(client, preview(client).get_json()).status_code == 200
    data = preview(client, production_workbook(lambda book: clear_source(book, (1, 2, 3, 4)))).get_json()
    assert data['items'] == [] and len(data['clear_items']) == 4
    assert sync(client, data).status_code == 200
    dashboard = client.get('/api/dashboard').get_json()
    assert all(line['monthly_qty'] is None for line in dashboard['lines'])
    assert dashboard['trend']['reported_days'] == 0
    assert dashboard['overview']['actual_qty'] is None


def test_sync_zero_is_data_and_blank_defects_clear_previous_value(client):
    """验证零与空白；client 为隔离客户端；断言明确零保留日报、空白报废不沿用旧值且差异无需另勾覆盖。"""
    assert sync(client, preview(client).get_json()).status_code == 200

    def change(book):
        """修改 B 线源数量；book 为内存工作簿；无返回值，清空报废并录入明确零产量。"""
        book.worksheets[3]['C25'] = 0
        book.worksheets[3]['C26'] = None

    data = preview(client, production_workbook(change)).get_json()
    assert data['clear_items'] == []
    assert data['items'][-1]['action'] == 'update'
    assert sync(client, data).status_code == 200
    saved = client.get('/api/editor?date=2026-11-01').get_json()['production']['lines'][3]
    assert saved['actual_qty'] == 0 and saved['defect_qty'] is None
    dashboard = client.get('/api/dashboard').get_json()
    assert dashboard['lines'][3]['monthly_qty'] == 0
    assert next(series for series in dashboard['trend']['series'] if series['line_id'] == 4)['has_data']


@pytest.mark.parametrize('kind', ['missing_clear', 'wrong_month', 'duplicate', 'partial_source', 'invalid_standard', 'invalid_quantity', 'stale'])
def test_invalid_sync_rolls_back_writes_and_deletions(client, kind):
    """验证事务边界；client/kind 为客户端及错误类型；断言坏请求与旧预览不改变日报、标准和版本。"""
    assert sync(client, preview(client).get_json()).status_code == 200
    data = preview(client, production_workbook(clear_source)).get_json()
    data['items'][0]['actual_qty'] = 999
    data['standards'][0]['standard_qty'] = 888
    if kind == 'missing_clear':
        data.pop('clear_items')
    elif kind == 'wrong_month':
        data['clear_items'][0]['date'] = '2026-10-01'
    elif kind == 'duplicate':
        data['clear_items'].append(data['clear_items'][0].copy())
    elif kind == 'partial_source':
        data['items'].pop()
    elif kind == 'invalid_standard':
        data['standards'][-1]['standard_qty'] = -1
    elif kind == 'invalid_quantity':
        data['items'][-1]['actual_qty'] = -1
    else:
        # 模拟预览后发生另一次保存，版本检查必须阻止旧范围清空。
        draft = production_payload(client, '2026-11-02')
        draft['lines'][3]['actual_qty'] = 200
        assert client.put('/api/production/2026-11-02', json=draft).status_code == 200
    before = client.get('/api/editor?date=2026-11-01').get_json()
    response = sync(client, data)
    assert response.status_code == (409 if kind == 'stale' else 400), response.get_json()
    assert client.get('/api/editor?date=2026-11-01').get_json() == before
    assert client.get('/api/dashboard').get_json()['lines'][3]['monthly_qty'] == (300 if kind == 'stale' else 100)


def test_sync_clears_timing_only_rows_but_preserves_monthly_plan(client):
    """验证无产量工时；client 为客户端；断言自定义工时进入清空预览，月计划独立保留。"""
    draft = production_payload(client, '2026-11-02')
    draft['monthly_plan'] = {'month': '2026-11', 'planned_qty': 12345}
    draft['lines'][3]['planned_hours'] = 12
    assert client.put('/api/production/2026-11-02', json=draft).status_code == 200
    data = preview(client).get_json()
    assert [(item['date'], item['line_id']) for item in data['clear_items']] == [('2026-11-02', 4)]
    assert sync(client, data).status_code == 200
    saved = client.get('/api/editor?date=2026-11-02').get_json()['production']
    assert saved['lines'][3]['planned_hours'] == 24
    assert saved['monthly_plan']['planned_qty'] == 12345


def test_sync_rejects_future_month_and_future_source(client):
    """验证未来日期；client 为隔离客户端；断言空白未来月和有未来产量的当前月均不写库。"""
    future = preview(client, production_workbook(lambda book: clear_source(book, (1, 2, 3, 4))), month='2026-12').get_json()
    assert sync(client, future).status_code == 400

    def change(book):
        """增加未来数量；book 为内存工作簿；无返回值，十一月二十二日录入 B 线。"""
        book.worksheets[3].cell(25, 24, 100)

    data = preview(client, production_workbook(change)).get_json()
    assert sync(client, data).status_code == 400
    assert client.get('/api/dashboard').get_json()['version'] == 0


def test_legacy_supplement_import_still_preserves_blank_b(client):
    """验证旧接口默认行为；client 为客户端；断言未指定同步时空白 B 线保留原记录。"""
    assert client.post('/api/production/2026-11/import', json=preview(client).get_json()).status_code == 200
    data = preview(client, production_workbook(clear_source)).get_json()
    assert client.post('/api/production/2026-11/import', json=data).status_code == 200
    assert client.get('/api/dashboard').get_json()['lines'][3]['monthly_qty'] == 100
