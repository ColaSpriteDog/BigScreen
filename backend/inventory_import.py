"""从仓库日报汇总表提取库存快照，只解析上传流，不保存文件或写入数据库。"""

from itertools import islice
from pathlib import Path
import re
import struct
from zipfile import BadZipFile, ZipFile
from xml.etree.ElementTree import ParseError

import xlrd
from openpyxl import load_workbook
from xlrd.compdoc import CompDocError

from .domain import ValidationError, number, text
from .service import unit_totals


MAX_FILE_BYTES = 160 * 1024 * 1024
MAX_SHEET_ROWS = 5000
INVALID_CELL = object()
HEADERS = {"material": "物料名称", "product": "产品名称", "specification": "规格型号",
           "quantity": "剩余数量", "unit": "单位", "weight_kg": "剩余总重量",
           "cuttable_qty": "预计可切割数量"}


def cell_text(value):
    """规范单元格文本；value 为 Excel 值；返回去除换行空白的文字，数值规格不附加 .0。"""
    if value is None:
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return format(value, ".15g")
    return re.sub(r"\s+", "", str(value))


def stock_number(value, label, integer=False, optional=False):
    """校验库存数字；value 为原值，label 用于报错；返回有效数字，空白可选字段保持为空。"""
    if isinstance(value, str):
        value = value.strip()
        if value:
            try:
                value = float(value.replace(",", "").replace("，", ""))
            except ValueError:
                raise ValidationError(f"{label}必须为有效数字") from None
    return number(value, label, integer=integer, optional=optional)


def parse_rows(rows):
    """提取汇总表记录；rows 为按行迭代的缓存值；返回完整库存及汇总，任一错误拒绝整份导入。"""
    rows = iter(rows)
    columns = None
    header_row = 0
    for header_row, values in enumerate(islice(rows, 30), 1):
        labels = [cell_text(value) for value in values]
        if all(label in labels for label in list(HEADERS.values())[:5]):
            if any(labels.count(label) != 1 for label in HEADERS.values() if label in labels):
                raise ValidationError("汇总表存在重复库存表头，请检查文件")
            columns = {key: labels.index(label) for key, label in HEADERS.items() if label in labels}
            break
    if columns is None:
        raise ValidationError("汇总表缺少物料名称、产品名称、规格型号、剩余数量或单位表头")

    result = {"raw": [], "semi": []}
    seen = {"raw": set(), "semi": set()}
    errors = []
    group = ""
    for row_number, values in enumerate(rows, header_row + 1):
        if row_number > MAX_SHEET_ROWS:
            raise ValidationError(f"汇总表超过 {MAX_SHEET_ROWS} 行，请移除多余空白行后重试")
        # 左右件分组在第一列合并，后续空单元格沿用分组，不把库存数值向下填充。
        group_column = columns["material"] - 1
        if group_column >= 0 and group_column < len(values) and cell_text(values[group_column]):
            group = cell_text(values[group_column])
        record = {key: values[index] if index < len(values) else None for key, index in columns.items()}
        material, product = cell_text(record["material"]), cell_text(record["product"])
        if not any(cell_text(value) for value in record.values()):
            continue
        # 页尾合并的备注仅为说明，不属于库存记录，也不作为可执行指令处理。
        if material.startswith(("备注：", "备注:")) and not product and not cell_text(record["specification"]) and not cell_text(record["quantity"]):
            continue
        if material in {"物料名称", "合计", "小计", "总计"} or product in {"合计", "小计", "总计"}:
            continue
        try:
            if any(value is INVALID_CELL for value in record.values()):
                raise ValidationError("库存字段存在 Excel 错误、公式缓存缺失或类型不正确，请重新计算并保存")
            if material == "半成品":
                kind = "semi"
                category = group if group in {"左件", "右件", "其他"} else (
                    "左件" if "左件" in product else "右件" if "右件" in product else "")
                if not category:
                    raise ValidationError("无法识别半成品类别，请填写左件、右件或其他分组")
                item = {"category": category, "material": text(product, "产品名称", maximum=30),
                        "warehouse": "半成品仓库"}
            elif material in {"钢卷", "原材料", "钢板", "板材"}:
                kind = "raw"
                item = {"material": material, "position": text(product, "产品名称", maximum=30),
                        "warehouse": "原材料仓库",
                        "weight_kg": stock_number(record.get("weight_kg"), "剩余总重量", optional=True),
                        "cuttable_qty": stock_number(record.get("cuttable_qty"), "预计可切割数量", integer=True, optional=True)}
            else:
                raise ValidationError(f"无法识别物料类型“{material}”")
            item.update(specification=text(cell_text(record["specification"]), "规格型号", maximum=80 if kind == "raw" else 30),
                        quantity=stock_number(record["quantity"], "剩余数量", integer=kind == "semi"),
                        unit=text(cell_text(record["unit"]), "单位", maximum=30))
            # 源表以“个”计数的半成品统一映射为“件”，只调整单位名称，不换算数量。
            if kind == "semi" and item["unit"] == "个":
                item["unit"] = "件"
            identity = tuple(item[key] for key in (["material", "warehouse", "specification", "position", "unit"]
                                                  if kind == "raw" else ["category", "material", "warehouse", "specification", "unit"]))
            if identity in seen[kind]:
                raise ValidationError("物料、规格和类别重复，请合并重复记录后重试")
            seen[kind].add(identity)
            item["source_row"] = row_number
            result[kind].append(item)
            if len(result[kind]) > 2000:
                raise ValidationError("同类库存不能超过 2000 条")
        except ValidationError as error:
            errors.append(f"第 {row_number} 行：{error}")
            if len(errors) >= 20:
                break
    if errors:
        raise ValidationError("汇总表解析失败：\n" + "\n".join(errors))
    if not result["raw"] and not result["semi"]:
        raise ValidationError("汇总表中没有可导入的库存记录")
    result.update(sheet="汇总表", raw_totals=unit_totals(result["raw"]), semi_totals=unit_totals(result["semi"]),
                  zero_count=sum(item["quantity"] == 0 for item in result["raw"]))
    return result


def parse_inventory_file(upload):
    """解析 Excel 上传；upload 为 FileStorage；返回汇总表库存快照，关闭读取器且不保存源文件。"""
    return parse_excel_sheet(upload, "汇总表", parse_rows)


def parse_excel_sheet(upload, sheet_name, parser, allow_dates=False, formula_cells=None):
    """读取指定表；upload/parser 为上传与解析器，allow_dates 控制日期，formula_cells 可接收公式位置；返回解析结果。"""
    extension = Path(upload.filename or "").suffix.lower()
    if extension not in {".xls", ".xlsx"}:
        raise ValidationError("请选择 .xls 或 .xlsx 格式的仓库日报表")
    workbook = None
    formula_book = None
    try:
        upload.stream.seek(0, 2)
        if upload.stream.tell() > MAX_FILE_BYTES:
            raise ValidationError("Excel 文件不能超过 160 MiB")
        upload.stream.seek(0)
        if extension == ".xls":
            content = upload.stream.read(MAX_FILE_BYTES + 1)
            if len(content) > MAX_FILE_BYTES:
                raise ValidationError("Excel 文件不能超过 160 MiB")
            workbook = xlrd.open_workbook(file_contents=content, on_demand=True, formatting_info=formula_cells is not None)
            sheets = {cell_text(name): name for name in workbook.sheet_names()}
            if sheet_name not in sheets:
                raise ValidationError(f"Excel 中未找到“{sheet_name}”工作表")
            sheet = workbook.sheet_by_name(sheets[sheet_name])
            if formula_cells is not None:
                for row in range(min(sheet.nrows, MAX_SHEET_ROWS + 1)):
                    for column in range(min(sheet.ncols, 256)):
                        style = workbook.xf_list[sheet.cell_xf_index(row, column)]
                        if "%" in workbook.format_map[style.format_key].format_str:
                            formula_cells[(row + 1, column + 1)] = "%"
            values = ([xlrd.xldate_as_datetime(cell.value, workbook.datemode)
                       if allow_dates and cell.ctype == xlrd.XL_CELL_DATE else
                       cell.value if cell.ctype not in {xlrd.XL_CELL_ERROR, xlrd.XL_CELL_BOOLEAN, xlrd.XL_CELL_DATE}
                       else INVALID_CELL for cell in sheet.row(index)[:256]]
                      for index in range(sheet.nrows))
        else:
            # 仅读取文件流和公式缓存，不执行宏、外部链接或公式，也不持久化文件。
            with ZipFile(upload.stream) as archive:
                if sum(item.file_size for item in archive.infolist()) > 512 * 1024 * 1024:
                    raise ValidationError("Excel 解压后内容过大，请精简文件后重试")
            upload.stream.seek(0)
            workbook = load_workbook(upload.stream, read_only=True, data_only=True, keep_links=False)
            sheets = {cell_text(name): name for name in workbook.sheetnames}
            if sheet_name not in sheets:
                raise ValidationError(f"Excel 中未找到“{sheet_name}”工作表")
            sheet = workbook[sheets[sheet_name]]
            # 额外核对公式来源，避免未保存缓存的可选重量被误认为真正空白而覆盖旧数据。
            upload.stream.seek(0)
            formula_book = load_workbook(upload.stream, read_only=True, data_only=False, keep_links=False)
            formula_sheet = formula_book[sheets[sheet_name]]
            bounds = {"max_row": min(sheet.max_row or 1, MAX_SHEET_ROWS + 1),
                      "max_col": min(sheet.max_column or 1, 256)}
            # 仅记录公式文本供业务核对字段含义，不求值或执行公式。
            if formula_cells is not None:
                for row in formula_sheet.iter_rows(**bounds):
                    for cell in row:
                        if cell.data_type == "f":
                            formula_cells[(cell.row, cell.column)] = cell.value
                        elif "%" in (cell.number_format or ""):
                            formula_cells[(cell.row, cell.column)] = "%"
            values = ([cell.value if cell.data_type not in {"e", "b"} and (allow_dates or not cell.is_date)
                       and not (formula.data_type == "f" and cell.value is None and cell.data_type != "str")
                       else INVALID_CELL for cell, formula in zip(row, formulas)]
                      for row, formulas in zip(sheet.iter_rows(**bounds), formula_sheet.iter_rows(**bounds)))
        return parser(values)
    except (xlrd.XLRDError, CompDocError, BadZipFile, ParseError, OSError, ValueError, KeyError, struct.error, EOFError) as error:
        if isinstance(error, ValidationError):
            raise
        raise ValidationError("无法读取 Excel，请确认文件未损坏、未加密，并用 Excel 重新计算和保存后重试") from error
    finally:
        if formula_book is not None:
            formula_book.close()
        if workbook is not None:
            if extension == ".xls":
                workbook.release_resources()
            else:
                workbook.close()
