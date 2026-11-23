"""验证自动记录人员、逐行实际变更判断和历史数据兼容。"""

from datetime import timedelta

from backend import create_app
from backend.db import connect_database
from tests.test_api import production_payload, settings_payload
from tests.test_auth import add_employee, employee_payload


def save_inventory(client, inventory):
    """按当前版本保存库存；client 为登录客户端、inventory 为完整草稿；返回响应供测试断言。"""
    return client.put('/api/inventory', json={
        'version': client.get('/api/editor').get_json()['version'], **inventory})


def inventory_example():
    """构造两行库存；无参数；返回可用于验证逐行修改的完整表单。"""
    return {'raw': [{'material': '钢卷', 'warehouse': '原材料仓库', 'specification': spec,
                     'position': '', 'quantity': 1, 'unit': '卷', 'weight_kg': None, 'cuttable_qty': None}
                    for spec in ['1.2', '1.4']], 'semi': []}


def worker_client(client, app):
    """创建第二位操作员工；client、app 为管理员客户端及应用；返回已登录员工客户端。"""
    add_employee(client)
    worker = app.test_client()
    assert worker.post('/api/auth/login', json={'username': 'worker', 'password': 'plain123'}).status_code == 200
    return worker


def test_inventory_records_only_changed_rows_and_ignores_forged_actor(client, app, clock):
    """验证逐行人员记录；客户端、应用及时间为参数；断言创建不变、仅修改行更新、伪造人员无效。"""
    worker = worker_client(client, app)
    assert save_inventory(client, inventory_example()).status_code == 200
    original = client.get('/api/editor').get_json()['inventory']
    created = original['raw'][0]['_audit']
    assert created['created_by_name'] == created['modified_by_name'] == '管理员'
    assert created['created_at'] == created['modified_at'] == clock['now'].isoformat()
    clock['now'] += timedelta(minutes=10)
    original['raw'][0]['quantity'] = 5
    original['raw'][0]['_audit'] = {'created_by_name': '伪造人', 'modified_by_name': '伪造人'}
    assert save_inventory(worker, original).status_code == 200
    current = client.get('/api/editor').get_json()['inventory']
    changed = current['raw'][0]['_audit']
    assert changed['created_by_name'] == '管理员'
    assert changed['created_at'] == created['created_at']
    assert changed['modified_by_name'] == '张三' and changed['modified_at'] == clock['now'].isoformat()
    assert current['raw'][1]['_audit'] == original['raw'][1]['_audit']
    clock['now'] += timedelta(minutes=10)
    assert save_inventory(client, current).status_code == 200
    assert client.get('/api/editor').get_json()['inventory'] == current
    assert '_audit' not in client.get('/api/dashboard').get_json()['inventory']['raw'][0]


def test_employee_rename_and_deletion_preserve_historical_names(client, app, clock):
    """验证员工姓名快照；参数为客户端、应用和时钟；断言改名及删除不会篡改已有记录归属。"""
    worker = worker_client(client, app)
    assert save_inventory(worker, inventory_example()).status_code == 200
    original = client.get('/api/editor').get_json()['inventory']
    payload = employee_payload(client)
    payload['employees'][-1]['name'] = '李四'
    assert client.put('/api/employees', json=payload).status_code == 200
    clock['now'] += timedelta(minutes=1)
    original['raw'][0]['quantity'] = 9
    assert save_inventory(worker, original).status_code == 200
    payload = employee_payload(client)
    payload['employees'].pop()
    assert client.put('/api/employees', json=payload).status_code == 200
    current = client.get('/api/editor').get_json()['inventory']['raw']
    assert current[0]['_audit']['created_by_name'] == '张三'
    assert current[0]['_audit']['modified_by_name'] == '李四'
    assert current[1]['_audit']['modified_by_name'] == '张三'


def test_daily_empty_placeholders_and_overview_are_independent(client, app, clock):
    """验证日报及全局表单；参数为客户端、应用和时钟；断言空白行未保存、首次填写才记录人、全局指标独立更新。"""
    worker = worker_client(client, app)
    draft = production_payload(client)
    draft['lines'][0]['actual_qty'] = 100
    assert client.put('/api/production/2026-11-21', json=draft).status_code == 200
    data = client.get('/api/editor').get_json()['production']
    assert data['lines'][0]['_audit']['created_by_name'] == '管理员'
    assert data['lines'][1]['_audit']['state'] == 'unsaved'
    assert data['overview']['_audit']['state'] == 'unsaved'
    first = data['lines'][0]['_audit']
    clock['now'] += timedelta(minutes=5)
    draft = production_payload(worker)
    draft['lines'][1]['actual_qty'] = 0
    draft['lines'][1]['abnormal_hours'] = 1
    assert worker.put('/api/production/2026-11-21', json=draft).status_code == 200
    data = client.get('/api/editor').get_json()['production']
    assert data['lines'][0]['_audit'] == first
    assert data['lines'][1]['_audit']['created_by_name'] == '张三'
    assert data['lines'][1]['_audit']['created_by_name'] == '张三'
    assert data['lines'][1]['_audit']['created_at'] == clock['now'].isoformat()


def test_sales_preserves_record_ids_and_only_updates_changed_parts(client, app, clock):
    """验证销售归属稳定；参数为客户端、应用和时钟；断言排序、无修改保存、单件号改名不改变其他行归属。"""
    worker = worker_client(client, app)
    version = client.get('/api/editor').get_json()['version']
    assert client.put('/api/sales/2026-11', json={'version': version, 'order_qty': 100,
        'items': [{'part_no': 'A', 'quantity': 5}, {'part_no': 'B', 'quantity': 10}]}).status_code == 200
    original = client.get('/api/editor').get_json()['sales']
    clock['now'] += timedelta(minutes=5)
    items = original['items'][::-1]
    items[1] = {**items[1], 'part_no': 'A-new', 'quantity': 20}
    version = client.get('/api/editor').get_json()['version']
    assert worker.put('/api/sales/2026-11', json={'version': version, 'order_qty': 100, 'items': items}).status_code == 200
    data = client.get('/api/editor').get_json()['sales']
    assert data['_audit'] == original['_audit']
    changed = next(row for row in data['items'] if row['part_no'] == 'A-new')
    untouched = next(row for row in data['items'] if row['part_no'] == 'B')
    assert changed['id'] == items[1]['id']
    assert changed['_audit']['created_by_name'] == '管理员' and changed['_audit']['modified_by_name'] == '张三'
    assert untouched['_audit'] == original['items'][1]['_audit']
    # 旧客户端没有记录 ID 时也按月份和件号保留现有身份。
    legacy = [{key: row[key] for key in ('part_no', 'quantity')} for row in data['items']]
    version = client.get('/api/editor').get_json()['version']
    assert client.put('/api/sales/2026-11', json={'version': version, 'order_qty': 100, 'items': legacy}).status_code == 200
    assert client.get('/api/editor').get_json()['sales'] == data


def test_settings_configuration_and_process_attribution(client, app, clock):
    """验证设置归属；参数为客户端、应用和时钟；断言只更新修改的产线和节点，业务设置未变仍为历史数据。"""
    worker = worker_client(client, app)
    payload = settings_payload(client)
    payload['lines'][0]['name'] = '测试产线'
    payload['processes'][0]['manual_status'] = 'maintenance'
    assert worker.put('/api/settings', json=payload).status_code == 200
    data = client.get('/api/editor').get_json()
    assert data['settings']['_audit']['state'] == 'legacy'
    line = data['production']['lines'][0]
    assert line['_configuration_audit']['created_at'] is None
    assert line['_configuration_audit']['modified_by_name'] == '张三'
    assert data['production']['lines'][1]['_configuration_audit']['state'] == 'legacy'
    assert data['processes'][0]['_audit']['modified_by_name'] == '张三'
    assert data['processes'][4]['_audit']['state'] == 'legacy'
    before = line['_configuration_audit']
    clock['now'] += timedelta(minutes=1)
    assert client.put('/api/settings', json=settings_payload(client)).status_code == 200
    assert client.get('/api/editor').get_json()['production']['lines'][0]['_configuration_audit'] == before


def test_instant_downtime_start_finish_keeps_creator(client, app, clock):
    """验证快捷停机归属；参数为客户端、应用和时钟；断言开始记录录入人，恢复仅更新修改人。"""
    worker = worker_client(client, app)
    version = client.get('/api/editor').get_json()['version']
    response = worker.post('/api/downtime/start', json={'version': version, 'line_id': 1, 'kind': 'stopped'})
    assert response.status_code == 200
    identifier = response.get_json()['result']
    clock['now'] += timedelta(minutes=1)
    assert client.post(f'/api/downtime/{identifier}/finish', json={'version': response.get_json()['version']}).status_code == 200
    meta = client.get('/api/editor').get_json()['production']['downtimes'][0]['_audit']
    assert meta['created_by_name'] == '张三' and meta['modified_by_name'] == '管理员'
    assert meta['created_at'] != meta['modified_at']


def test_invalid_batch_and_version_conflict_do_not_change_attribution(client, app, clock):
    """验证人员信息与业务原子回滚；客户端、应用和时钟为参数；断言晚期保存失败及版本冲突均保留原归属。"""
    assert save_inventory(client, inventory_example()).status_code == 200
    before = client.get('/api/editor').get_json()['inventory']
    clock['now'] += timedelta(minutes=1)
    changed = client.get('/api/editor').get_json()['inventory']
    changed['raw'][0]['quantity'] = 50
    semi = {'category': '左件', 'material': '测试', 'warehouse': '仓库', 'specification': '1', 'quantity': 1, 'unit': '个'}
    changed['semi'] = [semi, semi]
    assert save_inventory(client, changed).status_code == 400
    assert client.get('/api/editor').get_json()['inventory'] == before
    assert client.put('/api/inventory', json={'version': 0, **changed}).status_code == 409
    assert client.get('/api/editor').get_json()['inventory'] == before


def test_delete_copy_and_legacy_upgrade_preserve_unknown_creator(client, app, clock):
    """验证旧库升级和复制删除；参数为客户端、应用和时钟；断言旧数据不补造录入人，复制新建归属且删除清理。"""
    assert save_inventory(client, inventory_example()).status_code == 200
    with connect_database(app.config['DATABASE']) as db:
        db.execute('DROP TABLE record_attribution')
        db.execute('PRAGMA user_version=2')
    upgraded = create_app({'TESTING': True, 'DATABASE': app.config['DATABASE'],
                          'BUSINESS_TIMEZONE': 'UTC', 'NOW_PROVIDER': app.config['NOW_PROVIDER']}).test_client()
    upgraded.post('/api/auth/login', json={'username': 'admin', 'password': 'admin123'})
    original = upgraded.get('/api/editor').get_json()['inventory']
    assert original['raw'][0]['_audit']['state'] == 'legacy'
    assert save_inventory(upgraded, original).status_code == 200
    assert upgraded.get('/api/editor').get_json()['inventory']['raw'][0]['_audit']['state'] == 'legacy'
    original['raw'][0]['quantity'] = 3
    assert save_inventory(upgraded, original).status_code == 200
    meta = upgraded.get('/api/editor').get_json()['inventory']['raw'][0]['_audit']
    assert meta['created_by_name'] is None and meta['created_at'] is None
    current = upgraded.get('/api/editor').get_json()['inventory']
    copied = {**current['raw'][0], 'specification': '新复制行'}
    copied.pop('id')
    current['raw'] = [copied]
    assert save_inventory(upgraded, current).status_code == 200
    meta = upgraded.get('/api/editor').get_json()['inventory']['raw'][0]['_audit']
    assert meta['created_by_name'] == '管理员'
    with connect_database(app.config['DATABASE']) as db:
        assert db.execute("SELECT COUNT(*) FROM record_attribution WHERE record_type='raw_material_stock'").fetchone()[0] == 1


def test_employee_account_changes_have_attribution_without_password_leak(client, app, clock):
    """验证员工账号表归属；参数为客户端、应用和时钟；断言创建改密记录人员，接口不回传已有密码。"""
    add_employee(client)
    payload = employee_payload(client)
    original = payload['employees'][-1]['_audit']
    assert original['created_by_name'] == '管理员'
    clock['now'] += timedelta(minutes=1)
    payload['employees'][-1]['password'] = 'new-pass'
    assert client.put('/api/employees', json=payload).status_code == 200
    account = client.get('/api/employees').get_json()['employees'][-1]
    assert account['password'] == ''
    assert account['_audit']['created_at'] == original['created_at']
    assert account['_audit']['modified_at'] == clock['now'].isoformat()
