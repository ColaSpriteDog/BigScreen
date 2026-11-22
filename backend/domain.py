"""时间区间与数据校验规则，不依赖网页或数据库。"""

from datetime import datetime, time, timedelta, timezone
import math
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc
STATUS_LABELS = {"running": "运行中", "normal": "正常", "stopped": "停机", "maintenance": "维护", "fault": "故障"}
STOP_KINDS = {"stopped", "maintenance", "fault"}


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


def line_runtime(now, zone, downtimes):
    """计算产线状态与今日运行率；now 为当前时间，zone 为业务时区，downtimes 为停机记录；返回统计字典。"""
    now = now.astimezone(UTC)
    local_day = now.astimezone(ZoneInfo(zone)).date().isoformat()
    beginning, _ = day_bounds(local_day, zone)
    elapsed = max((now - beginning).total_seconds(), 0)
    spans = []
    active = []
    for item in downtimes:
        start = parse_datetime(item["start_at"])
        finish = parse_datetime(item["end_at"]) if item.get("end_at") else now
        if start <= now and (not item.get("end_at") or now < finish):
            active.append(item)
        left, right = max(start, beginning), min(finish, now)
        if right > left:
            spans.append((left, right))
    spans.sort()
    merged = []
    for start, finish in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], finish))
        else:
            merged.append((start, finish))
    stopped = sum((finish - start).total_seconds() for start, finish in merged)
    priority = {"stopped": 1, "maintenance": 2, "fault": 3}
    active.sort(key=lambda item: priority.get(item.get("kind"), 1), reverse=True)
    state = active[0].get("kind", "stopped") if active else "running"
    return {
        "status": state,
        "status_label": STATUS_LABELS[state],
        "runtime_rate": round((elapsed - stopped) / elapsed * 100, 2) if elapsed else None,
        "downtime_minutes": round(stopped / 60, 2),
        "active_downtime_id": active[0].get("id") if active else None,
        "open_downtime_id": next((item.get("id") for item in active if not item.get("end_at")), None),
    }
