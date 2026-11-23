"""验证 Excel 库存解析、只驻留内存的上传、预览不写库以及错误定位。"""

from io import BytesIO
from pathlib import Path
from zipfile import ZIP_STORED, ZipFile

import pytest
from openpyxl import Workbook

from backend.domain import ValidationError
from backend.inventory_import import parse_rows


def summary_rows():
    """创建代表性的日报行；无参数；返回包含累计值、合并分组和小数库存的行列表。"""
    return [
        ['11月库存日报表'], [],
        ['', '物料名称', '产品名称', '规格型号', '累计入库', '累计出库', '剩余数量', '单位', '剩余总重量', '预计可切割数量'],
        [],
        ['钢\n卷', '钢卷', 'A柱', '1.6*1430/\n1.6*1500', 23, 9, 16, '卷', 185370, 19200],
        ['', '钢卷', 'A柱下', '1.4*1002', 1, 1, 0, '卷', 0, 0],
        ['', '钢卷', '试机料', '1.2*1250', 0, 0, 0.5, '卷', None, None],
        ['左\n件', '半成品', '门槛左件', 1.4, 9686, 6709, 2977, '个'],
        ['', '半成品', 'A柱左件', 1.6, 9604, 6709, 2895, '个'],
        ['右\n件', '半成品', '门槛右件', 1.4, 11595, 6741, 4854, '个'],
    ]


def workbook_bytes(rows=None, sheet_name='汇总表', padding=0):
    """生成上传测试数据；rows 为日报、padding 为额外字节数；返回内存中的 xlsx 测试夹具。"""
    book = Workbook()
    sheet = book.active
    sheet.title = sheet_name
    for row in rows if rows is not None else summary_rows():
        sheet.append(row)
    sheet.merge_cells('A8:A9')
    output = BytesIO()
    book.save(output)
    book.close()
    if padding:
        with ZipFile(output, 'a', compression=ZIP_STORED) as archive:
            archive.writestr('test-padding.bin', b'x' * padding)
    output.seek(0)
    return output


def test_actual_xls_summary_matches_user_file(client):
    """验证用户实际 xls；client 为隔离客户端；断言大文件上传及钢卷、左右件、余量和空白字段映射。"""
    source = Path(__file__).resolve().parents[1] / 'docs' / '仓库11月份日报表 - 11.17.xls'
    if not source.exists():
        pytest.skip('当前环境没有用户提供的日报文件')
    with source.open('rb') as stream:
        response = client.post('/api/inventory/preview', data={'file': (stream, source.name)})
    assert response.status_code == 200, response.get_json()
    result = response.get_json()
    assert client.get('/api/editor').get_json()['version'] == 0
    assert len(result['raw']) == 8 and len(result['semi']) == 10
    assert result['raw_totals'] == [{'unit': '卷', 'quantity': 41.5}]
    assert result['semi_totals'] == [{'unit': '件', 'quantity': 29954}]
    assert result['zero_count'] == 2
    assert result['raw'][0] == {'material': '钢卷', 'position': 'A柱', 'warehouse': '原材料仓库',
        'specification': '1.6*1430/1.6*1500', 'quantity': 16, 'unit': '卷',
        'weight_kg': 185370, 'cuttable_qty': None, 'source_row': 5}
    assert result['raw'][5]['quantity'] == 4
    assert result['raw'][7]['quantity'] == 0.5
    assert result['raw'][7]['weight_kg'] is None
    assert [item['category'] for item in result['semi']] == ['左件'] * 5 + ['右件'] * 5


def test_preview_uses_memory_and_does_not_save_file_or_mutate_inventory(client, app, monkeypatch):
    """验证大于原 2 MiB 的上传；客户端、应用和补丁为参数；断言内存流释放、预览不写库和不落盘。"""
    import backend
    before = client.get('/api/editor').get_json()
    folder = Path(app.config['DATABASE']).parent
    files = set(folder.iterdir())
    streams = []
    parser = backend.parse_inventory_file

    def capture(upload):
        """检查上传载体；upload 为文件流；返回真实解析结果并记录内存流供关闭断言。"""
        assert isinstance(upload.stream, BytesIO)
        streams.append(upload.stream)
        return parser(upload)

    monkeypatch.setattr(backend, 'parse_inventory_file', capture)
    response = client.post('/api/inventory/preview', data={'file': (workbook_bytes(padding=3 * 1024 * 1024), '日报.xlsx')})
    assert response.status_code == 200, response.get_json()
    result = response.get_json()
    assert result['raw'][0]['quantity'] == 16
    assert result['raw'][1]['quantity'] == 0 and result['raw'][1]['weight_kg'] == 0
    assert result['raw'][2]['weight_kg'] is None and result['raw'][2]['cuttable_qty'] is None
    assert result['semi'][1]['category'] == '左件' and result['semi'][1]['specification'] == '1.6'
    after = client.get('/api/editor').get_json()
    assert (after['version'], after['inventory']) == (before['version'], before['inventory'])
    assert set(folder.iterdir()) == files
    assert streams and all(stream.closed for stream in streams)
    saved = client.put('/api/inventory', json={'version': before['version'], 'raw': result['raw'], 'semi': result['semi']})
    assert saved.status_code == 200
    assert client.get('/api/dashboard').get_json()['inventory']['raw_totals'] == [{'quantity': 16.5, 'unit': '卷'}]


@pytest.mark.parametrize('value', [None, -1, True, '不是数字', '=SUM(1,2)', '#REF!'])
def test_invalid_quantity_rejects_whole_preview_with_row(client, value):
    """验证非法及未缓存公式；参数为客户端和数量；断言定位源行并且不更新库存。"""
    rows = summary_rows()
    rows[4][6] = value
    response = client.post('/api/inventory/preview', data={'file': (workbook_bytes(rows), '日报.xlsx')})
    assert response.status_code == 400
    assert '第 5 行' in response.get_json()['error']
    assert client.get('/api/editor').get_json()['version'] == 0


def test_duplicate_or_fractional_semi_records_are_not_silently_imported():
    """验证重复记录和小数半成品；无参数；断言错误阻止整批导入。"""
    rows = summary_rows()
    rows.append(rows[4])
    with pytest.raises(ValidationError, match='重复'):
        parse_rows(rows)
    rows = summary_rows()
    rows[7][6] = 0.5
    with pytest.raises(ValidationError, match='第 8 行.*整数'):
        parse_rows(rows)


def test_invalid_files_and_access_controls(client, app):
    """验证表名、格式和访问边界；参数为登录客户端和应用；断言明确错误、认证及同源检查。"""
    assert client.post('/api/inventory/preview').status_code == 400
    for stream, filename, message in [(workbook_bytes(sheet_name='明细'), '日报.xlsx', '汇总表'),
                                      (BytesIO(b'broken'), '日报.xls', '无法读取'),
                                      (BytesIO(b'broken'), '日报.xlsx', '无法读取'),
                                      (BytesIO(b'wrong'), '日报.csv', '.xls')]:
        response = client.post('/api/inventory/preview', data={'file': (stream, filename)})
        assert response.status_code == 400 and message in response.get_json()['error']
    assert app.test_client().post('/api/inventory/preview').status_code == 401
    assert client.post('/api/inventory/preview', headers={'Origin': 'https://elsewhere.test'}).status_code == 403
    assert client.put('/api/inventory', data=b'x' * (2 * 1024 * 1024 + 1), content_type='application/json').status_code == 413


def test_missing_headers_and_missing_inventory_are_explicit():
    """验证缺少表头和空表；无参数；断言给出可处理的错误，避免无意清空库存。"""
    with pytest.raises(ValidationError, match='表头'):
        parse_rows([['累计入库', '累计出库']])
    with pytest.raises(ValidationError, match='没有可导入'):
        parse_rows(summary_rows()[:4])


def test_optional_formula_without_cache_is_not_treated_as_blank(client):
    """验证重量公式缓存；client 为登录客户端；断言未计算公式不能冒充空白覆盖库存。"""
    rows = summary_rows()
    rows[4][8] = '=100*2'
    response = client.post('/api/inventory/preview', data={'file': (workbook_bytes(rows), '日报.xlsx')})
    assert response.status_code == 400
    assert '第 5 行' in response.get_json()['error'] and '公式缓存缺失' in response.get_json()['error']


def test_legacy_semi_unit_is_presented_as_pieces_without_rewriting_on_read(client, app):
    """验证历史半成品单位；client、app 为隔离应用；断言读取转为件、数量及数据库原记录保持不变。"""
    from backend.db import connect_database
    with connect_database(app.config['DATABASE']) as database:
        database.execute("INSERT INTO semi_finished_stock(category,material,warehouse,specification,quantity,unit,sort_order) "
                         "VALUES('左件','门槛左件','半成品仓库','1.4',2977,'个',0)")
    editor = client.get('/api/editor').get_json()
    item = editor['inventory']['semi'][0]
    assert item['quantity'] == 2977 and item['unit'] == '件'
    assert client.get('/api/dashboard').get_json()['inventory']['semi_totals'] == [{'quantity': 2977, 'unit': '件'}]
    with connect_database(app.config['DATABASE']) as database:
        assert database.execute('SELECT unit FROM semi_finished_stock').fetchone()['unit'] == '个'
    assert editor['version'] == 0
