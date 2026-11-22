"""显式生成大屏演示数据，先进行 SQLite 在线备份，服务启动不会自动调用。"""

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import random
import sqlite3
from uuid import uuid4
from zoneinfo import ZoneInfo

from backend.db import connect_database
from backend.domain import day_bounds
from backend.service import (rows, save_inventory, save_production, save_sales, settings,
                             versioned_write)


def stock_examples():
    """构建库存演示明细；无参数；返回原材料与半成品字典，保留全部独立业务字段。"""
    raw = []
    materials = [('1.6*1430/1.6*1500', 'A柱', 16, 185370, 19200),
                 ('1.6*1142', 'A柱', 0, 0, 0), ('1.8*1245', 'B柱', 5, 61210, 6250),
                 ('1.8*1300', '补丁板', 9, 110850, 16200), ('1.4*1002', 'A柱下', 0, 0, 0),
                 ('1.4*1004', 'A柱下', 4, 43220, 5200), ('1.4*1414', '门槛', 7, 82720, 8400),
                 ('1.2*1250', '试机料', 0.5, None, None)]
    for specification, position, quantity, weight, cuttable in materials:
        raw.append({'material': '钢卷', 'warehouse': '原材料仓库', 'specification': specification,
                    'position': position, 'quantity': quantity, 'unit': '卷',
                    'weight_kg': weight, 'cuttable_qty': cuttable})
    semi = []
    names = [('门槛', '1.4'), ('A柱下', '1.4'), ('A柱', '1.6'), ('B柱', '1.8'), ('补丁板', '1.8')]
    amounts = {'左件': [2977, 2563, 2895, 1623, 4410], '右件': [4854, 2030, 2249, 1157, 5196]}
    for category, quantities in amounts.items():
        for (name, specification), quantity in zip(names, quantities):
            semi.append({'category': category, 'material': name + category, 'warehouse': '半成品仓库',
                         'specification': specification, 'quantity': quantity, 'unit': '个'})
    return {'raw': raw, 'semi': semi}


def apply_demo_data(database_path, now=None):
    """备份并写入演示数据；database_path 为已有数据库、now 可固定生成时间；返回备份及生成摘要。"""
    path = Path(database_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f'数据库不存在：{path}；请先启动服务创建数据库')
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    connection = connect_database(path)
    try:
        config = settings(connection)
        lines = rows(connection, 'SELECT * FROM production_line ORDER BY sort_order')
        if [line['id'] for line in lines] != [1, 2, 3, 4]:
            raise ValueError('演示数据需要本项目固定的四条产线')
        zone = config['timezone']
        today = now.astimezone(ZoneInfo(zone)).date()
        backup_dir = path.parent / 'backups'
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f'{path.stem}-before-demo-{now.strftime("%Y%m%dT%H%M%SZ")}-{uuid4().hex[:8]}.sqlite3'
        backup = sqlite3.connect(str(backup_path))
        try:
            connection.backup(backup)
        finally:
            backup.close()
        generator = random.Random(20261121)
        quantities_today = [1280, 960, 1420, 1360]
        standard_quantities = [1320, 980, 1450, 1400]
        with versioned_write(connection, {'version': config['version']}, now):
            for line, standard in zip(lines, standard_quantities):
                connection.execute('UPDATE production_line SET standard_qty=COALESCE(standard_qty,?) WHERE id=?',
                                   (standard, line['id']))
            if not config['safety_start_date']:
                connection.execute('UPDATE screen_settings SET safety_start_date=? WHERE id=1',
                                   ((today - timedelta(days=368)).isoformat(),))
            for offset in range(29, -1, -1):
                day = (today - timedelta(days=offset)).isoformat()
                production = []
                for index, line in enumerate(lines):
                    actual = quantities_today[index] if offset == 0 else quantities_today[index] + generator.randrange(-170, 161, 10)
                    defects = [18, 22, 15, 38][index] if offset == 0 else max(1, round(actual * generator.uniform(0.008, 0.027)))
                    production.append({'id': line['id'], 'actual_qty': actual, 'defect_qty': defects})
                stops = []
                if offset == 0:
                    start_day, _ = day_bounds(day, zone)
                    stops.append({'line_id': 4, 'start_at': max(start_day, now - timedelta(minutes=45)).isoformat(),
                                  'end_at': (now + timedelta(hours=2)).isoformat(), 'kind': 'maintenance',
                                  'note': '演示数据：激光焊接设备维护'})
                save_production(connection, {'lines': production, 'downtimes': stops,
                    'overview': {'planned_qty': 8600, 'actual_qty': 6010 if offset == 0 else 5600 + generator.randrange(0, 1000),
                                 'oee_percent': 95, 'quality_percent': 98.6, 'average_output': 45}}, day, now)
            save_inventory(connection, stock_examples())
            part_numbers = ['G-7033', 'C-3012', 'E-5001', 'A-1024', 'F-6022', 'B-2048',
                            'D-4096', 'H-8044', 'J-9012', 'K-9013', 'L-9014', 'M-9015']
            quantities = [6200, 5400, 4800, 4200, 3600, 3000, 2600, 2200, 1800, 1600, 1200, 800]
            save_sales(connection, {'order_qty': 134490,
                'items': [{'part_no': part, 'quantity': quantity} for part, quantity in zip(part_numbers, quantities)]}, today.strftime('%Y-%m'))
        return {'backup_path': str(backup_path), 'business_date': today.isoformat(), 'days': 30,
                'raw_rows': 8, 'semi_rows': 10, 'shipment_rows': 12, 'version': settings(connection)['version']}
    finally:
        connection.close()


def main():
    """运行显式演示数据命令；命令行可指定 database；无返回值，打印原库备份路径。"""
    parser = argparse.ArgumentParser(description='先备份再生成近30天大屏演示数据，仅在手动执行时写入')
    parser.add_argument('--database', default=str(Path(__file__).resolve().parent / 'data' / 'dashboard.sqlite3'))
    arguments = parser.parse_args()
    result = apply_demo_data(arguments.database)
    print(f'演示数据已保存：业务日期 {result["business_date"]}，近 {result["days"]} 天产量。')
    print(f'原材料 {result["raw_rows"]} 条，半成品 {result["semi_rows"]} 条，出货件号 {result["shipment_rows"]} 条。')
    print(f'写入前完整数据库备份：{result["backup_path"]}')


if __name__ == '__main__':
    main()
