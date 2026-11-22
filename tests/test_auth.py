"""验证员工管理、会话及后台接口访问边界。"""

import pytest

from backend import create_app
from backend.db import connect_database


def employee_payload(client):
    """读取员工保存草稿；client 为已登录客户端；返回包含版本和完整员工列表的载荷。"""
    snapshot = client.get('/api/employees').get_json()
    return {'version': snapshot['version'], 'employees': snapshot['employees']}


def add_employee(client, username='worker'):
    """增加测试员工；client 为客户端、username 为账号；返回员工标识。"""
    payload = employee_payload(client)
    payload['employees'].append({'username': username, 'name': '张三', 'password': 'plain123', 'enabled': True})
    assert client.put('/api/employees', json=payload).status_code == 200
    return client.get('/api/employees').get_json()['employees'][-1]['id']


@pytest.mark.parametrize('method,path', [
    ('get', '/api/editor'), ('get', '/api/employees'), ('get', '/api/auth/session'),
    ('put', '/api/production/2026-11-21'), ('put', '/api/inventory'),
    ('put', '/api/sales/2026-11'), ('put', '/api/settings'), ('put', '/api/employees'),
    ('post', '/api/downtime/start'), ('post', '/api/downtime/1/finish'),
])
def test_anonymous_cannot_access_editor_or_mutate(app, method, path):
    """验证未登录边界；app、method、path 指定隔离应用及受保护接口；断言请求返回 401。"""
    anonymous = app.test_client()
    response = getattr(anonymous, method)(path, json={})
    assert response.status_code == 401
    assert '登录' in response.get_json()['error']
    assert anonymous.get('/api/dashboard').get_json()['version'] == 0


def test_login_logout_and_public_screen(app):
    """验证登录退出链路；app 为隔离应用；断言公开大屏、登录跳转、失败提示和退出后拦截。"""
    client = app.test_client()
    assert client.get('/edit').location == '/login'
    for path in ['/', '/screen', '/api/dashboard', '/api/health', '/login']:
        assert client.get(path).status_code == 200
    assert client.post('/api/auth/login', json={'username': 'admin', 'password': 'wrong'}).status_code == 401
    response = client.post('/api/auth/login', json={'username': 'ADMIN', 'password': 'admin123'})
    assert response.status_code == 200
    assert 'password' not in response.get_json()['employee']
    assert 'HttpOnly' in response.headers['Set-Cookie'] and 'SameSite=Lax' in response.headers['Set-Cookie']
    assert client.get('/login').location == '/edit'
    assert client.get('/edit').status_code == 200
    assert client.get('/edit').headers['Cache-Control'] == 'no-store'
    assert client.post('/api/auth/logout').status_code == 200
    assert client.get('/api/editor').status_code == 401


def test_employee_crud_and_plaintext_storage(client, app):
    """验证员工增改删；client、app 为登录客户端和应用；断言明文持久化、留空保留及删除后禁止登录。"""
    identifier = add_employee(client)
    with connect_database(app.config['DATABASE']) as db:
        assert db.execute('SELECT password FROM employee WHERE id=?', (identifier,)).fetchone()['password'] == 'plain123'
    payload = employee_payload(client)
    payload['employees'][-1]['name'] = '李四'
    assert payload['employees'][-1]['password'] == ''
    assert client.put('/api/employees', json=payload).status_code == 200
    worker = app.test_client()
    assert worker.post('/api/auth/login', json={'username': 'worker', 'password': 'plain123'}).status_code == 200
    assert worker.get('/api/editor').status_code == 200
    payload = employee_payload(client)
    payload['employees'].pop()
    assert client.put('/api/employees', json=payload).status_code == 200
    assert worker.get('/api/editor').status_code == 401
    assert worker.post('/api/auth/login', json={'username': 'worker', 'password': 'plain123'}).status_code == 401


@pytest.mark.parametrize('change', ['disable', 'password'])
def test_account_changes_invalidate_existing_sessions(client, app, change):
    """验证停用及改密失效；change 为变更类型；断言旧会话失效，重新启用不恢复旧会话。"""
    add_employee(client)
    worker = app.test_client()
    worker.post('/api/auth/login', json={'username': 'worker', 'password': 'plain123'})
    old_cookie = worker.get_cookie('session').value
    payload = employee_payload(client)
    if change == 'disable':
        payload['employees'][-1]['enabled'] = False
    else:
        payload['employees'][-1]['password'] = 'new password'
    assert client.put('/api/employees', json=payload).status_code == 200
    assert worker.put('/api/inventory', json={}).status_code == 401
    if change == 'disable':
        assert worker.post('/api/auth/login', json={'username': 'worker', 'password': 'plain123'}).status_code == 401
        payload = employee_payload(client)
        payload['employees'][-1]['enabled'] = True
        assert client.put('/api/employees', json=payload).status_code == 200
    worker.set_cookie('session', old_cookie)
    assert worker.get('/api/editor').status_code == 401
    password = 'plain123' if change == 'disable' else 'new password'
    assert worker.post('/api/auth/login', json={'username': 'worker', 'password': password}).status_code == 200


def test_self_protection_and_other_sessions_invalidated_on_password_change(client, app):
    """验证当前账号保护；client、app 为客户端和应用；断言禁止自删停用，改密保留当前会话且撤销其他会话。"""
    other = app.test_client()
    other.post('/api/auth/login', json={'username': 'admin', 'password': 'admin123'})
    payload = employee_payload(client)
    payload['employees'][0]['enabled'] = False
    assert client.put('/api/employees', json=payload).status_code == 400
    payload['employees'] = []
    assert client.put('/api/employees', json=payload).status_code == 400
    payload = employee_payload(client)
    payload['employees'][0]['password'] = 'changed'
    assert client.put('/api/employees', json=payload).status_code == 200
    assert client.get('/api/auth/session').status_code == 200
    assert other.get('/api/auth/session').status_code == 401
    restarted = create_app(dict(app.config)).test_client()
    assert restarted.post('/api/auth/login', json={'username': 'admin', 'password': 'admin123'}).status_code == 401
    assert restarted.post('/api/auth/login', json={'username': 'admin', 'password': 'changed'}).status_code == 200


@pytest.mark.parametrize('field,value', [('username', 'ADMIN'), ('username', 'bad account'), ('name', ''),
                                        ('password', ''), ('enabled', 1), ('id', 12345)])
def test_invalid_employee_batch_is_atomic(client, field, value):
    """验证员工字段及事务；field/value 为错误输入；断言完整批次失败且原账号和版本保持不变。"""
    payload = employee_payload(client)
    payload['employees'][0]['name'] = '不能写入'
    employee = {'username': 'worker', 'name': '张三', 'password': 'secret', 'enabled': True}
    employee[field] = value
    payload['employees'].append(employee)
    assert client.put('/api/employees', json=payload).status_code == 400
    snapshot = client.get('/api/employees').get_json()
    assert snapshot['version'] == 0
    assert len(snapshot['employees']) == 1 and snapshot['employees'][0]['name'] == '管理员'


def test_employee_conflict_and_cross_origin_writes(client):
    """验证版本与同源写入；client 为登录客户端；断言旧版本及跨站请求被拒绝。"""
    payload = employee_payload(client)
    assert client.put('/api/employees', json=payload).status_code == 200
    assert client.put('/api/employees', json=payload).status_code == 409
    assert client.put('/api/employees', json=payload, headers={'Origin': 'https://other.example'}).status_code == 403


def test_legacy_database_upgrade_preserves_business_data(client, app):
    """验证旧库升级；client、app 为客户端和应用；断言补建账号后业务数据与版本完全保留。"""
    assert client.put('/api/sales/2026-11', json={
        'version': 0, 'order_qty': 500, 'items': [{'part_no': 'P001', 'quantity': 123}]}).status_code == 200
    before = client.get('/api/dashboard').get_json()
    with connect_database(app.config['DATABASE']) as db:
        db.execute('DROP TABLE employee')
        db.execute('DROP TABLE auth_settings')
        db.execute('PRAGMA user_version=1')
    upgraded = create_app({'TESTING': True, 'DATABASE': app.config['DATABASE'],
                           'BUSINESS_TIMEZONE': 'UTC', 'NOW_PROVIDER': app.config['NOW_PROVIDER']}).test_client()
    assert upgraded.get('/api/dashboard').get_json() == before
    assert upgraded.post('/api/auth/login', json={'username': 'admin', 'password': 'admin123'}).status_code == 200


def test_restart_preserves_existing_session_signature(client, app):
    """验证重启会话密钥持久化；client、app 为客户端和应用；断言新应用仍接受原 Cookie。"""
    restarted = create_app({'TESTING': True, 'DATABASE': app.config['DATABASE'],
                            'BUSINESS_TIMEZONE': 'UTC', 'NOW_PROVIDER': app.config['NOW_PROVIDER']}).test_client()
    restarted.set_cookie('session', client.get_cookie('session').value)
    assert restarted.get('/api/auth/session').status_code == 200
