"""Flask 应用工厂，提供同源页面及结构化数据接口。"""

from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
import os
import sqlite3
from zoneinfo import ZoneInfo

from flask import Flask, Request, g, jsonify, redirect, request, send_from_directory, session
from werkzeug.exceptions import HTTPException

from .db import close_db, get_db, initialize_database
from .domain import ValidationError
from .inventory_import import MAX_FILE_BYTES, parse_inventory_file
from .shipment_import import parse_shipment_file
from .warehouse_import import parse_warehouse_file
from .production_import import parse_production_file
from . import audit, auth, service


class InventoryRequest(Request):
    """让库存和出货上传仅驻留内存，避免默认 multipart 解析器为大文件创建临时磁盘文件。"""

    def _get_file_stream(self, total_content_length, content_type, filename=None, content_length=None):
        """创建上传流；参数由 Werkzeug 提供文件元数据；Excel 预览返回内存流，其他请求沿用默认值。"""
        if self.path == "/api/inventory/preview" or (self.path.startswith(("/api/sales/", "/api/production/", "/api/warehouse/")) and self.path.endswith("/preview")):
            return BytesIO()
        return super()._get_file_stream(total_content_length, content_type, filename, content_length)


def utc_now():
    """获取当前时间；无参数；返回带 UTC 时区的 datetime。"""
    return datetime.now(timezone.utc)


def create_app(configuration=None):
    """创建应用；configuration 可覆盖数据库、时区和测试时钟；返回已初始化的 Flask 实例。"""
    root = Path(__file__).resolve().parents[1]
    app = Flask(__name__, static_folder=str(root / "static"), static_url_path="/static")
    app.request_class = InventoryRequest
    app.config.update(DATABASE=os.environ.get("BIGSCREEN_DATABASE", str(root / "data" / "dashboard.sqlite3")),
                      BUSINESS_TIMEZONE=os.environ.get("BIGSCREEN_TIMEZONE", "Asia/Shanghai"),
                      NOW_PROVIDER=utc_now, MAX_CONTENT_LENGTH=2 * 1024 * 1024,
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                      PERMANENT_SESSION_LIFETIME=timedelta(hours=12))
    if configuration:
        app.config.update(configuration)
    ZoneInfo(app.config["BUSINESS_TIMEZONE"])
    app.json.ensure_ascii = False
    initialize_database(app.config["DATABASE"], app.config["BUSINESS_TIMEZONE"], app.config["NOW_PROVIDER"]().isoformat())
    stored_secret = auth.initialize_auth(app.config["DATABASE"])
    app.secret_key = app.config.get("SECRET_KEY") or stored_secret
    app.teardown_appcontext(close_db)

    @app.before_request
    def require_login():
        """保护后台页面及接口；无参数；未登录返回跳转或 401，跨站写入返回 403。"""
        if request.path.startswith("/api/") and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("Origin")
            if origin and origin != request.host_url.rstrip("/"):
                return jsonify(error="请在本站页面执行此操作"), 403
        public = {"/api/health", "/api/dashboard", "/api/auth/login"}
        if request.path == "/edit" or (request.path.startswith("/api/") and request.path not in public):
            g.employee = auth.current_employee(get_db())
            if g.employee is None:
                if request.path == "/edit":
                    return redirect("/login")
                return jsonify(error="登录已失效，请重新登录"), 401

    @app.after_request
    def response_headers(response):
        """设置响应头；response 为待发送响应；返回禁止接口缓存并防止内容嗅探的响应。"""
        if request.path.startswith("/api/") or request.path in {"/edit", "/login"}:
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.errorhandler(ValidationError)
    def validation_error(error):
        """输出业务校验错误；error 为校验异常；返回 400 JSON 响应。"""
        return jsonify(error=str(error)), 400

    @app.errorhandler(service.ConflictError)
    def conflict_error(error):
        """输出并发冲突；error 为版本异常；返回 409 JSON 响应。"""
        return jsonify(error=str(error)), 409

    @app.errorhandler(sqlite3.IntegrityError)
    def integrity_error(error):
        """处理约束失败；error 为 SQLite 异常；返回不泄露内部 SQL 的 400 响应。"""
        return jsonify(error="存在重复记录或字段约束不满足，请检查物料、仓库、规格及件号"), 400

    @app.errorhandler(sqlite3.OperationalError)
    def database_error(error):
        """处理数据库不可用；error 为 SQLite 操作异常；返回可重试的 503 响应。"""
        app.logger.error("数据库操作失败：%s", error)
        return jsonify(error="数据库暂时不可用，请稍后重试，当前输入未保存"), 503

    @app.errorhandler(HTTPException)
    def http_error(error):
        """统一接口 HTTP 错误；error 为协议异常；接口返回中文 JSON，页面保留正常 HTTP 响应。"""
        if request.path.startswith("/api/"):
            labels = {400: "请求格式不正确", 404: "接口不存在", 405: "请求方法不支持",
                      413: "提交内容过大", 415: "请使用 JSON 格式提交"}
            return jsonify(error=labels.get(error.code, "请求失败")), error.code
        return error

    def mutate(writer, *arguments):
        """执行写接口；writer 为业务保存函数、arguments 为附加参数；返回保存后的版本号。"""
        payload = service.object_value(request.get_json())
        db = get_db()
        now = app.config["NOW_PROVIDER"]()
        with service.versioned_write(db, payload, now, g.employee, audit.write_scope(writer.__name__, arguments)):
            result = writer(db, payload, *arguments, now) if writer in {
                service.save_production, service.save_production_import, service.save_settings, service.start_downtime} else writer(db, payload, *arguments)
        return jsonify(version=service.settings(db)["version"], updated_at=now.isoformat(), result=result)

    @app.get("/")
    @app.get("/screen")
    def screen_page():
        """提供联网大屏；无参数；返回新增的大屏 HTML，保留原始参考文件。"""
        return send_from_directory(root / "templates", "screen.html")

    @app.get("/edit")
    def editor_page():
        """提供编辑页面；无参数；返回 Vue 静态页面。"""
        return send_from_directory(root / "templates", "editor.html")

    @app.get("/login")
    def login_page():
        """提供登录页；无参数；已登录跳转编辑页，否则返回登录表单。"""
        if auth.current_employee(get_db()):
            return redirect("/edit")
        return send_from_directory(root / "templates", "login.html")

    @app.post("/api/auth/login")
    def login():
        """登录员工账号；JSON 提供 username/password；返回员工信息并建立会话。"""
        payload = service.object_value(request.get_json())
        username, password = payload.get("username"), payload.get("password")
        if not isinstance(username, str) or not isinstance(password, str):
            raise ValidationError("请输入登录账号和密码")
        employee = get_db().execute("SELECT * FROM employee WHERE username=?", (username.strip(),)).fetchone()
        if not employee or not employee["enabled"] or employee["password"] != password:
            return jsonify(error="账号或密码错误，或账号已停用"), 401
        auth.establish_session(employee)
        return jsonify(employee=auth.public_employee(employee))

    @app.get("/api/auth/session")
    def login_session():
        """读取当前登录员工；无参数；返回已通过会话校验的员工信息。"""
        return jsonify(employee=auth.public_employee(g.employee))

    @app.post("/api/auth/logout")
    def logout():
        """退出登录；无参数；清除浏览器会话并返回成功状态。"""
        session.clear()
        return jsonify(status="ok")

    @app.get("/api/employees")
    def employees():
        """读取员工管理表；无参数；返回员工列表及保存版本，密码不回传。"""
        db = get_db()
        with service.consistent_read(db):
            result = auth.employee_snapshot(db)
        return jsonify(result)

    @app.put("/api/employees")
    def employees_write():
        """保存完整员工列表；JSON 提供 version/employees；返回新版本并保留当前合法会话。"""
        result = mutate(auth.save_employees)
        employee = get_db().execute("SELECT * FROM employee WHERE id=?", (g.employee["id"],)).fetchone()
        auth.establish_session(employee)
        return result

    @app.get("/assets/<path:filename>")
    def asset_file(filename):
        """读取工序及品牌图片；filename 为受路径保护的文件名；返回图片响应。"""
        return send_from_directory(root / "assets", filename)

    @app.get("/api/health")
    def health():
        """检查服务和数据库；无参数；返回就绪状态。"""
        get_db().execute("SELECT 1").fetchone()
        return jsonify(status="ok")

    @app.get("/api/dashboard")
    def dashboard():
        """获取大屏数据；无参数；返回当前业务日的实时计算结果。"""
        db = get_db()
        with service.consistent_read(db):
            result = service.dashboard_snapshot(db, app.config["NOW_PROVIDER"]())
        return jsonify(result)

    @app.get("/api/editor")
    def editor_data():
        """读取表单；date、month 查询参数选择日报和销售月份；返回原始录入值及版本。"""
        db = get_db()
        with service.consistent_read(db):
            result = service.editor_snapshot(db, app.config["NOW_PROVIDER"](), request.args.get("date"), request.args.get("month"))
        return jsonify(result)

    @app.put("/api/production/<day>")
    def production_write(day):
        """保存指定日期日报；day 为 YYYY-MM-DD；返回新版本，任一错误会回滚整批。"""
        return mutate(service.save_production, day)

    @app.post("/api/production/<month>/preview")
    def production_preview(month):
        """预览四线月日报；month 为目标月份、file 为上传；返回源值、旧值和冲突，不写库。"""
        request.max_content_length = MAX_FILE_BYTES + 1024 * 1024
        if request.content_length and request.content_length > request.max_content_length:
            raise ValidationError("Excel 文件不能超过 160 MiB")
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            raise ValidationError("请选择需要解析的 Excel 文件")
        try:
            result = parse_production_file(upload, month)
        finally:
            upload.close()
        db = get_db()
        now = app.config["NOW_PROVIDER"]()
        with service.consistent_read(db):
            result["version"] = service.settings(db)["version"]
            zone = service.settings(db)["timezone"]
            today = now.astimezone(ZoneInfo(zone)).date().isoformat()
            lines = {row["id"]: row["name"] for row in db.execute("SELECT id,name FROM production_line")}
            existing = {(row["work_date"], row["line_id"]): row for row in db.execute(
                "SELECT * FROM production_daily WHERE substr(work_date,1,7)=?", (month,))}
            for item in result["items"]:
                previous = existing.get((item["date"], item["line_id"]))
                item["name"] = lines[item["line_id"]]
                item["previous_actual"] = previous["actual_qty"] if previous is not None else None
                item["previous_defect"] = previous["defect_qty"] if previous is not None else None
                item["previous_hours"] = previous["planned_hours"] if previous is not None else None
                item["previous_abnormal"] = previous["abnormal_hours"] if previous is not None else None
                item["conflict"] = previous is not None and any(item.get(field) is not None and previous[field] is not None
                    and item[field] != previous[field] for field in ("actual_qty", "defect_qty", "planned_hours", "abnormal_hours", "ideal_cycle_hours"))
                item["future"] = item["date"] > today
            for item in result["standards"]:
                item["name"] = lines[item["line_id"]]
        return jsonify(result)

    @app.post("/api/production/<month>/import")
    def production_import_write(month):
        """保存勾选的月日报与文件标准；month 为月份；返回原子提交版本并记录当前录入人。"""
        return mutate(service.save_production_import, month)

    @app.post("/api/warehouse/<month>/preview")
    def warehouse_preview(month):
        """统一预览日报；month 为出货月份、file 为上传；返回两张表及同版本现有记录，不写库。"""
        request.max_content_length = MAX_FILE_BYTES + 1024 * 1024
        if request.content_length and request.content_length > request.max_content_length:
            raise ValidationError("Excel 文件不能超过 160 MiB")
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            raise ValidationError("请选择需要解析的 Excel 文件")
        try:
            result = parse_warehouse_file(upload, month)
        finally:
            upload.close()
        db = get_db()
        with service.consistent_read(db):
            snapshot = service.editor_snapshot(db, app.config["NOW_PROVIDER"](), month=month)
            result.update(version=snapshot["version"], existing={"inventory": snapshot["inventory"], "sales": snapshot["sales"]})
        return jsonify(result)

    @app.post("/api/warehouse/<month>/import")
    def warehouse_import_write(month):
        """一次保存库存与当月出货；month 为月份、JSON 为预览确认值；返回单次原子提交的新版本。"""
        return mutate(service.save_warehouse_import, month)

    @app.put("/api/inventory")
    def inventory_write():
        """保存原材料和半成品；JSON 为两类完整列表；返回原子保存后的版本。"""
        return mutate(service.save_inventory)

    @app.post("/api/inventory/preview")
    def inventory_preview():
        """预览 Excel 库存；multipart 的 file 为日报；返回解析值，不保存文件、不写库或递增版本。"""
        # 只为 Excel 上传放宽体积，其他 JSON 接口继续使用原来的 2 MiB 限额。
        request.max_content_length = MAX_FILE_BYTES + 1024 * 1024
        if request.content_length and request.content_length > request.max_content_length:
            raise ValidationError("Excel 文件不能超过 160 MiB")
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            raise ValidationError("请选择需要解析的 Excel 文件")
        try:
            return jsonify(parse_inventory_file(upload))
        finally:
            upload.close()

    @app.put("/api/sales/<month>")
    def sales_write(month):
        """保存当月全部件号出货；month 为 YYYY-MM；返回保存后的版本。"""
        return mutate(service.save_sales, month)

    @app.post("/api/sales/<month>/preview")
    def shipment_preview(month):
        """预览指定月份出货；month 为 YYYY-MM、file 为 Excel；返回逐条日期、件号、名称和数量，不写库。"""
        request.max_content_length = MAX_FILE_BYTES + 1024 * 1024
        if request.content_length and request.content_length > request.max_content_length:
            raise ValidationError("Excel 文件不能超过 160 MiB")
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            raise ValidationError("请选择需要解析的 Excel 文件")
        try:
            return jsonify(parse_shipment_file(upload, month))
        finally:
            upload.close()

    @app.put("/api/settings")
    def settings_write():
        """保存统一配置；JSON 为产线及业务设置；返回保存后的版本。"""
        return mutate(service.save_settings)

    @app.post("/api/downtime/start")
    def downtime_start():
        """立即开始停机；JSON 提供产线及原因；返回新记录ID和新版本。"""
        return mutate(service.start_downtime)

    @app.post("/api/downtime/<int:identifier>/finish")
    def downtime_finish(identifier):
        """恢复运行；identifier 为未结束停机ID；返回保存后的版本。"""
        payload = service.object_value(request.get_json())
        db = get_db()
        now = app.config["NOW_PROVIDER"]()
        with service.versioned_write(db, payload, now, g.employee, audit.write_scope("finish_downtime", ())):
            service.finish_downtime(db, identifier, now)
        return jsonify(version=service.settings(db)["version"], updated_at=now.isoformat())

    return app
