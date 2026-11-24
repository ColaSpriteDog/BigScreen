"""保存记录的首次录入及最后修改信息，不记录字段历史，也不接受客户端指定人员。"""

import json


# 表名、主键及参与变更判断的业务字段均由服务端固定，排除版本号、排序及派生状态。
TABLES = {
    "production_daily": (("work_date", "line_id"), ("actual_qty", "defect_qty", "planned_hours", "abnormal_hours", "ideal_cycle_hours")),
    "daily_overview": (("work_date",), ("planned_qty", "actual_qty", "oee_percent", "quality_percent", "average_output")),
    "line_downtime": (("id",), ("line_id", "start_at", "end_at", "kind", "note")),
    "raw_material_stock": (("id",), ("material", "warehouse", "specification", "position", "quantity", "unit", "weight_kg", "cuttable_qty", "show_on_screen")),
    "semi_finished_stock": (("id",), ("category", "material", "warehouse", "specification", "quantity", "unit", "show_on_screen")),
    "sales_month": (("month",), ("order_qty",)),
    "production_month_plan": (("month",), ("planned_qty",)),
    "shipment_month_item": (("id",), ("month", "part_no", "quantity", "name", "shipment_date")),
    "screen_settings": (("id",), ("timezone", "safety_start_date", "show_logo", "show_login_logo", "show_sidebar_logo")),
    "production_line": (("id",), ("name", "standard_qty", "show_in_trend", "theoretical_max_per_hour")),
    "process_node": (("id",), ("manual_status",)),
    "employee": (("id",), ("username", "name", "password", "enabled")),
}
OPTIONAL_TABLES = {"production_daily", "daily_overview", "sales_month", "production_month_plan"}


def record_key(values):
    """编码记录主键；values 为服务端取得的主键序列；返回无歧义的复合键字符串。"""
    return json.dumps(list(values), ensure_ascii=False, separators=(",", ":"))


def write_scope(writer, arguments):
    """确定写请求影响范围；writer 为保存函数名、arguments 为日期等参数；返回固定表名及查询条件。"""
    if writer == "save_production_import":
        return {"production_daily": ("substr(work_date,1,7)=?", arguments), "production_line": ("1", ())}
    if writer == "save_production":
        return {"production_daily": ("work_date=?", arguments), "daily_overview": ("work_date=?", arguments),
                "line_downtime": ("1", ()), "production_month_plan": ("1", ())}
    if writer == "save_inventory":
        return {table: ("1", ()) for table in ("raw_material_stock", "semi_finished_stock")}
    if writer == "save_warehouse_import":
        return {"raw_material_stock": ("1", ()), "semi_finished_stock": ("1", ()),
                "sales_month": ("month=?", arguments), "shipment_month_item": ("month=?", arguments)}
    if writer == "save_sales":
        return {table: ("month=?", arguments) for table in ("sales_month", "shipment_month_item")}
    if writer == "save_settings":
        return {table: ("1", ()) for table in ("screen_settings", "production_line", "process_node")}
    if writer == "save_employees":
        return {"employee": ("1", ())}
    return {"line_downtime": ("1", ())}


def capture(db, scope):
    """读取可比较的业务值；db 为事务连接、scope 为固定查询范围；返回按表和主键索引的字段元组。"""
    snapshot = {}
    for table, (condition, parameters) in scope.items():
        keys, fields = TABLES[table]
        snapshot[table] = {
            record_key(row[field] for field in keys): tuple(row[field] for field in fields)
            for row in db.execute(f"SELECT {','.join((*keys, *fields))} FROM {table} WHERE {condition}", parameters)
        }
    return snapshot


def meaningful(table, values):
    """判断是否已有真实录入；table 为业务表、values 为字段元组或空值；返回是否应视为已保存记录。"""
    if table == "production_daily" and values is not None:
        return any(value is not None for value in values[:2]) or values[2] not in (None, 24) or any(value is not None for value in values[3:])
    return values is not None and (table not in OPTIONAL_TABLES or any(value is not None for value in values))


def record_changes(db, before, after, actor, now):
    """记录实际变化；before/after 为业务快照，actor 为可信员工，now 为保存时间；无返回值，与业务一起提交。"""
    for table, current in after.items():
        previous = before[table]
        for key in previous.keys() - current.keys():
            db.execute("DELETE FROM record_attribution WHERE record_type=? AND record_key=?", (table, key))
        for key, values in current.items():
            old = previous.get(key)
            if old == values or (old is None and not meaningful(table, values)):
                continue
            # 历史记录首次被编辑时不猜测创建人；空白占位首次填值才属于新录入。
            created = not meaningful(table, old)
            db.execute(
                "INSERT INTO record_attribution(record_type,record_key,created_by_id,created_by_name,created_at,"
                "modified_by_id,modified_by_name,modified_at) VALUES(?,?,?,?,?,?,?,?) "
                "ON CONFLICT(record_type,record_key) DO UPDATE SET modified_by_id=excluded.modified_by_id,"
                "modified_by_name=excluded.modified_by_name,modified_at=excluded.modified_at",
                (table, key, actor["id"] if created else None, actor["name"] if created else None,
                 now.isoformat() if created else None, actor["id"], actor["name"], now.isoformat()),
            )


def attribution_lookup(db):
    """批量读取归属信息；db 为连接；返回按业务表及主键索引的只读展示字段，不联查当前员工姓名。"""
    return {(row["record_type"], row["record_key"]): {
        "state": "saved", **{field: row[field] for field in (
            "created_by_id", "created_by_name", "created_at", "modified_by_id", "modified_by_name", "modified_at")}}
        for row in db.execute("SELECT * FROM record_attribution")}


def attach(items, table, lookup, saved=True):
    """附加只读人员信息；items 为记录列表、table 为业务表、lookup 为归属索引、saved 为缺省是否已保存；无返回值。"""
    keys, fields = TABLES[table]
    for item in items:
        key = record_key(item.get(field) for field in keys)
        existing = saved and meaningful(table, tuple(item.get(field) for field in fields))
        item["_audit"] = lookup.get((table, key), {"state": "legacy" if existing else "unsaved"})
