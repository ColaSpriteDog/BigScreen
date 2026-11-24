"""验证离线部署的配置优先级及升级备份，不改动正式数据库。"""

import sqlite3

import pytest

from tools.packaging.build import validate_config
from tools.packaging.launcher import backup_database, read_configuration


def test_configuration_accepts_custom_unicode_directory(tmp_path):
    """验证自选中文数据目录；tmp_path 为临时目录；断言端口及路径与配置一致。"""
    path = tmp_path / "BigScreen.ini"
    directory = tmp_path / "客户 数据&备份"
    path.write_text(f"[server]\nport=8099\ndata_directory={directory}\n", encoding="utf-8-sig")
    port, database = read_configuration(path, tmp_path)
    assert port == 8099
    assert database == directory / "dashboard.sqlite3"


def test_configuration_relative_path_uses_program_directory(tmp_path, monkeypatch):
    """验证相对路径基准；参数为临时目录及环境工具；断言不受启动工作目录影响。"""
    program = tmp_path / "program"
    program.mkdir()
    monkeypatch.chdir(tmp_path)
    assert read_configuration(program / "missing.ini", program) == (8080, program / "data" / "dashboard.sqlite3")


@pytest.mark.parametrize("port", [0, 65536, "invalid"])
def test_configuration_rejects_invalid_port(tmp_path, port):
    """验证非法运行端口；参数为临时目录及端口；断言配置读取直接报错。"""
    path = tmp_path / "BigScreen.ini"
    path.write_text(f"[server]\nport={port}\n", encoding="utf-8")
    with pytest.raises(ValueError):
        read_configuration(path, tmp_path)


def test_backup_includes_committed_wal_data(tmp_path):
    """验证在线备份；tmp_path 为临时目录；断言未关闭源连接时 WAL 中的已提交记录完整备份。"""
    source = tmp_path / "客户数据库.sqlite3"
    with sqlite3.connect(source) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE probe(value TEXT)")
        connection.execute("INSERT INTO probe VALUES('客户记录')")
        connection.commit()
        copied = backup_database(source, tmp_path / "自选备份")
        with sqlite3.connect(copied) as backup:
            assert backup.execute("SELECT value FROM probe").fetchone()[0] == "客户记录"
        assert connection.execute("SELECT COUNT(*) FROM probe").fetchone()[0] == 1


def test_missing_database_does_not_create_empty_backup(tmp_path):
    """验证缺失数据库；tmp_path 为临时目录；断言不悄悄创建空库或备份目录。"""
    with pytest.raises(FileNotFoundError):
        backup_database(tmp_path / "missing.sqlite3", tmp_path / "backups")
    assert not (tmp_path / "backups").exists()


@pytest.mark.parametrize("config", [
    {"version": "1.0", "port": 8080}, {"version": "1.0.0&bad", "port": 8080},
    {"version": "99999.0.0", "port": 8080}, {"version": "1.0.0", "port": True},
    {"version": "1.0.0", "port": "8080"}, {"version": "1.0.0", "port": 65536},
])
def test_build_rejects_invalid_configuration(config):
    """验证构建配置；config 为非法配置；断言构建前拒绝异常版本及端口。"""
    with pytest.raises(ValueError):
        validate_config(config)


def test_build_accepts_numeric_version():
    """验证正常制作配置；无参数；断言返回版本与端口。"""
    assert validate_config({"version": "1.2.3", "port": 8088}) == ("1.2.3", 8088)
