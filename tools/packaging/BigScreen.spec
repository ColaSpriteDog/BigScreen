# 将服务及必需资源加入白名单，避免携带开发数据库、测试和设计文件。
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files

root = Path(SPECPATH).parents[1]
datas = [(str(root / name), name) for name in ("templates", "static", "assets")]
datas += [(str(root / "backend" / "schema.sql"), "backend")]
datas += collect_data_files("tzdata")
analysis = Analysis(
    [str(root / "tools" / "packaging" / "launcher.py")],
    pathex=[str(root)], datas=datas, hiddenimports=["xlrd", "openpyxl"],
    excludes=["pytest", "playwright", "tkinter", "numpy", "pandas"],
)
archive = PYZ(analysis.pure)
executable = EXE(
    archive, analysis.scripts, [], exclude_binaries=True, name="BigScreen",
    console=True, upx=False,
)
collection = COLLECT(executable, analysis.binaries, analysis.datas, name="BigScreen", upx=False)
