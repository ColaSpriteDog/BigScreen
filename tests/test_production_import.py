"""验证四线生产文件的解析、月份、单位、原子保存和人员归属，所有写入使用隔离数据库。"""

from io import BytesIO
from pathlib import Path

import pytest
from openpyxl import Workbook

from backend.production_import import LAYOUTS, HOUR_ROWS, STOP_ROWS
from tests.test_api import production_payload, settings_payload


def production_workbook(change=None):
    """创建四线模板；change 可修改工作簿模拟错误；返回包含首日数量及整月标准的内存文件。"""
    book = Workbook()
    book.remove(book.active)
    for identifier, name, unit, actual, defects, standards in LAYOUTS:
        sheet = book.create_sheet(name)
        for row, label in actual + defects + standards:
            sheet.cell(row, 2, label)
        for row, _ in standards:
            for column in range(3, 34):
                sheet.cell(row, column, 250)
        for row in HOUR_ROWS[identifier]:
            sheet.cell(row, 2, '工时（H）')
            for column in range(3, 34):
                sheet.cell(row, column, 10)
        if identifier in (3, 4):
            for row in (28, 33, 41, 45):
                sheet.cell(row, 2, '异常停机工时')
        else:
            for row, label in STOP_ROWS[identifier]:
                sheet.cell(row, 2, label)
        sheet.cell(actual[0][0], 3, 100.125 if unit == '米' else 100)
        sheet.cell(defects[0][0], 3, 0.25 if unit == '米' else 1)
        # 报废率错误不属于导入字段，不能阻止有效数量的提取。
        sheet.cell(70, 3, '#DIV/0!')
    if change:
        change(book)
    stream = BytesIO()
    book.save(stream)
    book.close()
    stream.seek(0)
    return stream


def preview(client, source=None, month='2026-11'):
    """请求导入预览；client 为测试客户端、source 为文件流、month 为月份；返回 HTTP 响应。"""
    return client.post('/api/production/' + month + '/preview', data={'file': (source or production_workbook(), '生产日报.xlsx')})


@pytest.mark.parametrize('value,number_format,expected', [
    (10.166666666666666, '0.0', 20.4),
    (10.25, '0.0', 20.6),
    (10.166666666666666, '0.00', 20.34),
    (10.166666666666666, 'General', 20 + 1 / 3),
])
def test_import_hours_follow_display_precision(client, value, number_format, expected):
    """验证显示精度；参数为客户端、源工时、格式及预期合计；断言逐班舍入且预览、保存口径一致。"""
    def change(book):
        """设置班次工时及格式；book 为内存工作簿；无返回值，覆盖四条产线的白夜班。"""
        for identifier, name, *_ in LAYOUTS:
            for row in HOUR_ROWS[identifier]:
                book[name].cell(row, 3, value).number_format = number_format
    response = preview(client, production_workbook(change))
    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    assert all(item['planned_hours'] == pytest.approx(expected) for item in data['items'])
    assert client.post('/api/production/2026-11/import', json=data).status_code == 200
    saved = client.get('/api/editor?date=2026-11-01').get_json()['production']['lines']
    assert all(line['planned_hours'] == pytest.approx(expected) for line in saved)


def test_real_production_file_preview_and_save(client, app):
    """核对用户原表；client、app 为隔离环境；断言明细、标准、米数精度、空白及同步保存正确。"""
    source = next((Path(__file__).resolve().parents[1] / 'docs').glob('5.*.xlsx'), None)
    if source is None:
        pytest.skip('当前环境没有用户提供的生产日报')
    before = client.get('/api/editor').get_json()
    with source.open('rb') as stream:
        response = preview(client, stream)
    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    assert len(data['items']) == 48
    assert [item['standard_qty'] for item in data['standards']] == [18000, 1420, 1000, 1000]
    assert [item['unit'] for item in data['standards']] == ['米', '件', '件', '件']
    assert client.get('/api/editor').get_json() == before
    assert {item['line_id'] for item in data['items']} == {1, 2, 3}
    for identifier, amount in [(1, 233939.009212), (2, 21737), (3, 16115)]:
        assert sum(item['actual_qty'] for item in data['items'] if item['line_id'] == identifier) == pytest.approx(amount)
    assert client.post('/api/production/2026-11/import', json=data).status_code == 200
    result = client.get('/api/dashboard').get_json()
    assert [line['standard_qty'] for line in result['lines']] == [18000, 1420, 1000, 1000]
    assert [line['monthly_qty'] for line in result['lines']] == pytest.approx([233939.009212, 21737, 16115, None], nan_ok=True)
    assert result['overview']['actual_qty'] == 16115
    assert result['overview']['planned_qty'] == 16000
    assert result['overview']['completion_rate'] == 100.72
    assert result['overview']['average_output'] == 49.37
    assert result['overview']['oee_percent'] is None
    assert any(item['warnings'] for item in data['items'] if item['line_id'] == 3)
    assert all(item['abnormal_hours'] is None for item in data['items'] if item['line_id'] == 3)
    assert all(line['actual_qty'] is None for line in result['lines'])
    first = client.get('/api/editor?date=2026-11-01').get_json()['production']['lines']
    assert first[0]['actual_qty'] == pytest.approx(19217.6438348)
    assert first[0]['defect_qty'] == pytest.approx(15.172375)
    assert first[0]['_audit']['modified_by_name'] == '管理员'
    assert first[0]['planned_hours'] == pytest.approx(20.4)
    assert first[2]['actual_qty'] == 1001 and first[2]['defect_qty'] == 12


def test_import_is_idempotent_preserves_unrelated_fields_and_uses_units(client):
    """验证重复与工时更新；client 为客户端；断言覆盖有效工时、派生 OEE、重复不累加且单位不混加。"""
    draft = production_payload(client, '2026-11-01')
    draft['lines'][0]['planned_hours'] = 12
    draft['overview']['oee_percent'] = 95
    assert client.put('/api/production/2026-11-01', json=draft).status_code == 200
    data = preview(client).get_json()
    data['overwrite'] = True
    assert client.post('/api/production/2026-11/import', json=data).status_code == 200
    saved = client.get('/api/editor?date=2026-11-01').get_json()['production']
    assert saved['lines'][0]['actual_qty'] == 100.125
    assert saved['lines'][0]['planned_hours'] == 20 and saved['overview']['oee_percent'] is None
    assert saved['overview']['actual_qty'] == 200
    repeat = preview(client).get_json()
    assert not any(item['conflict'] for item in repeat['items'])
    assert client.post('/api/production/2026-11/import', json=repeat).status_code == 200
    assert client.get('/api/editor?date=2026-11-01').get_json()['production'] == saved
    settings = settings_payload(client)
    for line in settings['lines']:
        line['show_in_trend'] = True
    assert client.put('/api/settings', json=settings).status_code == 200
    trend = client.get('/api/dashboard').get_json()['trend']
    assert trend['period_total'] is None and trend['totals'] is None
    assert {group['unit']: group['period_total'] for group in trend['groups']} == {'米': 100.125, '件': 300}


def test_import_conflict_and_failure_roll_back_standards_and_quantities(client):
    """验证覆盖与回滚；client 为客户端；断言冲突、非法末行、旧版本不会部分提交，显式覆盖成功。"""
    data = preview(client).get_json()
    assert client.post('/api/production/2026-11/import', json=data).status_code == 200
    conflict = preview(client).get_json()
    conflict['items'][0]['actual_qty'] = 999
    conflict['standards'][0]['standard_qty'] = 9999
    response = client.post('/api/production/2026-11/import', json=conflict)
    assert response.status_code == 400 and '覆盖' in response.get_json()['error']
    assert client.get('/api/dashboard').get_json()['lines'][0]['standard_qty'] == 500
    conflict['overwrite'] = True
    conflict['items'][-1]['actual_qty'] = -1
    assert client.post('/api/production/2026-11/import', json=conflict).status_code == 400
    assert client.get('/api/editor?date=2026-11-01').get_json()['production']['lines'][0]['actual_qty'] == 100.125
    conflict['items'][-1]['actual_qty'] = 100
    assert client.post('/api/production/2026-11/import', json=conflict).status_code == 200
    assert client.get('/api/dashboard').get_json()['lines'][0]['standard_qty'] == 9999
    assert client.post('/api/production/2026-11/import', json=conflict).status_code == 409


@pytest.mark.parametrize('kind', ['fraction', 'cache', 'layout', 'extra_day', 'standard'])
def test_invalid_source_fields_reject_preview(client, kind):
    """验证源文件错误；kind 为错误类型；断言分数件数、无缓存、错模板、无效日期和变动标准被拒绝。"""
    def change(book):
        """注入错误；book 为临时工作簿；无返回值。"""
        sheet = book['激光焊接']
        if kind == 'fraction': sheet['C25'] = 1.5
        elif kind == 'cache': sheet['C25'] = '=1+2'
        elif kind == 'layout': sheet['B25'] = '错误标题'
        elif kind == 'extra_day': sheet['AG25'] = 1
        else: sheet['D24'] = 123
    response = preview(client, production_workbook(change))
    assert response.status_code == 400, response.get_json()
    assert client.get('/api/dashboard').get_json()['version'] == 0


def test_preview_auth_month_future_and_partial_selection(client, app):
    """验证导入范围；client、app 为测试环境；断言鉴权、月份选择、未来日期阻止及只保存勾选行。"""
    assert app.test_client().post('/api/production/2026-11/preview').status_code == 401
    assert client.post('/api/production/2026-11/preview', headers={'Origin': 'https://elsewhere.test'}).status_code == 403
    assert preview(client, month='2026-13').status_code == 400
    future = preview(client, month='2026-12').get_json()
    assert all(item['future'] for item in future['items'])
    assert client.post('/api/production/2026-12/import', json=future).status_code == 400
    data = preview(client, month='2026-10').get_json()
    data['items'] = data['items'][:1]
    assert client.post('/api/production/2026-10/import', json=data).status_code == 200
    saved = client.get('/api/editor?date=2026-10-01').get_json()['production']['lines']
    assert saved[0]['actual_qty'] == 100.125 and saved[1]['actual_qty'] is None
