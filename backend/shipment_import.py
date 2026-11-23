"""从成品出库明细逐行提取指定月份的出货记录，仅解析内存中的工作簿。"""

from datetime import date, datetime
from itertools import islice

from .domain import ValidationError, parse_month, text
from .inventory_import import INVALID_CELL, MAX_SHEET_ROWS, cell_text, parse_excel_sheet, stock_number


HEADERS = {"date": "日期", "part_no": "零件号", "name": "名称", "quantity": "数量"}


def shipment_date(value):
    """识别出库日期；value 为 Excel 日期或年月日文字；返回 YYYY-MM-DD，缺失或无效时拒绝导入。"""
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, str):
        for pattern in ("%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(value.strip(), pattern).strftime("%Y-%m-%d")
            except ValueError:
                continue
    raise ValidationError("日期缺失或格式不正确，请使用 Excel 日期或完整的年月日")


def parse_shipment_rows(rows, month):
    """提取出库明细；rows 为源行、month 为目标月份；返回当月逐条记录，保留原顺序及重复件号。"""
    parse_month(month)
    rows = iter(rows)
    columns = None
    header_row = 0
    for header_row, values in enumerate(islice(rows, 30), 1):
        labels = [cell_text(value) for value in values]
        if all(label in labels for label in HEADERS.values()):
            if any(labels.count(label) != 1 for label in HEADERS.values()):
                raise ValidationError("成品出库明细存在重复表头，请检查文件")
            columns = {key: labels.index(label) for key, label in HEADERS.items()}
            break
    if columns is None:
        raise ValidationError("成品出库明细缺少日期、零件号、名称或数量表头")

    items, errors, available_months = [], [], set()
    for row_number, values in enumerate(rows, header_row + 1):
        if row_number > MAX_SHEET_ROWS:
            raise ValidationError(f"成品出库明细超过 {MAX_SHEET_ROWS} 行，请精简文件后重试")
        record = {key: values[index] if index < len(values) else None for key, index in columns.items()}
        if not any(cell_text(record[key]) for key in ("part_no", "name", "quantity")):
            continue
        if cell_text(record["part_no"]) in {"零件号", "合计", "小计", "总计"} or cell_text(record["name"]) in {"合计", "小计", "总计"}:
            continue
        try:
            if any(value is INVALID_CELL for value in record.values()):
                raise ValidationError("出库字段存在 Excel 错误或公式缓存缺失，请重新计算并保存")
            day = shipment_date(record["date"])
            row_month = day[:7]
            available_months.add(row_month)
            if row_month != month:
                continue
            part = text(cell_text(record["part_no"]), "零件号", maximum=40)
            name = text(record["name"], "名称", maximum=80)
            quantity = stock_number(record["quantity"], "数量", integer=True)
            # 每个源行独立保留；相同日期、件号、名称和数量也不能合并或去重。
            items.append({"shipment_date": day, "part_no": part, "name": name, "quantity": quantity, "source_row": row_number})
            if len(items) > 2000:
                raise ValidationError("当月出货记录不能超过 2000 条")
        except ValidationError as error:
            errors.append(f"第 {row_number} 行：{error}")
            if len(errors) >= 20:
                break
    if errors:
        raise ValidationError("成品出库明细解析失败：\n" + "\n".join(errors))
    if not items:
        available = "、".join(sorted(available_months)) or "无"
        raise ValidationError(f"成品出库明细中没有 {month} 的出货记录；表内月份：{available}")
    return {"sheet": "成品出库明细", "month": month, "items": items, "source_count": len(items),
            "shipped_qty": sum(item["quantity"] for item in items), "available_months": sorted(available_months)}


def parse_shipment_file(upload, month):
    """解析出库上传；upload 为 Excel 文件流、month 为目标月份；返回出货预览，不保存文件或写入数据库。"""
    parse_month(month)
    return parse_excel_sheet(upload, "成品出库明细", lambda rows: parse_shipment_rows(rows, month), allow_dates=True)
