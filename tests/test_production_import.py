"""验证四线生产文件的解析、月份、单位、原子保存和人员归属，所有写入使用隔离数据库。"""

from io import BytesIO
from pathlib import Path

import pytest
from openpyxl import Workbook

from backend.production_import import LAYOUTS, HOUR_ROWS
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
