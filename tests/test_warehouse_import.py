"""验证一次上传同时预览和原子保存库存及当月逐条出货。"""

from io import BytesIO
from pathlib import Path
from copy import deepcopy

from openpyxl import Workbook

from tests.test_inventory_import import summary_rows
from tests.test_shipment_import import shipment_rows


def warehouse_workbook(inventory_rows=None, sales_rows=None):
    """创建完整仓库日报；参数为两类源行；返回含汇总表及成品出库明细的内存工作簿。"""
    book = Workbook()
    inventory = book.active
    inventory.title = '汇总表'
    for row in inventory_rows if inventory_rows is not None else summary_rows():
        inventory.append(row)
    shipments = book.create_sheet('成品出库明细')
    for row in sales_rows if sales_rows is not None else shipment_rows():
        shipments.append(row)
    output = BytesIO()
    book.save(output)
    book.close()
    output.seek(0)
    return output


def preview_warehouse(client):
    """获取两表预览；client 为登录客户端；返回成功解析后的预览 JSON。"""
    response = client.post('/api/warehouse/2026-11/preview', data={'file': (warehouse_workbook(), '仓库日报.xlsx')})
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def import_payload(preview):
    """生成确认载荷；preview 为两表预览；返回库存和逐条出货的同版本保存数据。"""
    return {'version': preview['version'], 'inventory': preview['inventory'], 'shipments': preview['shipments']}


def test_one_upload_previews_both_tables_in_memory_without_writes(client, app, monkeypatch):
    """验证统一上传；参数为客户端、应用和补丁；断言一次上传提取两表、流关闭且无版本和文件变化。"""
    import backend
    before = client.get('/api/editor').get_json()
    files = set(Path(app.config['DATABASE']).parent.iterdir())
    streams = []
    parser = backend.parse_warehouse_file

    def capture(upload, month):
        """核对上传载体；upload 为文件流、month 为月份；返回解析结果并记录流以验证释放。"""
        assert isinstance(upload.stream, BytesIO)
        streams.append(upload.stream)
        return parser(upload, month)

    monkeypatch.setattr(backend, 'parse_warehouse_file', capture)
    result = preview_warehouse(client)
    assert len(result['inventory']['raw']) == 3 and len(result['inventory']['semi']) == 3
    assert [item['quantity'] for item in result['shipments']['items']] == [800, 800, 400, 0]
    assert result['existing']['sales']['order_qty'] is None
    assert result['version'] == 0 and result['month'] == '2026-11'
    assert len(streams) == 1 and streams[0].closed
    assert client.get('/api/editor').get_json() == before
    assert set(Path(app.config['DATABASE']).parent.iterdir()) == files


def test_confirm_import_saves_both_once_preserves_orders_other_month_and_audit(client):
    """验证统一保存；client 为客户端；断言单次版本递增、订单保留、月份隔离及两模块均记录录入人。"""
    for version, month in enumerate(['2026-10', '2026-11']):
        assert client.put('/api/sales/' + month, json={'version': version, 'order_qty': 5000,
            'items': [{'part_no': 'ORIGINAL', 'quantity': 5}]}).status_code == 200
    preview = preview_warehouse(client)
    payload = import_payload(preview)
    payload['shipments']['order_qty'] = 999
    response = client.post('/api/warehouse/2026-11/import', json=payload)
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['version'] == 3
    saved = client.get('/api/editor').get_json()
    assert len(saved['inventory']['raw']) == 3 and len(saved['inventory']['semi']) == 3
    assert len(saved['sales']['items']) == 4 and saved['sales']['order_qty'] == 5000
    assert len({item['id'] for item in saved['sales']['items']}) == 4
    assert all(row['_audit']['created_by_name'] == '管理员' for rows in
        (saved['inventory']['raw'], saved['inventory']['semi'], saved['sales']['items']) for row in rows)
    assert client.get('/api/editor?month=2026-10').get_json()['sales']['items'][0]['part_no'] == 'ORIGINAL'
    assert client.get('/api/dashboard').get_json()['sales']['shipped_qty'] == 2000


def test_invalid_second_table_or_stale_version_rolls_back_inventory_and_sales(client):
    """验证原子性；client 为客户端；断言后处理出货失败及并发冲突时库存、出货、人员记录和版本不变。"""
    preview = preview_warehouse(client)
    payload = import_payload(preview)
    payload['shipments'] = deepcopy(payload['shipments'])
    payload['shipments']['items'][0]['quantity'] = -1
    before = client.get('/api/editor').get_json()
    response = client.post('/api/warehouse/2026-11/import', json=payload)
    assert response.status_code == 400
    assert client.get('/api/editor').get_json() == before
    assert client.put('/api/sales/2026-11', json={'version': 0, 'order_qty': 25, 'items': []}).status_code == 200
    before = client.get('/api/editor').get_json()
    response = client.post('/api/warehouse/2026-11/import', json=import_payload(preview))
    assert response.status_code == 409
    assert client.get('/api/editor').get_json() == before


def test_bad_second_sheet_rejects_whole_preview_and_requires_auth(client, app):
    """验证预览整批拒绝及安全边界；参数为客户端和应用；断言错误源行、鉴权和来源限制。"""
    rows = shipment_rows()
    rows[3][4] = -1
    path = '/api/warehouse/2026-11/preview'
    response = client.post(path, data={'file': (warehouse_workbook(sales_rows=rows), '日报.xlsx')})
    assert response.status_code == 400 and '第 4 行' in response.get_json()['error']
    assert client.get('/api/editor').get_json()['version'] == 0
    for route in (path, '/api/warehouse/2026-11/import'):
        assert app.test_client().post(route).status_code == 401
        assert client.post(route, headers={'Origin': 'https://elsewhere.test'}).status_code == 403


def test_actual_user_workbook_single_upload_reads_inventory_and_seventeen_shipments(client):
    """验证真实日报统一解析；client 为客户端；断言八条原料、十条半成品与十一月十七条出货。"""
    import pytest
    source = Path(__file__).resolve().parents[1] / 'docs' / '仓库11月份日报表 - 11.17.xls'
    if not source.exists():
        pytest.skip('当前环境没有用户提供的日报文件')
    with source.open('rb') as stream:
        response = client.post('/api/warehouse/2026-11/preview', data={'file': (stream, source.name)})
    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    assert len(data['inventory']['raw']) == 8 and len(data['inventory']['semi']) == 10
    assert len(data['shipments']['items']) == 17 and data['shipments']['shipped_qty'] == 15500
