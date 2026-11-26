"""按月份解析生产日报的数量、工时及异常停机，仅使用已保存值，不执行公式或批注。"""

from calendar import monthrange
from decimal import Decimal, ROUND_HALF_UP
import re

from .domain import ValidationError, parse_month, planned_equipment_hours
from .inventory_import import INVALID_CELL, cell_text, parse_excel_sheet, stock_number


# 行号来自约定模板；实际与报废必须读取明细，不能把空白明细的汇总公式零值当作已录入。
LAYOUTS = (
    (1, "激光切割", "米", [(26, "切割线周长"), (36, "切割线周长")],
     [(29, "报废米数"), (39, "报废米数")], [(25, "标准产能（米）"), (35, "标准产能（米）")]),
    (2, "机器人点焊", "件", [(23, "实际产能R4"), (28, "实际产能R5"), (33, "实际产能R6"),
     (41, "实际产能R4"), (46, "实际产能R5"), (51, "实际产能R6")],
     [(24, "报废R4"), (29, "报废R5"), (34, "报废R6"), (42, "报废R4"), (47, "报废R5"), (52, "报废R6")],
     [(22, "标准产能R4"), (27, "标准产能R5"), (32, "标准产能R6"), (40, "标准产能R4"),
      (45, "标准产能R5"), (50, "标准产能R6")]),
    (3, "激光焊接", "件", [(25, "实际产能左"), (30, "实际产能右"), (38, "实际产能左"), (43, "实际产能右")],
     [(26, "报废左"), (31, "报废右"), (39, "报废左"), (44, "报废右")],
     [(24, "标准产能左"), (29, "标准产能右"), (37, "标准产能左"), (42, "标准产能右")]),
    (4, "激光焊接 (B)", "件", [(25, "实际产能左"), (30, "实际产能右"), (38, "实际产能左"), (43, "实际产能右")],
     [(26, "报废左"), (31, "报废右"), (39, "报废左"), (44, "报废右")],
     [(24, "标准产能左"), (29, "标准产能右"), (37, "标准产能左"), (42, "标准产能右")]),
)

HOUR_ROWS = {1: (24, 34), 2: (21, 39), 3: (23, 36), 4: (23, 36)}
# 落料线使用全天停机合计；点焊线汇总白夜班 R4/R5/R6 明细，避免遗漏设备停机。
STOP_ROWS = {
    1: ((41, "异常停机工时"),),
    2: ((26, "异常停机工时R4"), (31, "异常停机工时R5"), (36, "异常停机工时R6"),
        (44, "异常停机工时R4"), (49, "异常停机工时R5"), (54, "异常停机工时R6")),
}


def displayed_hours(value, number_format):
    """按源表显示精度取工时；value 为非负小时数，number_format 为 Excel 格式；返回舍入后的数值。"""
    if number_format.lower() == "general":
        return value
    # 工时模板使用普通小数格式；先逐班四舍五入，再合计，避免显示 10.2 却按隐藏精度累计。
    section = number_format.split(";")[0]
    section = re.sub(r'"[^"]*"|\[[^\]]*\]|_.|\*.', "", section).strip()
    match = re.fullmatch(r"[#,]*0(?:\.([0#]+))?", section)
    if not match:
        raise ValidationError("工时显示格式不支持，请使用普通数字格式")
    decimals = len(match[1] or "")
    return float(Decimal(str(value)).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP))


def parse_daily_hours(data, day, identifier, sheet, formula_cells, number_formats=None):
    """提取日工时；参数含源行、日序、产线、公式及数字格式；返回按显示值合计的工时和警告。"""
    column = day + 1
    warnings = []

    def hours(row):
        """读取小时数；row 为一基行号；返回非负小数，错误包含表名、日期和行号。"""
        label = f"“{sheet}”第 {day} 日第 {row} 行"
        raw = data[row - 1][column] if row <= len(data) and column < len(data[row - 1]) else None
        if raw is INVALID_CELL:
            raise ValidationError(label + "工时公式缓存缺失或错误")
        # 工时允许附带 H/h 单位；仅接受数值前缀，故障说明等文本仍交由数字校验拦截。
        if isinstance(raw, str):
            matched = re.fullmatch(r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*[Hh]\s*", raw)
            if matched:
                raw = matched[1]
        return stock_number(raw, label + "工时", optional=True)

    planned = None
    abnormal = None
    try:
        values = []
        for row in HOUR_ROWS[identifier]:
            if row > len(data) or cell_text(data[row - 1][1]) != "工时（H）":
                raise ValidationError(f"“{sheet}”第 {row} 行应为工时（H）")
            value = hours(row)
            if value is None:
                raise ValidationError(f"“{sheet}”第 {day} 日第 {row} 行工时为空")
            values.append(displayed_hours(value, (number_formats or {}).get((row, column + 1), "General")))
        planned = float(sum(Decimal(str(value)) for value in values))
        if planned > 24:
            raise ValidationError(f"“{sheet}”第 {day} 日白夜班总工时不能超过 24 小时")
    except ValidationError as error:
        planned = None
        warnings.append(str(error))
    try:
        if identifier in STOP_ROWS:
            stops = []
            for row, label in STOP_ROWS[identifier]:
                if row > len(data) or cell_text(data[row - 1][1]) != label:
                    raise ValidationError(f"“{sheet}”第 {row} 行应为“{label}”，请核对异常停机行")
                # 仅已录入产量且停机行存在时，空白明细按无停机处理；缓存错误仍由 hours 拦截。
                stops.append(hours(row) or 0)
            total = float(sum(Decimal(str(value)) for value in stops))
            # 点焊按三条并行线累计设备工时，不能与单线一天的计划工时比较。
            capacity = planned_equipment_hours(planned if planned is not None else 24, identifier)
            if total > capacity + 1e-8:
                raise ValidationError(f"“{sheet}”第 {day} 日异常停机合计超过计划工时，请核对停机明细")
            # 白夜班按同一机器人配对，防止单台全天停机超限被其他两台的空闲额度掩盖。
            if identifier == 2 and any(stops[index] + stops[index + 3] > (planned if planned is not None else 24) + 1e-8
                                       for index in range(3)):
                raise ValidationError(f"“{sheet}”第 {day} 日单条点焊线异常停机超过计划工时，请核对停机明细")
            # 源计划工时异常时仍提取有效停机值；保存时按保留的计划工时再次校验。
            abnormal = total
        elif planned is not None:
            stops = []
            for index, candidates in enumerate(((28,), (33,), (41,), (45, 46))):
                matching = [row for row in candidates if row <= len(data)
                            and cell_text(data[row - 1][1]) == "异常停机工时"]
                if len(matching) != 1:
                    raise ValidationError(f"“{sheet}”第 {candidates[0]} 行附近缺少唯一的异常停机工时")
                row = matching[0]
                # 数据数组使用零基列号，Excel 公式和格式映射使用一基列号，必须核对当前日期的同一单元格。
                formula = formula_cells.get((row, column + 1), "")
                # 源模板曾将报废率公式放在停机行，不能把其缓存小数当成小时。
                rate_formula = rf"=\$?[A-Z]+\$?{44}\s*/\s*\$?[A-Z]+\$?{43}"
                if formula == "%" or "%" in (number_formats or {}).get((row, column + 1), "") or (index == 3 and re.fullmatch(rate_formula, formula, re.IGNORECASE)):
                    raise ValidationError(f"“{sheet}”第 {day} 日第 {row} 行是报废率公式，请修正异常停机行")
                stop = hours(row) or 0
                if stop > values[index // 2] + 1e-8:
                    raise ValidationError(f"“{sheet}”第 {day} 日第 {row} 行异常停机 {stop:g} 小时超过班次工时")
                stops.append(stop)
            # 左右件共用班次工时，停机小时按各明细累计；不与空白的班次合计行重复相加。
            if stops[0] + stops[1] > values[0] + 1e-8 or stops[2] + stops[3] > values[1] + 1e-8:
                raise ValidationError(f"“{sheet}”第 {day} 日左右件异常停机合计超过班次工时，请核对重叠停机")
            abnormal = float(sum(Decimal(str(value)) for value in stops))
    except ValidationError as error:
        warnings.append(str(error))
    # 标准日产量是生产目标，不能据此推算设备最快节拍；理论产能由后台独立配置。
    result = {"warnings": warnings, "abnormal_hours": abnormal}
    if planned is not None and 0 <= planned <= 24:
        result["planned_hours"] = planned
    return result


def parse_production_rows(rows, month, layout, formula_cells=None, number_formats=None):
    """解析单条产线；rows/month/layout 为缓存、月份和映射，后两参数为公式及格式；返回日报、工时与标准。"""
    identifier, sheet, unit, actual_rows, defect_rows, standard_rows = layout
    data = list(rows)
    days = monthrange(int(month[:4]), int(month[5:]))[1]
    for row, label in actual_rows + defect_rows + standard_rows:
        if row > len(data) or len(data[row - 1]) < 2 or cell_text(data[row - 1][1]) != label:
            raise ValidationError(f"“{sheet}”第 {row} 行应为“{label}”，请核对生产日报模板")

    def value(row, day):
        """读取单日源值；row 为一基行号、day 为日序；返回数字或空值，错误附带工作表和单元格位置。"""
        column = day + 1
        source = data[row - 1][column] if len(data[row - 1]) > column else None
        label = f"“{sheet}”第 {row} 行第 {day} 日"
        if source is INVALID_CELL:
            raise ValidationError(label + "存在 Excel 错误或公式缓存缺失，请重新计算并保存")
        return stock_number(source, label, integer=unit == "件", optional=True)

    def total(values):
        """汇总同单位数量；values 为含空值的数字列表；返回精确求和结果，全空保持为空。"""
        known = [Decimal(str(item)) for item in values if item is not None]
        return float(sum(known)) if known else None

    items, standards = [], []
    for day in range(1, days + 1):
        standard_values = [value(row, day) for row, _ in standard_rows]
        if any(item is None for item in standard_values):
            raise ValidationError(f"“{sheet}”第 {day} 日标准产量不完整")
        standards.append(total(standard_values))
        actual = total([value(row, day) for row, _ in actual_rows])
        defects = total([value(row, day) for row, _ in defect_rows])
        if actual is None:
            if defects not in (None, 0):
                raise ValidationError(f"“{sheet}”第 {day} 日有报废但未填写实际产量")
            continue
        if defects is not None and defects > actual:
            raise ValidationError(f"“{sheet}”第 {day} 日报废数量大于实际产量")
        timing = parse_daily_hours(data, day, identifier, sheet, formula_cells or {}, number_formats)
        items.append({"date": f"{month}-{day:02d}", "line_id": identifier, "unit": unit,
                      "actual_qty": actual, "defect_qty": defects, "sheet": sheet, **timing})
    if len(set(standards)) != 1:
        raise ValidationError(f"“{sheet}”各日标准产量不一致，系统使用固定标准日产量，请先统一")
    # 短月份模板仍保留三十一列；标准模板值可忽略，但不能静默丢弃超出月份的实际生产数量。
    for day in range(days + 1, 32):
        if any(value(row, day) not in (None, 0) for row, _ in actual_rows + defect_rows):
            raise ValidationError(f"“{sheet}”第 {day} 日有数量，但 {month} 没有该日期")
    return {"items": items, "line_id": identifier, "sheet": sheet, "unit": unit, "standard_qty": standards[0]}


def parse_production_file(upload, month):
    """解析整月四线日报；upload 为上传流、month 为用户选择月份；返回预览数据，不保存文件或写库。"""
    parse_month(month)
    items, standards = [], []
    for layout in LAYOUTS:
        formulas = {}
        formats = {}
        result = parse_excel_sheet(upload, cell_text(layout[1]),
            lambda rows: parse_production_rows(rows, month, layout, formulas, formats),
            formula_cells=formulas, number_formats=formats)
        items.extend(result.pop("items"))
        standards.append(result)
    # 完整模板即使全月为空也可预览，用于同步清空该月旧日报。
    return {"month": month, "items": sorted(items, key=lambda item: (item["date"], item["line_id"])), "standards": standards}
