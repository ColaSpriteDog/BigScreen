"""离线程序入口：从外置配置读取端口及数据目录，并提供升级备份命令。"""

import argparse
import configparser
from contextlib import closing
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import sys


def read_configuration(path, base):
    """读取运行配置；path 为 INI 路径、base 为相对路径基准；返回端口与数据库绝对路径。"""
    config = configparser.ConfigParser(interpolation=None)
    if path.is_file():
        with path.open(encoding="utf-8-sig") as stream:
            config.read_file(stream)
    port = config.getint("server", "port", fallback=8080)
    if not 1 <= port <= 65535:
        raise ValueError("配置中的 port 必须为 1～65535")
    directory = Path(config.get("server", "data_directory", fallback="data"))
    if not directory.is_absolute():
        directory = base / directory
    return port, directory.resolve() / "dashboard.sqlite3"


def backup_database(source, directory):
    """在线备份数据库；source 为现有库、directory 为备份目录；返回备份文件路径，缺失源文件时报错。"""
    source = Path(source).resolve(strict=True)
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = directory / f"dashboard-before-upgrade-{stamp}.sqlite3"
    partial = destination.with_suffix(".sqlite3.partial")
    # 只读连接及 SQLite 备份接口确保 WAL 中已提交数据也被纳入备份。
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as original:
        with closing(sqlite3.connect(partial)) as copied:
            original.backup(copied)
            if copied.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError("数据库备份完整性检查失败")
    partial.replace(destination)
    return destination


def main():
    """执行离线入口；参数来自命令行；正常启动服务或完成备份后返回，无数据库时不生成业务数据。"""
    # 冻结程序显式使用 UTF-8 输出，保证服务日志中的中文在不同系统区域设置下可读。
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    base = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--config", type=Path, default=base / "BigScreen.ini")
    parser.add_argument("--backup-database", type=Path)
    parser.add_argument("--backup-directory", type=Path)
    options, remaining = parser.parse_known_args()
    if options.backup_database:
        if not options.backup_directory or remaining:
            parser.error("备份需要 --backup-database 和 --backup-directory，不能附加启动参数")
        print(backup_database(options.backup_database, options.backup_directory), flush=True)
        return
    if options.backup_directory:
        parser.error("--backup-directory 必须与 --backup-database 一起使用")
    port, database = read_configuration(options.config, base)
    os.environ["BIGSCREEN_DATABASE"] = str(database)
    # 显式命令行参数仍由原服务入口解析，并优先于外置配置。
    sys.argv = [sys.argv[0], "--port", str(port), *remaining]
    from server import main as serve
    serve()


if __name__ == "__main__":
    main()
