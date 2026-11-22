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


def initialize_database(path, zone, timestamp):
    """创建表及固定展示配置；path、zone、timestamp 为路径、时区和初始化时间；无返回值。"""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with connect_database(destination) as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(Path(__file__).with_name("schema.sql").read_text(encoding="utf-8"))
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
