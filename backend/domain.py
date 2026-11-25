"""时间区间与数据校验规则，不依赖网页或数据库。"""

from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
import math
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc
STATUS_LABELS = {"running": "运行中", "normal": "正常", "stopped": "停机", "maintenance": "维护", "fault": "故障", "construction": "建设中"}
STOP_KINDS = {"stopped", "maintenance", "fault"}
LINE_STATUSES = {"running", "construction", *STOP_KINDS}


class ValidationError(ValueError):
    """表示可直接向编辑人员展示的输入错误。"""


def parse_datetime(value, zone="UTC"):
    """解析日期时间；value 为 ISO 字符串，zone 为无偏移时的业务时区；返回 UTC 时间。"""
    if not isinstance(value, str):
        raise ValidationError("日期时间格式不正确")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            local_zone = ZoneInfo(zone)
            result = result.replace(tzinfo=local_zone)
            if result.astimezone(UTC).astimezone(local_zone).replace(tzinfo=None) != result.replace(tzinfo=None):
                raise ValidationError("该本地时间不存在，请检查夏令时切换")
        return result.astimezone(UTC)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ValidationError("日期时间格式或时区不正确") from exc


def day_bounds(day, zone):
    """计算业务日边界；day 为日期字符串，zone 为业务时区；返回 UTC 起止时间元组。"""
    parsed = parse_date(day)
    tz = ZoneInfo(zone)
    return (
        datetime.combine(parsed, time.min, tzinfo=tz).astimezone(UTC),
        datetime.combine(parsed + timedelta(days=1), time.min, tzinfo=tz).astimezone(UTC),
    )


def parse_date(value):
    """校验业务日期；value 为 YYYY-MM-DD 字符串；返回 date，格式错误时抛出校验异常。"""
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValidationError("日期必须为 YYYY-MM-DD")
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValidationError("日期不存在") from exc


def parse_month(value):
    """校验月份；value 为 YYYY-MM；返回同一字符串，月份非法时抛出校验异常。"""
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}", value):
        raise ValidationError("月份必须为 YYYY-MM")
    parse_date(value + "-01")
    return value


def number(value, label, integer=False, optional=False, maximum=None):
    """校验数值；label 用于错误消息，integer/optional/maximum 控制约束；返回数字或空值。"""
    if value is None or value == "":
        if optional:
            return None
        raise ValidationError(f"{label}不能为空")
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValidationError(f"{label}必须为有效数字")
    if value < 0 or value > 1_000_000_000_000 or (maximum is not None and value > maximum):
        raise ValidationError(f"{label}超出允许范围")
    if not math.isfinite(value):
        raise ValidationError(f"{label}必须为有效数字")
    if integer and int(value) != value:
        raise ValidationError(f"{label}必须为整数")
    return int(value) if integer else value


def text(value, label, maximum=80, optional=False):
    """校验文本；value 为输入值，label 为字段名，maximum 为长度限制；返回去除首尾空格的文本。"""
    if value is None and optional:
        return ""
    if not isinstance(value, str):
        raise ValidationError(f"{label}必须为文本")
    result = value.strip()
    if not result and not optional:
        raise ValidationError(f"{label}不能为空")
    if len(result) > maximum:
        raise ValidationError(f"{label}最多 {maximum} 个字符")
    return result


def ratio(numerator, denominator):
    """计算百分比；参数为分子、分母；缺少数据或分母为零时返回 None，否则返回两位小数。"""
    if numerator is None or denominator is None or denominator == 0:
        return None
    return round(numerator / denominator * 100, 2)


def parallel_line_count(line_id):
    """读取并行线数量；line_id 为固定产线标识；返回点焊 R4/R5/R6 的三条线或其他产线的一条线。"""
    return 3 if line_id == 2 else 1


def planned_equipment_hours(planned_hours, line_id):
    """换算设备总计划；参数为单线计划小时与产线标识；返回精确小数换算后的总小时，避免边界浮点误差。"""
    return float(Decimal(str(planned_hours)) * parallel_line_count(line_id))


def production_oee_issues(line, day):
    """检查日报完整性；line 为含产能的产线日报，day 为日期；返回可定位的字段问题及错误级别。"""
    actual, planned, abnormal = line.get("actual_qty"), line.get("planned_hours"), line.get("abnormal_hours")
    # 计划工时按单线填写，点焊停机为三条并行线合计，校验前统一为设备总工时。
    planned = planned_equipment_hours(planned, line["id"]) if planned is not None else None
    if actual is None:
        return []
    issues = []

    def add(field, message, severity="warning"):
        """收集字段问题；field/message/severity 为字段、提示和级别；无返回值。"""
        issues.append({"date": day, "line_id": line["id"], "name": line["name"],
                       "field": field, "message": message, "severity": severity})

    if actual > 0 and planned == 0:
        add("planned_hours", "已有产量，计划生产时长必须大于 0，请核对。", "error")
    if abnormal is not None and planned is not None and abnormal > planned:
        add("abnormal_hours", "异常停机工时不能超过计划生产时长，请核对。", "error")
    elif actual > 0 and abnormal is not None and planned is not None and abnormal >= planned:
        add("abnormal_hours", f"已有产量 {actual:g}，异常停机工时必须小于计划工时 {planned:g} 小时，请核对。", "error")
    if line["id"] not in (3, 4):
        return issues
    if planned == 0 and actual == 0:
        add("planned_hours", "计划生产时长为 0，本月设备综合效率暂无法计算；请核对是否无生产计划。")
    if abnormal is None:
        add("abnormal_hours", "未填写异常停机工时，本月设备综合效率暂无法计算；确认无异常停机请填 0。")
    if actual > 0 and line.get("defect_qty") is None:
        add("defect_qty", "未填写不良数量，本月设备综合效率暂无法计算；确认无不良请填 0。")
    if not line.get("theoretical_max_per_hour"):
        add("theoretical_max_per_hour", "未配置设备理论最大产量，请到流程与设置填写。")
    return issues


def line_availability(planned_hours, abnormal_hours, line_id=None):
    """计算日报时间开动率；参数为单线计划、合计停机及产线标识；返回百分比，缺少停机或无计划时为空。"""
    planned = number(planned_hours, "计划生产时长", maximum=24)
    planned = planned_equipment_hours(planned, line_id)
    abnormal = number(abnormal_hours, "异常停机工时", optional=True, maximum=planned)
    return ratio(planned - abnormal, planned) if abnormal is not None else None
