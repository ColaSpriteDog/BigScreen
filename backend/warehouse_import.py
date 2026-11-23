"""统一解析仓库日报中的库存和当月出货，不持久化上传文件。"""

from .domain import parse_month
from .inventory_import import parse_inventory_file
from .shipment_import import parse_shipment_file


def parse_warehouse_file(upload, month):
    """解析两类业务表；upload 为内存上传流、month 为出货月份；返回完整预览，任一表错误时整批拒绝。"""
    parse_month(month)
    inventory = parse_inventory_file(upload)
    shipments = parse_shipment_file(upload, month)
    return {"month": month, "inventory": inventory, "shipments": shipments}
