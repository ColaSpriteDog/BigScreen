"""员工账号与登录会话；密码按现场要求使用明文保存。"""

import re
import secrets

from flask import g, session

from .db import connect_database
from .domain import ValidationError
from . import service


def initialize_auth(path):
    """初始化登录配置；path 为数据库路径；返回持久化会话密钥，仅首次创建默认员工。"""
    with connect_database(path) as db:
        db.execute("BEGIN IMMEDIATE")
        config = db.execute("SELECT secret_key FROM auth_settings WHERE id=1").fetchone()
        if config is None:
            secret = secrets.token_hex(32)
            db.execute("INSERT INTO auth_settings(id,secret_key) VALUES(1,?)", (secret,))
            db.execute("INSERT INTO employee(username,name,password) VALUES('admin','管理员','admin123')")
        else:
            secret = config["secret_key"]
    db.close()
    return secret


def public_employee(employee):
    """筛选员工展示信息；employee 为数据库记录；返回不含密码和会话版本的字典。"""
    return {key: employee[key] for key in ("id", "username", "name", "enabled")}


def current_employee(db):
    """校验当前会话；db 为数据库连接；返回仍启用且版本一致的员工记录，否则返回空值。"""
    employee = db.execute("SELECT * FROM employee WHERE id=?", (session.get("employee_id"),)).fetchone()
    if employee and employee["enabled"] and employee["auth_version"] == session.get("auth_version"):
        return employee
    session.clear()
    return None


def establish_session(employee):
    """建立登录会话；employee 为已校验账号；无返回值，Cookie 仅记录标识及会话版本。"""
    session.clear()
    session.permanent = True
    session.update(employee_id=employee["id"], auth_version=employee["auth_version"])


def employee_snapshot(db):
    """读取员工列表；db 为数据库连接；返回带全局版本的员工表单，已有密码不回传。"""
    config = service.settings(db)
    employees = [dict(public_employee(row), enabled=bool(row["enabled"]), password="")
                 for row in db.execute("SELECT * FROM employee ORDER BY id")]
    return {"version": config["version"], "updated_at": config["updated_at"], "employees": employees}


def save_employees(db, payload):
    """原子保存员工；db 为事务连接、payload 含完整 employees 列表；无返回值，空旧密码表示保留。"""
    items = service.array_value(payload.get("employees"), "员工列表", limit=500)
    existing = {row["id"]: dict(row) for row in db.execute("SELECT * FROM employee")}
    identifiers, usernames, prepared = set(), set(), []
    for item in items:
        identifier = item.get("id")
        if identifier is not None:
            if type(identifier) is not int or identifier not in existing or identifier in identifiers:
                raise ValidationError("员工记录不存在或重复，请重新加载")
            identifiers.add(identifier)
        username, name = item.get("username"), item.get("name")
        if not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", username):
            raise ValidationError("登录账号需为 1～40 位字母、数字、下划线、点或短横线")
        if username.lower() in usernames:
            raise ValidationError("登录账号不能重复（不区分大小写）")
        usernames.add(username.lower())
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 40:
            raise ValidationError("员工姓名不能为空且不能超过 40 字")
        password = item.get("password", "")
        if not isinstance(password, str) or len(password) > 128 or (identifier is None and not password):
            raise ValidationError("新员工必须设置密码，密码不能超过 128 字")
        enabled = item.get("enabled")
        if type(enabled) is not bool:
            raise ValidationError("员工状态必须为启用或停用")
        if identifier == g.employee["id"] and not enabled:
            raise ValidationError("不能停用当前登录账号")
        previous = existing.get(identifier)
        final_password = password or (previous["password"] if previous else "")
        revision = previous["auth_version"] if previous else 0
        if previous and (final_password != previous["password"] or enabled != bool(previous["enabled"])):
            revision += 1
        prepared.append((identifier, username, name.strip(), final_password, int(enabled), revision))
    if g.employee["id"] not in identifiers:
        raise ValidationError("不能删除当前登录账号")
    # 先删除未保留员工并临时释放账号，允许在同批保存中交换账号名称。
    for identifier in existing:
        if identifier not in identifiers:
            db.execute("DELETE FROM employee WHERE id=?", (identifier,))
        else:
            db.execute("UPDATE employee SET username=? WHERE id=?", ("~" + str(identifier), identifier))
    for identifier, username, name, password, enabled, revision in prepared:
        if identifier is None:
            db.execute("INSERT INTO employee(username,name,password,enabled) VALUES(?,?,?,?)",
                       (username, name, password, enabled))
        else:
            db.execute("UPDATE employee SET username=?,name=?,password=?,enabled=?,auth_version=? WHERE id=?",
                       (username, name, password, enabled, revision, identifier))
