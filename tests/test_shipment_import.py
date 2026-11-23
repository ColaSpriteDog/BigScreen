"""验证按月逐条导入出库明细、内存解析、日期边界、保存及旧数据升级。"""

from datetime import datetime
from io import BytesIO
from pathlib import Path
import sqlite3

import pytest
from openpyxl import Workbook

from backend import create_app
from backend.db import connect_database
from backend.domain import ValidationError
from backend.shipment_import import parse_shipment_rows


def shipment_rows():
    """提供跨月重复件号；无参数；返回含相同日期重复记录和零数量的 Excel 源行。"""
    return [['成品出库明细'], ['日期', '客户', '零件号', '名称', '数量', '木托盘数量'],
            [datetime(2026, 10, 31), '客户', 'LP000365', '门环左件', 999, 10],
            [datetime(2026, 11, 1), '客户', 'LP000366', '门环右件', 800, 8],
            [datetime(2026, 11, 1), '客户', 'LP000366', '门环右件', 800, 8],
            ['2026/11/17', '客户', 'LP000365', '门环左件', 400, 4],
            ['2026-11-30', '客户', 'LP000365', '门环左件', 0, 0],
            [datetime(2026, 12, 1), '客户', 'LP000366', '门环右件', 888, 9],
            [], ['', '', '合计', '', 3887]]


def shipment_workbook(rows=None, sheet_name='成品出库明细'):
    """创建上传夹具；rows 为源行、sheet_name 为工作表名；返回内存中的 xlsx 数据流。"""
    book = Workbook()
    sheet = book.active
    sheet.title = sheet_name
    for row in rows if rows is not None else shipment_rows():
        sheet.append(row)
    output = BytesIO()
    book.save(output)
    book.close()
    output.seek(0)
    return output


def test_preview_preserves_each_monthly_row_and_never_writes_file(client, app, monkeypatch):
    """验证预览隔离；参数为客户端、应用和补丁；断言重复行完整、内存流释放且数据版本与文件不变。"""
    import backend
    before = client.get('/api/editor').get_json()
    folder = Path(app.config['DATABASE']).parent
    files = set(folder.iterdir())
    streams = []
    parser = backend.parse_shipment_file

    def capture(upload, month):
        """核对解析载体；upload 为上传、month 为筛选月份；返回真实解析结果并记录内存流。"""
        assert isinstance(upload.stream, BytesIO)
        streams.append(upload.stream)
        return parser(upload, month)

    monkeypatch.setattr(backend, 'parse_shipment_file', capture)
    response = client.post('/api/sales/2026-11/preview', data={'file': (shipment_workbook(), '日报.xlsx')})
    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    assert data['source_count'] == 4
    assert data['shipped_qty'] == 2000
    assert data['available_months'] == ['2026-10', '2026-11', '2026-12']
    assert [row['source_row'] for row in data['items']] == [4, 5, 6, 7]
    assert [row['quantity'] for row in data['items']] == [800, 800, 400, 0]
    assert data['items'][0] == {**data['items'][1], 'source_row': 4}
    assert all(row['shipment_date'].startswith('2026-11') for row in data['items'])
    assert all(stream.closed for stream in streams)
    assert client.get('/api/editor').get_json() == before
    assert set(folder.iterdir()) == files


def test_actual_xls_retains_all_november_details(client):
    """核对用户日报；client 为隔离客户端；断言仅筛选十一月且不把重复件号合并成两行。"""
    source = Path(__file__).resolve().parents[1] / 'docs' / '仓库11月份日报表 - 11.17.xls'
    if not source.exists():
        pytest.skip('当前环境没有用户提供的日报文件')
    with source.open('rb') as stream:
        response = client.post('/api/sales/2026-11/preview', data={'file': (stream, source.name)})
    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    assert len(data['items']) > 8
    assert data['shipped_qty'] == 15500
    assert sum(row['quantity'] for row in data['items'] if row['part_no'] == 'LP000365') == 8200
    assert sum(row['quantity'] for row in data['items'] if row['part_no'] == 'LP000366') == 7300
    assert all(row['shipment_date'].startswith('2026-11') for row in data['items'])
    assert data['available_months'] == ['2026-09', '2026-10', '2026-11']


@pytest.mark.parametrize('column,value,message', [(0, None, '日期'), (2, '', '零件号'),
    (3, '', '名称'), (4, -1, '数量'), (4, 1.5, '整数'), (4, '=SUM(A1:A2)', '缓存')])
def test_bad_source_row_rejects_entire_preview(client, column, value, message):
    """验证整批拒绝；参数为错误列、值及提示；断言精确源行错误且无部分数据发布。"""
    rows = shipment_rows()
    rows[3][column] = value
    response = client.post('/api/sales/2026-11/preview', data={'file': (shipment_workbook(rows), '日报.xlsx')})
    assert response.status_code == 400
    assert '第 4 行' in response.get_json()['error']
    assert message in response.get_json()['error']
    assert client.get('/api/dashboard').get_json()['sales']['items'] == []


def test_preview_auth_headers_missing_month_and_sheet(client, app):
    """验证导入边界；参数为客户端及应用；断言鉴权、来源、工作表、月份和无数据提示。"""
    path = '/api/sales/2026-11/preview'
    assert app.test_client().post(path).status_code == 401
    assert client.post(path, headers={'Origin': 'https://elsewhere.test'}).status_code == 403
    assert client.post(path).status_code == 400
    response = client.post(path, data={'file': (shipment_workbook(sheet_name='其他'), '日报.xlsx')})
    assert response.status_code == 400 and '成品出库明细' in response.get_json()['error']
    response = client.post('/api/sales/2026-08/preview', data={'file': (shipment_workbook(), '日报.xlsx')})
    assert response.status_code == 400 and '没有 2026-08' in response.get_json()['error']
    with pytest.raises(ValidationError, match='日期|月份'):
        parse_shipment_rows(shipment_rows(), '2026-13')


def test_save_repeated_details_preserves_order_and_other_month(client):
    """验证明细保存；client 为客户端；断言相同行保留、整月替换、顺序稳定且不覆盖其他月份。"""
    assert client.put('/api/sales/2026-10', json={'version': 0, 'order_qty': 100,
        'items': [{'part_no': 'OLD', 'quantity': 55, 'shipment_date': '2026-10-31'}]}).status_code == 200
    items = parse_shipment_rows(shipment_rows(), '2026-11')['items']
    for version in (1, 2):
        assert client.put('/api/sales/2026-11', json={'version': version, 'order_qty': 5000, 'items': items}).status_code == 200
        saved = client.get('/api/editor').get_json()['sales']['items']
        assert [row['quantity'] for row in saved] == [800, 800, 400, 0]
        assert len({row['id'] for row in saved}) == 4
        items = saved
    data = client.get('/api/dashboard').get_json()['sales']
    assert len(data['items']) == 4 and data['shipped_qty'] == 2000 and data['achievement_rate'] == 40
    assert client.get('/api/editor?month=2026-10').get_json()['sales']['items'][0]['quantity'] == 55
    items[0]['shipment_date'] = '2026-12-01'
    response = client.put('/api/sales/2026-11', json={'version': 3, 'order_qty': 999, 'items': items})
    assert response.status_code == 400
    assert client.get('/api/dashboard').get_json()['sales']['order_qty'] == 5000


def test_old_unique_part_table_migrates_without_changing_existing_records(tmp_path, clock):
    """验证旧表升级；参数为临时目录及时间；断言标识数量保留、重复件号可保存、重启后结构稳定。"""
    path = tmp_path / 'legacy.sqlite3'
    with sqlite3.connect(path) as database:
        database.executescript("CREATE TABLE sales_month(month TEXT PRIMARY KEY,order_qty INTEGER);"
            "CREATE TABLE shipment_month_item(id INTEGER PRIMARY KEY AUTOINCREMENT,month TEXT NOT NULL "
            "REFERENCES sales_month(month) ON DELETE CASCADE,part_no TEXT NOT NULL COLLATE NOCASE,"
            "quantity INTEGER NOT NULL,UNIQUE(month,part_no));"
            "INSERT INTO sales_month VALUES('2026-11',1000);"
            "INSERT INTO shipment_month_item VALUES(7,'2026-11','LEGACY',200);"
            "UPDATE sqlite_sequence SET seq=30 WHERE name='shipment_month_item';")
    config = {'TESTING': True, 'DATABASE': str(path), 'BUSINESS_TIMEZONE': 'UTC', 'NOW_PROVIDER': lambda: clock['now']}
    app = create_app(config)
    client = app.test_client()
    assert client.post('/api/auth/login', json={'username': 'admin', 'password': 'admin123'}).status_code == 200
    original = client.get('/api/editor').get_json()['sales']['items'][0]
    assert (original['id'], original['quantity'], original['name'], original['shipment_date']) == (7, 200, '', None)
    assert client.put('/api/sales/2026-11', json={'version': 0, 'order_qty': 1000,
        'items': [original, {'part_no': 'LEGACY', 'name': '新明细', 'quantity': 300, 'shipment_date': '2026-11-01'}]}).status_code == 200
    saved = client.get('/api/dashboard').get_json()['sales']
    assert saved['items'][0]['id'] == 7 and saved['items'][1]['id'] > 30
    assert create_app(config).test_client().get('/api/dashboard').get_json()['sales'] == saved
    with connect_database(path) as database:
        assert database.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
