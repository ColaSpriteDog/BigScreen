"""SQLite 连接与初始化，正式数据库只初始化产线和工序配置。"""

from pathlib import Path
import sqlite3

from flask import current_app, g


def connect_database(path):
    """建立数据库连接；path 为 SQLite 文件路径；返回启用外键及等待锁配置的连接。"""
    connection = sqlite3.connect(str(path), timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def get_db():
    """获得当前请求的数据库连接；无参数；返回请求内复用的 SQLite 连接。"""
    if "database" not in g:
        g.database = connect_database(current_app.config["DATABASE"])
    return g.database


def close_db(error=None):
    """关闭请求连接；error 为 Flask 传入的异常信息；无返回值。"""
    connection = g.pop("database", None)
    if connection is not None:
        connection.close()


def migrate_shipment_details(connection):
    """升级旧出货表；connection 为 SQLite 连接；无返回值，保留旧记录并解除件号唯一限制。"""
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(shipment_month_item)")}
    has_unique = any(row["unique"] for row in connection.execute("PRAGMA index_list(shipment_month_item)"))
    if {"name", "shipment_date", "sort_order"} <= columns and not has_unique:
        return
    # 旧数据是月累计，不能伪造出库日期或拆分成逐次明细，原值与人员记录继续保留。
    name = "name" if "name" in columns else "''"
    day = "shipment_date" if "shipment_date" in columns else "NULL"
    order = "sort_order" if "sort_order" in columns else "id"
    sequence = connection.execute("SELECT seq FROM sqlite_sequence WHERE name='shipment_month_item'").fetchone()
    connection.execute("SAVEPOINT shipment_details_migration")
    try:
        connection.execute("CREATE TABLE shipment_month_item_next (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                           "month TEXT NOT NULL REFERENCES sales_month(month) ON DELETE CASCADE, "
                           "part_no TEXT NOT NULL COLLATE NOCASE, quantity INTEGER NOT NULL CHECK(quantity >= 0), "
                           "name TEXT NOT NULL DEFAULT '', shipment_date TEXT, sort_order INTEGER NOT NULL DEFAULT 0)")
        connection.execute("INSERT INTO shipment_month_item_next(id,month,part_no,quantity,name,shipment_date,sort_order) "
                           f"SELECT id,month,part_no,quantity,{name},{day},{order} FROM shipment_month_item")
        connection.execute("DROP TABLE shipment_month_item")
        connection.execute("ALTER TABLE shipment_month_item_next RENAME TO shipment_month_item")
        if sequence:
            connection.execute("UPDATE sqlite_sequence SET seq=MAX(seq,?) WHERE name='shipment_month_item'", (sequence["seq"],))
        connection.execute("RELEASE shipment_details_migration")
    except Exception:
        connection.execute("ROLLBACK TO shipment_details_migration")
        connection.execute("RELEASE shipment_details_migration")
        raise


def initialize_database(path, zone, timestamp):
    """创建表及固定展示配置；path、zone、timestamp 为路径、时区和初始化时间；无返回值。"""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with connect_database(destination) as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(Path(__file__).with_name("schema.sql").read_text(encoding="utf-8"))
        # 理论产能必须由设备或工艺确认，旧库升级时保留空值，不从生产目标推算。
        line_columns = {row["name"] for row in connection.execute("PRAGMA table_info(production_line)")}
        if "theoretical_max_per_hour" not in line_columns:
            connection.execute("ALTER TABLE production_line ADD COLUMN theoretical_max_per_hour REAL "
                               "CHECK (theoretical_max_per_hour IS NULL OR theoretical_max_per_hour > 0)")
        daily_columns = {row["name"] for row in connection.execute("PRAGMA table_info(production_daily)")}
        if "planned_hours" not in daily_columns:
            connection.execute("ALTER TABLE production_daily ADD COLUMN planned_hours REAL NOT NULL DEFAULT 24 "
                               "CHECK (planned_hours >= 0 AND planned_hours <= 24)")
        # 旧日报缺少停机汇总与理想节拍，保留空值，不能据此推断设备效率。
        for column in ("abnormal_hours", "ideal_cycle_hours"):
            if column not in daily_columns:
                connection.execute(f"ALTER TABLE production_daily ADD COLUMN {column} REAL")
        migrate_shipment_details(connection)
        connection.execute(
            "INSERT OR IGNORE INTO screen_settings(id,timezone,updated_at) VALUES(1,?,?)",
            (zone, timestamp),
        )
        nodes = [(1, "钢卷", "01_钢卷.png"), (2, "激光切割", "02_激光切割.png"),
                 (3, "机器人点焊", "03_机器人点焊.png"), (4, "激光焊接", "04_激光焊接.png"),
                 (5, "出货", "05_出货.png")]
        connection.executemany(
            "INSERT OR IGNORE INTO process_node(id,name,image_path,sort_order) VALUES(?,?,?,?)",
            [(identifier, name, "/assets/" + image, identifier) for identifier, name, image in nodes],
        )
        lines = [(1, "01", "激光落料线", 2, 0), (2, "02", "机器人点焊线", 3, 0),
                 (3, "03", "激光拼焊线A", 4, 1), (4, "04", "激光拼焊线B", 4, 1)]
        connection.executemany(
            "INSERT OR IGNORE INTO production_line(id,code,name,process_id,show_in_trend,sort_order) VALUES(?,?,?,?,?,?)",
            [(*line, line[0]) for line in lines],
        )
    connection.close()
