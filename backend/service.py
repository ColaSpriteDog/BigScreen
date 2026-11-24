"""编辑与展示的业务边界：按业务表保存，统一计算展示结果。"""

from collections import defaultdict
from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import audit

from .domain import (STATUS_LABELS, STOP_KINDS, ValidationError, day_bounds, line_runtime,
                     number, parse_date, parse_datetime, parse_month, ratio, text)


class ConflictError(ValueError):
    """表示数据版本已变化，需要重新加载编辑内容。"""


def rows(db, query, parameters=()):
    """读取结构化记录；db 为连接，query/parameters 为 SQL 及绑定参数；返回字典列表。"""
    return [dict(row) for row in db.execute(query, parameters).fetchall()]


def settings(db):
    """读取统一业务设置；db 为数据库连接；返回设置字典。"""
    return dict(db.execute("SELECT * FROM screen_settings WHERE id=1").fetchone())


def production_unit(line_id):
    """读取固定产线计量单位；line_id 为产线标识；返回落料线的米或其他产线的件。"""
    return "米" if line_id == 1 else "件"


def object_value(value):
    """校验对象载荷；value 为任意输入；返回字典，非对象时抛出校验异常。"""
    if not isinstance(value, dict):
        raise ValidationError("提交内容必须为对象")
    return value


def array_value(value, label, limit=2000):
    """校验记录列表；label 为字段名、limit 为行数上限；返回经过类型检查的字典列表。"""
    if not isinstance(value, list) or len(value) > limit:
        raise ValidationError(f"{label}格式不正确或超过 {limit} 行")
    return [object_value(item) for item in value]


@contextmanager
def versioned_write(db, payload, now, actor=None, scope=None):
    """执行原子写入；payload 含版本、now 为时间、actor/scope 为可信操作人及变更范围；产生连接，成功后递增版本。"""
    expected = number(payload.get("version"), "数据版本", integer=True)
    db.execute("BEGIN IMMEDIATE")
    try:
        if settings(db)["version"] != expected:
            raise ConflictError("数据已被其他操作更新，请重新加载后保存")
        before = audit.capture(db, scope) if actor is not None and scope else None
        yield db
        if before is not None:
            audit.record_changes(db, before, audit.capture(db, scope), actor, now)
        db.execute("UPDATE screen_settings SET version=version+1,updated_at=? WHERE id=1", (now.isoformat(),))
        db.commit()
    except Exception:
        db.rollback()
        raise


@contextmanager
def consistent_read(db):
    """开启一致性读事务；db 为连接；产生同一 SQLite 快照，结束后释放事务。"""
    db.execute("BEGIN")
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise


def downtime_rows(db, day, zone):
    """读取与业务日相交的停机；day、zone 为日期和时区；返回含跨天、未结束记录的列表。"""
    start, finish = day_bounds(day, zone)
    return rows(db, "SELECT * FROM line_downtime WHERE start_at<? AND (end_at IS NULL OR end_at>?) ORDER BY start_at,id",
                (finish.isoformat(), start.isoformat()))


def editor_snapshot(db, now, day=None, month=None, include_audit=True, production_month=None):
    """读取编辑表单；now 为当前时间，day/month/production_month 为日报、销售和月计划周期；返回含可选人员信息的表单。"""
    config = settings(db)
    zone = config["timezone"]
    today = now.astimezone(ZoneInfo(zone)).date().isoformat()
    day, month = day or today, month or today[:7]
    parse_date(day)
    parse_month(month)
    # 生产月份独立于日报日期及销售月份，默认读取业务当月。
    production_month = parse_month(production_month or today[:7])
    plan = rows(db, "SELECT * FROM production_month_plan WHERE month=?", (production_month,))
    output = {row["line_id"]: row for row in rows(db, "SELECT * FROM production_daily WHERE work_date=?", (day,))}
    lines = rows(db, "SELECT * FROM production_line ORDER BY sort_order")
    current_stops = downtime_rows(db, today, zone)
    selected_stops = current_stops if day == today else downtime_rows(db, day, zone)
    for line in lines:
        line["unit"] = production_unit(line["id"])
        line.update(actual_qty=output.get(line["id"], {}).get("actual_qty"),
                    defect_qty=output.get(line["id"], {}).get("defect_qty"),
                    planned_hours=output.get(line["id"], {}).get("planned_hours", 24),
                    abnormal_hours=output.get(line["id"], {}).get("abnormal_hours"),
                    ideal_cycle_hours=1 / line["theoretical_max_per_hour"] if line["theoretical_max_per_hour"] else None)
        runtime = line_runtime(now, zone, [item for item in current_stops if item["line_id"] == line["id"]], line["planned_hours"])
        if day != today:
            selected_runtime = line_runtime(now, zone, [item for item in selected_stops if item["line_id"] == line["id"]], line["planned_hours"], day)
            runtime.update(runtime_rate=selected_runtime["runtime_rate"], downtime_minutes=selected_runtime["downtime_minutes"])
        line.update(runtime)
    stops = selected_stops
    for item in stops:
        item["start_at"] = parse_datetime(item["start_at"]).astimezone(ZoneInfo(zone)).strftime("%Y-%m-%dT%H:%M:%S")
        if item["end_at"]:
            item["end_at"] = parse_datetime(item["end_at"]).astimezone(ZoneInfo(zone)).strftime("%Y-%m-%dT%H:%M:%S")
    overview = rows(db, "SELECT * FROM daily_overview WHERE work_date=?", (day,))
    sale = rows(db, "SELECT * FROM sales_month WHERE month=?", (month,))
    result = {
        "version": config["version"], "updated_at": config["updated_at"], "server_time": now.isoformat(),
        "business_date": today, "date": day, "month": month, "settings": config,
        "production": {"lines": lines, "downtimes": stops,
            "monthly_plan": plan[0] if plan else {"month": production_month, "planned_qty": None},
            "overview": overview[0] if overview else {
            "planned_qty": None, "actual_qty": None, "oee_percent": None, "quality_percent": None, "average_output": None}},
        "inventory": {"raw": rows(db, "SELECT * FROM raw_material_stock ORDER BY sort_order,id"),
                      "semi": rows(db, "SELECT * FROM semi_finished_stock ORDER BY sort_order,id")},
        "sales": {"order_qty": sale[0]["order_qty"] if sale else None,
                  "items": rows(db, "SELECT * FROM shipment_month_item WHERE month=? AND (shipment_date IS NULL OR substr(shipment_date,1,7)=?) ORDER BY sort_order,id", (month, month))},
        "processes": rows(db, "SELECT * FROM process_node ORDER BY sort_order"),
    }
    # 对外统一返回布尔值，后台保留隐藏记录以便随时恢复显示。
    for item in result["inventory"]["raw"]:
        item["show_on_screen"] = bool(item["show_on_screen"])
    # 历史“个”与现在的“件”为同一计数口径，读取时统一单位，不在读取请求中修改数据库。
    for item in result["inventory"]["semi"]:
        item["show_on_screen"] = bool(item["show_on_screen"])
        if item["unit"] == "个":
            item["unit"] = "件"
    if include_audit:
        lookup = audit.attribution_lookup(db)
        audit.attach([config], "screen_settings", lookup)
        audit.attach([result["production"]["monthly_plan"]], "production_month_plan", lookup, bool(plan))
        for line in lines:
            audit.attach([line], "production_line", lookup)
            line["_configuration_audit"] = line.pop("_audit")
            source = {"work_date": day, "line_id": line["id"], "actual_qty": line["actual_qty"], "defect_qty": line["defect_qty"],
                      "planned_hours": line["planned_hours"], "abnormal_hours": line["abnormal_hours"],
                      "ideal_cycle_hours": output.get(line["id"], {}).get("ideal_cycle_hours")}
            audit.attach([source], "production_daily", lookup, line["id"] in output)
            line["_audit"] = source["_audit"]
        audit.attach([result["production"]["overview"]], "daily_overview", lookup, bool(overview))
        # 未保存的默认全局指标没有主键，保存过的记录使用数据库中的业务日期。
        audit.attach(stops, "line_downtime", lookup)
        audit.attach(result["inventory"]["raw"], "raw_material_stock", lookup)
        audit.attach(result["inventory"]["semi"], "semi_finished_stock", lookup)
        sale_info = sale[0] if sale else {"month": month, "order_qty": None}
        audit.attach([sale_info], "sales_month", lookup, bool(sale))
        result["sales"]["_audit"] = sale_info["_audit"]
        audit.attach(result["sales"]["items"], "shipment_month_item", lookup)
        audit.attach(result["processes"], "process_node", lookup)
    result["production"]["overview"].update(production_totals(db, day, day))
    return result


def unit_totals(items):
    """按单位汇总库存；items 为含 quantity/unit 的记录；返回不同单位分别统计的列表。"""
    totals = defaultdict(Decimal)
    for item in items:
        totals[item["unit"]] += Decimal(str(item["quantity"]))
    return [{"unit": unit, "quantity": float(quantity)} for unit, quantity in totals.items()]


def production_totals(db, first_day, last_day):
    """累计拼焊日报；db 为连接、first_day/last_day 为日期边界；返回数量、比率、每小时产出和 OEE。"""
    # 只计 A/B 拼焊线，按已录入产量的产线日累计标准；零产量计入，空白不生成计划。
    totals = rows(db,
        "SELECT SUM(d.actual_qty) AS actual_qty,"
        "COUNT(*) AS reported_count,"
        "SUM(CASE WHEN d.planned_hours>0 AND d.abnormal_hours IS NOT NULL "
        "AND d.abnormal_hours<=d.planned_hours AND l.theoretical_max_per_hour>0 "
        "AND (d.actual_qty=0 OR d.abnormal_hours<d.planned_hours) "
        "AND (d.defect_qty IS NOT NULL OR d.actual_qty=0) THEN 1 ELSE 0 END) AS oee_count,"
        "SUM(d.planned_hours) AS oee_planned_hours,SUM(d.planned_hours-d.abnormal_hours) AS oee_run_hours,"
        "SUM((d.actual_qty-COALESCE(d.defect_qty,0))/l.theoretical_max_per_hour) AS good_hours,"
        "ROUND(SUM(d.actual_qty)/NULLIF(SUM(d.planned_hours),0),2) AS average_output,"
        "CASE WHEN COUNT(*)=COUNT(l.standard_qty) THEN SUM(l.standard_qty) END AS planned_qty,"
        "CASE WHEN SUM(CASE WHEN d.actual_qty>0 AND d.defect_qty IS NULL THEN 1 ELSE 0 END)=0 "
        "THEN SUM(d.actual_qty-COALESCE(d.defect_qty,0)) END AS good_qty "
        "FROM production_daily d JOIN production_line l ON l.id=d.line_id "
        "WHERE d.line_id IN (3,4) AND d.work_date>=? AND d.work_date<=? AND d.actual_qty IS NOT NULL",
        (first_day, last_day))[0]
    # 任一已录入产线缺少标准时保留空计划，避免用不完整分母计算达成率。
    totals["completion_rate"] = ratio(totals["actual_qty"], totals["planned_qty"])
    # 有产量但缺少报废量时不推断合格品；零产量没有待判定产品，整月零产量的比率仍为空。
    totals["quality_percent"] = ratio(totals.pop("good_qty"), totals["actual_qty"])
    # 按当前配置的理论小时产能折算合格品理想工时；配置修改后重算历史，不使用日产目标推算节拍。
    complete = totals.pop("reported_count") == totals.pop("oee_count")
    planned, runtime = totals.pop("oee_planned_hours"), totals.pop("oee_run_hours")
    good = totals.pop("good_hours")
    valid_runtime = runtime is not None and (runtime > 0 or (runtime == 0 and not totals["actual_qty"]))
    totals["oee_percent"] = ratio(good, planned) if complete and valid_runtime else None
    return totals


def monthly_overview(db, day):
    """汇总当月全局指标；db 为连接、day 为业务今天；返回优先使用月计划设置的指标。"""
    totals = production_totals(db, day[:7] + "-01", day)
    # 显式零也是有效设置，只有未设置或清空时才回退日报累计计划。
    plan = db.execute("SELECT planned_qty FROM production_month_plan WHERE month=?", (day[:7],)).fetchone()
    if plan is not None and plan["planned_qty"] is not None:
        totals["planned_qty"] = plan["planned_qty"]
        totals["completion_rate"] = ratio(totals["actual_qty"], totals["planned_qty"])
    return totals


def dashboard_snapshot(db, now):
    """生成大屏数据；db 为一致性读连接、now 为当前时间；返回全部计算后的展示模块。"""
    snapshot = editor_snapshot(db, now, include_audit=False)
    day, zone = snapshot["date"], snapshot["settings"]["timezone"]
    lines = snapshot["production"]["lines"]
    # 日报次日更新：卡片日指标读取业务时区昨日，当前运行状态仍使用今天的时长和停机记录。
    production_date = (parse_date(day) - timedelta(days=1)).isoformat()
    yesterday = {row["line_id"]: row for row in rows(db,
        "SELECT line_id,actual_qty,defect_qty FROM production_daily WHERE work_date=?", (production_date,))}
    monthly = {row["line_id"]: row["quantity"] for row in rows(db,
        "SELECT line_id,SUM(actual_qty) AS quantity FROM production_daily WHERE work_date>=? AND work_date<=? GROUP BY line_id",
        (day[:7] + "-01", day))}
    for line in lines:
        line["monthly_qty"] = monthly.get(line["id"])
        line["defect_rate"] = ratio(line["defect_qty"], line["actual_qty"])
        previous = yesterday.get(line["id"], {})
        line["yesterday_qty"] = previous.get("actual_qty")
        line["yesterday_defect_qty"] = previous.get("defect_qty")
        line["yesterday_defect_rate"] = ratio(line["yesterday_defect_qty"], line["yesterday_qty"])
    # 趋势与本月产量统一按业务月初至今天取数，跨月后自动重新累计。
    today = parse_date(day)
    first = today.replace(day=1)
    dates = [(first + timedelta(days=index)).isoformat() for index in range(today.day)]
    outputs = {(item["work_date"], item["line_id"]): item["actual_qty"] for item in rows(db,
        "SELECT * FROM production_daily WHERE work_date>=? AND work_date<=?", (dates[0], day))}
    series = [{"line_id": line["id"], "name": line["name"], "unit": line["unit"],
               "data": [outputs.get((date, line["id"])) or 0 for date in dates],
               # 保留空白与明确录入 0 的区别，供标准产量线判断未启用产线。
               "has_data": any(outputs.get((date, line["id"])) is not None for date in dates)}
              for line in lines if line["show_in_trend"]]
    # 趋势展示将未录入产量按零累计，原始日报仍保留空值；录入天数按任一所选产线有数量计算。
    groups = []
    for unit in dict.fromkeys(item["unit"] for item in series):
        selected = [item for item in series if item["unit"] == unit]
        values = [float(sum(Decimal(str(item["data"][index])) for item in selected)) for index in range(len(dates))]
        groups.append({"unit": unit, "totals": values, "period_total": float(sum(Decimal(str(value)) for value in values))})
    # 不同单位没有可相加的总量；单单位保留标量字段，多单位只提供分组合计。
    totals = groups[0]["totals"] if len(groups) == 1 else None
    reported_days = sum(any(outputs.get((date, item["line_id"])) is not None for item in series) for date in dates)
    inventory = snapshot["inventory"]
    # 明细、数量汇总、重量及零库存提示统一使用大屏可见记录。
    inventory["raw"] = [item for item in inventory["raw"] if item["show_on_screen"]]
    # 半成品明细与合计使用相同的可见记录，编辑端仍保留全部库存。
    inventory["semi"] = [item for item in inventory["semi"] if item["show_on_screen"]]
    for item in inventory["raw"]:
        item["status"] = "zero" if item["quantity"] == 0 else "available"
    inventory.update(raw_totals=unit_totals(inventory["raw"]), semi_totals=unit_totals(inventory["semi"]),
                     zero_count=sum(item["quantity"] == 0 for item in inventory["raw"]))
    sales = snapshot["sales"]
    sales["shipped_qty"] = sum(item["quantity"] for item in sales["items"]) if sales["items"] else None
    sales["achievement_rate"] = ratio(sales["shipped_qty"], sales["order_qty"])
    # 大屏按件号汇总排名，编辑与导入仍保留逐条明细；并列时按件号排序以保持轮询稳定。
    shipments_by_part = {}
    shipment_names = defaultdict(dict)
    for item in sales["items"]:
        key = item["part_no"].casefold()
        if key not in shipments_by_part:
            shipments_by_part[key] = {"part_no": item["part_no"], "quantity": 0}
        shipments_by_part[key]["quantity"] += item["quantity"]
        # 同件号保留各个非空名称并去重，避免数量汇总时丢失名称。
        if item["name"]:
            shipment_names[key][item["name"]] = None
    for key, item in shipments_by_part.items():
        item["name"] = " / ".join(shipment_names[key])
    sales["ranking"] = sorted(shipments_by_part.values(), key=lambda item: (-item["quantity"], item["part_no"].casefold()))[:8]
    overview = monthly_overview(db, day)
    processes = snapshot["processes"]
    for process in processes:
        linked = [line for line in lines if line["process_id"] == process["id"]]
        abnormal = [line for line in linked if line["status"] != "running"]
        if abnormal:
            worst = max(abnormal, key=lambda line: {"stopped": 1, "maintenance": 2, "fault": 3}[line["status"]])
            process["status"] = worst["status"]
            # 默认名称可简写为 A/B 线；改名后用状态标签配合完整告警名称，避免沿用旧称呼。
            default_alias = {3: ("激光拼焊线A", "A线"), 4: ("激光拼焊线B", "B线")}.get(worst["id"])
            suffix = default_alias[1] if default_alias and worst["name"] == default_alias[0] else ""
            process["status_label"] = suffix + worst["status_label"]
            process["alert_label"] = worst["name"] + "：" + worst["status_label"]
        else:
            process["status"] = "running" if linked else process["manual_status"]
            process["status_label"] = STATUS_LABELS[process["status"]]
            process["alert_label"] = process["status_label"]
        process["alert"] = process["status"] not in {"running", "normal"}
    safe_start = snapshot["settings"]["safety_start_date"]
    return {"version": snapshot["version"], "updated_at": snapshot["updated_at"], "server_time": now.isoformat(),
            "business_date": day, "production_date": production_date, "timezone": zone, "lines": lines, "inventory": inventory,
            "trend": {"dates": dates, "series": series, "totals": totals, "groups": groups,
                      "period_total": groups[0]["period_total"] if len(groups) == 1 else None, "reported_days": reported_days},
            "sales": sales, "overview": overview, "processes": processes,
            "safety_days": (parse_date(day) - parse_date(safe_start)).days if safe_start else None}


def validate_line_ids(db, items):
    """验证固定产线集合；items 为带 id 的记录；返回合法 ID 集合，缺行或重复时抛出异常。"""
    known = {row[0] for row in db.execute("SELECT id FROM production_line")}
    identifiers = [number(item.get("id"), "产线ID", integer=True) for item in items]
    if set(identifiers) != known or len(identifiers) != len(known):
        raise ValidationError("必须提交完整且不重复的产线记录")
    return known


def save_production(db, payload, day, now):
    """保存日报、月计划与停机草稿；day 为业务日期、now 为保存时间；无返回值，外层事务统一提交。"""
    parse_date(day)
    if parse_date(day) > now.astimezone(ZoneInfo(settings(db)["timezone"])).date():
        raise ValidationError("生产日报不能录入未来日期")
    # 缺少字段的旧日报请求保留月计划；月份随草稿提交，避免跨月写错目标。
    if "monthly_plan" in payload:
        plan = object_value(payload["monthly_plan"])
        plan_month = parse_month(plan.get("month"))
        planned_qty = number(plan.get("planned_qty"), "月计划产量", integer=True, optional=True)
        db.execute("INSERT INTO production_month_plan(month,planned_qty) VALUES(?,?) "
                   "ON CONFLICT(month) DO UPDATE SET planned_qty=excluded.planned_qty", (plan_month, planned_qty))
    lines = array_value(payload.get("lines"), "产线日报", 20)
    known = validate_line_ids(db, lines)
    existing_hours = {row["line_id"]: row["planned_hours"] for row in db.execute(
        "SELECT line_id,planned_hours FROM production_daily WHERE work_date=?", (day,))}
    previous = {row["line_id"]: row for row in db.execute("SELECT * FROM production_daily WHERE work_date=?", (day,))}
    for item in lines:
        planned_hours = number(item.get("planned_hours", existing_hours.get(item["id"], 24)), "计划生产时长", maximum=24)
        actual = number(item.get("actual_qty"), "实际产量", integer=item["id"] != 1, optional=True)
        defects = number(item.get("defect_qty"), "不良数量", integer=item["id"] != 1, optional=True)
        if defects is not None and (actual is None or defects > actual):
            raise ValidationError("不良数量不能大于实际产量，且必须先填写实际产量")
        abnormal = number(item.get("abnormal_hours", previous[item["id"]]["abnormal_hours"] if item["id"] in previous else None),
                          "异常停机工时", optional=True, maximum=planned_hours)
        # 旧版导入节拍只保留原始记录；当前 OEE 从产线理论小时产能计算，不接受日报改写基准。
        cycle = previous[item["id"]]["ideal_cycle_hours"] if item["id"] in previous else None
        db.execute("INSERT INTO production_daily(line_id,work_date,actual_qty,defect_qty,planned_hours,abnormal_hours,ideal_cycle_hours) "
                   "VALUES(?,?,?,?,?,?,?) "
                   "ON CONFLICT(line_id,work_date) DO UPDATE SET actual_qty=excluded.actual_qty,defect_qty=excluded.defect_qty,"
                   "planned_hours=excluded.planned_hours,abnormal_hours=excluded.abnormal_hours",
                   (item["id"], day, actual, defects, planned_hours, abnormal, cycle))
    zone = settings(db)["timezone"]
    existing = {item["id"] for item in downtime_rows(db, day, zone)}
    seen = set()
    start_day, end_day = day_bounds(day, zone)
    for item in array_value(payload.get("downtimes"), "停机记录"):
        identifier = item.get("id")
        if identifier is not None:
            identifier = number(identifier, "停机记录ID", integer=True)
            if identifier not in existing or identifier in seen:
                raise ValidationError("停机记录不存在或重复，请重新加载")
            seen.add(identifier)
        line_id = number(item.get("line_id"), "停机产线", integer=True)
        if line_id not in known:
            raise ValidationError("停机记录产线不存在")
        start = parse_datetime(item.get("start_at"), zone)
        finish = parse_datetime(item["end_at"], zone) if item.get("end_at") else None
        if finish is not None and finish <= start:
            raise ValidationError("停机结束时间必须晚于开始时间")
        if identifier is None and (start >= end_day or (finish is not None and finish <= start_day)):
            raise ValidationError("新增停机记录必须与选定日期相交")
        kind = item.get("kind", "stopped")
        if not isinstance(kind, str) or kind not in STOP_KINDS:
            raise ValidationError("停机类型不正确")
        note = text(item.get("note", ""), "停机备注", maximum=200, optional=True)
        params = (line_id, start.isoformat(), finish.isoformat() if finish else None, kind, note, now.isoformat())
        if identifier is None:
            db.execute("INSERT INTO line_downtime(line_id,start_at,end_at,kind,note,updated_at) VALUES(?,?,?,?,?,?)", params)
        else:
            db.execute("UPDATE line_downtime SET line_id=?,start_at=?,end_at=?,kind=?,note=?,updated_at=? WHERE id=?", (*params, identifier))
    for identifier in existing - seen:
        db.execute("DELETE FROM line_downtime WHERE id=?", (identifier,))


def replace_stock(db, table, fields, items):
    """替换指定库存表；table/fields 由服务端固定，items 为已校验记录；无返回值，保留已有记录ID。"""
    existing = {row[0] for row in db.execute(f"SELECT id FROM {table}")}
    supplied = [item.get("id") for item in items if item.get("id") is not None]
    if len(set(supplied)) != len(supplied) or not set(supplied).issubset(existing):
        raise ValidationError("库存记录已变化，请重新加载")
    db.execute(f"DELETE FROM {table}")
    columns = ["id", *fields, "sort_order"]
    query = f"INSERT INTO {table}({','.join(columns)}) VALUES({','.join('?' for _ in columns)})"
    for index, item in enumerate(items):
        db.execute(query, (item.get("id"), *(item[field] for field in fields), index))


def save_inventory(db, payload):
    """原子保存两类库存；payload 为原材料与半成品列表；无返回值，状态和汇总由读取时计算。"""
    raw = []
    existing_visibility = {row["id"]: bool(row["show_on_screen"])
                           for row in db.execute("SELECT id,show_on_screen FROM raw_material_stock")}
    for item in array_value(payload.get("raw"), "原材料"):
        cleaned = {"id": number(item.get("id"), "库存ID", integer=True, optional=True)}
        for field, label in [("material", "物料"), ("warehouse", "仓库"), ("specification", "规格"),
                             ("position", "部位"), ("unit", "单位")]:
            cleaned[field] = text(item.get(field, ""), label, optional=field == "position", maximum=80 if field == "specification" else 30)
        cleaned["quantity"] = number(item.get("quantity"), "原材料库存")
        cleaned["weight_kg"] = number(item.get("weight_kg"), "重量", optional=True)
        cleaned["cuttable_qty"] = number(item.get("cuttable_qty"), "可切件数", integer=True, optional=True)
        # 新记录默认显示，旧客户端未传开关时保留原配置，避免导入或保存意外恢复隐藏项。
        visible = item.get("show_on_screen", existing_visibility.get(cleaned["id"], True))
        if not isinstance(visible, bool):
            raise ValidationError("大屏显示必须为布尔值")
        cleaned["show_on_screen"] = int(visible)
        raw.append(cleaned)
    semi = []
    semi_visibility = {row["id"]: bool(row["show_on_screen"])
                       for row in db.execute("SELECT id,show_on_screen FROM semi_finished_stock")}
    for item in array_value(payload.get("semi"), "半成品"):
        cleaned = {"id": number(item.get("id"), "库存ID", integer=True, optional=True)}
        for field, label in [("category", "类别"), ("material", "物料"), ("warehouse", "仓库"),
                             ("specification", "规格"), ("unit", "单位")]:
            cleaned[field] = text(item.get(field), label, maximum=30)
        cleaned["quantity"] = number(item.get("quantity"), "半成品库存", integer=True)
        if cleaned["unit"] == "个":
            cleaned["unit"] = "件"
        # 兼容未传开关的旧请求，重新保存时保留已有半成品的显示配置。
        visible = item.get("show_on_screen", semi_visibility.get(cleaned["id"], True))
        if not isinstance(visible, bool):
            raise ValidationError("大屏显示必须为布尔值")
        cleaned["show_on_screen"] = int(visible)
        semi.append(cleaned)
    replace_stock(db, "raw_material_stock", ["material", "warehouse", "specification", "position", "quantity", "unit", "weight_kg", "cuttable_qty", "show_on_screen"], raw)
    replace_stock(db, "semi_finished_stock", ["category", "material", "warehouse", "specification", "quantity", "unit", "show_on_screen"], semi)


def save_warehouse_import(db, payload, month):
    """统一保存仓库日报；payload 为库存和出货明细、month 为月份；无返回值，复用外层事务且保留原订单量。"""
    parse_month(month)
    inventory = object_value(payload.get("inventory"))
    shipments = object_value(payload.get("shipments"))
    sale = db.execute("SELECT order_qty FROM sales_month WHERE month=?", (month,)).fetchone()
    save_inventory(db, inventory)
    save_sales(db, {"order_qty": sale["order_qty"] if sale else None, "items": shipments.get("items")}, month)


def save_sales(db, payload, month):
    """保存月订单量及逐条出货明细；month 为月份，payload 为录入值；无返回值，允许重复件号。"""
    parse_month(month)
    order_qty = number(payload.get("order_qty"), "本月订单量", integer=True, optional=True)
    db.execute("INSERT INTO sales_month VALUES(?,?) ON CONFLICT(month) DO UPDATE SET order_qty=excluded.order_qty", (month, order_qty))
    existing = {row["id"]: dict(row) for row in db.execute("SELECT * FROM shipment_month_item WHERE month=?", (month,))}
    seen, prepared = set(), []
    for sort_order, item in enumerate(array_value(payload.get("items"), "出货明细")):
        part = text(item.get("part_no"), "件号", maximum=40)
        quantity = number(item.get("quantity"), "出货数量", integer=True)
        identifier = number(item.get("id"), "出货记录ID", integer=True, optional=True)
        if identifier is None:
            # 没有 ID 的旧请求仅在唯一匹配时沿用身份，重复件号不再被当作同一条记录。
            matches = [row["id"] for row in existing.values() if row["id"] not in seen
                       and row["part_no"].lower() == part.lower()
                       and ("shipment_date" not in item or row["shipment_date"] == item["shipment_date"])
                       and ("name" not in item or row["name"] == item["name"])]
            if len(matches) == 1:
                identifier = matches[0]
        if identifier is not None:
            if identifier not in existing or identifier in seen:
                raise ValidationError("出货记录不存在或重复，请重新加载")
            seen.add(identifier)
        # 旧客户端未提供名称时沿用原值，导入及编辑时按提交值保存。
        name = text(item.get("name", existing[identifier]["name"] if identifier is not None else ""), "名称", maximum=80, optional=True)
        day = item.get("shipment_date", existing[identifier]["shipment_date"] if identifier is not None else None) or None
        if day is not None:
            parse_date(day)
            if day[:7] != month:
                raise ValidationError("出库日期必须属于当前出货月份")
        prepared.append((identifier, month, part, quantity, name or "", day, sort_order))
    db.execute("DELETE FROM shipment_month_item WHERE month=?", (month,))
    db.executemany("INSERT INTO shipment_month_item(id,month,part_no,quantity,name,shipment_date,sort_order) VALUES(?,?,?,?,?,?,?)", prepared)


def save_production_import(db, payload, month, now):
    """原子导入月日报及标准；payload 为勾选记录与标准、month 为月份、now 为时间；返回写入条数，空字段保留旧值。"""
    parse_month(month)
    items = array_value(payload.get("items"), "导入日报", 124)
    standards = array_value(payload.get("standards"), "导入标准", 4)
    validate_line_ids(db, [{"id": item.get("line_id")} for item in standards])
    overwrite = payload.get("overwrite", False)
    if type(overwrite) is not bool:
        raise ValidationError("覆盖已有数据必须为勾选值")
    if not items:
        raise ValidationError("请至少选择一条日报")
    today = now.astimezone(ZoneInfo(settings(db)["timezone"])).date().isoformat()
    seen = set()
    for item in items:
        identifier = number(item.get("line_id"), "产线ID", integer=True)
        if identifier not in {1, 2, 3, 4}:
            raise ValidationError("产线不存在")
        day = item.get("date")
        parse_date(day)
        if day[:7] != month or day > today:
            raise ValidationError("日报日期必须属于导入月份，且不能晚于今天")
        if (day, identifier) in seen:
            raise ValidationError("导入日报的日期与产线重复")
        seen.add((day, identifier))
        actual = number(item.get("actual_qty"), "实际产量", integer=identifier != 1)
        defects = number(item.get("defect_qty"), "不良数量", integer=identifier != 1, optional=True)
        previous = db.execute("SELECT * FROM production_daily WHERE work_date=? AND line_id=?", (day, identifier)).fetchone()
        hours = number(item.get("planned_hours", previous["planned_hours"] if previous else 24), "计划生产工时", maximum=24)
        abnormal = number(item.get("abnormal_hours", previous["abnormal_hours"] if previous else None),
                          "异常停机工时", optional=True, maximum=hours)
        # 历史导入节拍仅留档，OEE 统一使用产线配置的理论小时产能，忽略客户端节拍。
        cycle = previous["ideal_cycle_hours"] if previous else None
        # 未填写报废时保留已有值；只有用户勾选覆盖才接受改变已录入数量。
        if previous is not None:
            if not overwrite and any(new is not None and previous[field] is not None and new != previous[field]
                                     for field, new in (("actual_qty", actual), ("defect_qty", defects),
                                                        ("planned_hours", hours), ("abnormal_hours", abnormal))):
                raise ValidationError(f"{day} 产线 {identifier:02d} 已有不同数据，请核对预览并勾选覆盖")
            if defects is None:
                defects = previous["defect_qty"]
        if defects is not None and defects > actual:
            raise ValidationError(f"{day} 产线 {identifier:02d} 不良数量大于实际产量")
        db.execute("INSERT INTO production_daily(line_id,work_date,actual_qty,defect_qty,planned_hours,abnormal_hours,ideal_cycle_hours) "
                   "VALUES(?,?,?,?,?,?,?) ON CONFLICT(line_id,work_date) DO UPDATE SET "
                   "actual_qty=excluded.actual_qty,defect_qty=excluded.defect_qty,planned_hours=excluded.planned_hours,"
                   "abnormal_hours=excluded.abnormal_hours,ideal_cycle_hours=excluded.ideal_cycle_hours",
                   (identifier, day, actual, defects, hours, abnormal, cycle))
    for item in standards:
        identifier = item["line_id"]
        value = number(item.get("standard_qty"), "标准日产量", integer=identifier != 1)
        db.execute("UPDATE production_line SET standard_qty=? WHERE id=?", (value, identifier))
    return len(items)


def save_settings(db, payload, now):
    """保存配置；payload 为时区、安全日期、产线和独立节点配置；无返回值。"""
    zone = text(payload.get("timezone"), "业务时区")
    try:
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValidationError("业务时区不存在") from exc
    safety_date = payload.get("safety_start_date") or None
    if safety_date and parse_date(safety_date) > now.astimezone(ZoneInfo(zone)).date():
        raise ValidationError("安全起算日期不能晚于今天")
    lines = array_value(payload.get("lines"), "产线配置", 20)
    validate_line_ids(db, lines)
    current_visibility = {row["id"]: row["show_in_trend"] for row in rows(db, "SELECT id,show_in_trend FROM production_line")}
    visibility = {}
    for line in lines:
        value = line.get("show_in_trend", current_visibility[line["id"]])
        if type(value) not in (bool, int) or value not in (0, 1):
            raise ValidationError("趋势图选择必须为勾选值")
        visibility[line["id"]] = int(value)
    if not any(visibility.values()):
        raise ValidationError("趋势图至少选择一条产线")
    capacities = {row["id"]: row["theoretical_max_per_hour"] for row in
                  rows(db, "SELECT id,theoretical_max_per_hour FROM production_line")}
    for line in lines:
        name = text(line.get("name"), "产线名称", maximum=20)
        standard = number(line.get("standard_qty"), "标准日产量", integer=line["id"] != 1, optional=True)
        # 小时产能支持小数；空白表示未配置，旧客户端未提交时保留原值。
        capacity = number(line.get("theoretical_max_per_hour", capacities[line["id"]]), "设备理论最大产量", optional=True)
        if capacity == 0:
            raise ValidationError("设备理论最大产量必须大于零，未配置请留空")
        db.execute("UPDATE production_line SET name=?,standard_qty=?,show_in_trend=?,theoretical_max_per_hour=? WHERE id=?",
                   (name, standard, visibility[line["id"]], capacity, line["id"]))
    nodes = array_value(payload.get("processes"), "独立工序配置", 5)
    for node in nodes:
        node["id"] = number(node.get("id"), "工序ID", integer=True)
    if {node.get("id") for node in nodes} != {1, 5} or len(nodes) != 2:
        raise ValidationError("需提交钢卷和出货两个独立工序状态")
    for node in nodes:
        state = node.get("manual_status")
        if not isinstance(state, str) or state not in {"normal", *STOP_KINDS}:
            raise ValidationError("工序状态不正确")
        db.execute("UPDATE process_node SET manual_status=? WHERE id=?", (state, node["id"]))
    db.execute("UPDATE screen_settings SET timezone=?,safety_start_date=? WHERE id=1", (zone, safety_date))


def start_downtime(db, payload, now):
    """立即开始停机；payload 含 line_id/kind；返回新记录ID，已处于停机区间时拒绝重复开始。"""
    identifier = number(payload.get("line_id"), "产线ID", integer=True)
    if not db.execute("SELECT 1 FROM production_line WHERE id=?", (identifier,)).fetchone():
        raise ValidationError("产线不存在")
    zone = settings(db)["timezone"]
    day = now.astimezone(ZoneInfo(zone)).date().isoformat()
    stops = [row for row in downtime_rows(db, day, zone) if row["line_id"] == identifier]
    if line_runtime(now, zone, stops)["status"] != "running":
        raise ConflictError("该产线已处于停机时段，请直接修改原记录")
    kind = payload.get("kind", "stopped")
    if not isinstance(kind, str) or kind not in STOP_KINDS:
        raise ValidationError("停机类型不正确")
    cursor = db.execute("INSERT INTO line_downtime(line_id,start_at,kind,updated_at) VALUES(?,?,?,?)",
                        (identifier, now.isoformat(), kind, now.isoformat()))
    return cursor.lastrowid


def finish_downtime(db, identifier, now):
    """结束正在进行的停机；identifier 为记录ID、now 为恢复时间；无返回值。"""
    item = db.execute("SELECT * FROM line_downtime WHERE id=?", (identifier,)).fetchone()
    if item is None or item["end_at"] is not None:
        raise ConflictError("停机记录已结束或不存在，请重新加载")
    if parse_datetime(item["start_at"]) >= now:
        raise ValidationError("恢复时间必须晚于停机开始时间")
    db.execute("UPDATE line_downtime SET end_at=?,updated_at=? WHERE id=?", (now.isoformat(), now.isoformat(), identifier))
