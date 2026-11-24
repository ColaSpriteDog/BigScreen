"""一键制作 Windows 离线安装包；工具链仅缓存在 output，不全局安装软件。"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import struct
import subprocess
import sys
import urllib.request
import venv
import zipfile

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
CACHE = ROOT / "output" / "packaging"
# 下载固定版本并校验 SHA-256，避免静默使用变动的远程工具。
DOWNLOADS = {
    "tools.innosetup.6.4.3.nupkg": (
        "https://api.nuget.org/v3-flatcontainer/tools.innosetup/6.4.3/tools.innosetup.6.4.3.nupkg",
        "9d67a2b1155ca5bc825475ceb907aa357c5feb42e474cfd5347eddeef5ad92a5"),
    "winsw.2.11.0.nupkg": (
        "https://api.nuget.org/v3-flatcontainer/winsw/2.11.0/winsw.2.11.0.nupkg",
        "b73c6c10ee634a782cb74024a4d802a6214647e44c432a2cc34b360c64f84766"),
    "ChineseSimplified.isl": (
        "https://raw.githubusercontent.com/jrsoftware/issrc/is-6_4_3/Files/Languages/Unofficial/ChineseSimplified.isl",
        "dceca9fea16ea057a64012ac8744a1eca7dc6ce3187d0c8991accbb539f2d4a0"),
    "WinSW-LICENSE.txt": (
        "https://raw.githubusercontent.com/winsw/winsw/v2.11.0/LICENSE.txt",
        "1cdf703c10a70e5973bf3acf2a5eeabe7746237155b92db2034aeae26fdf7802"),
}


def sha256(path):
    """计算文件校验值；path 为文件路径；返回十六进制 SHA-256。"""
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(command, log, timeout=600):
    """运行构建命令；command 为参数列表、log 为日志路径、timeout 为秒数；失败时附日志末尾并抛错。"""
    with log.open("ab") as stream:
        stream.write(("\n> " + subprocess.list2cmdline([str(x) for x in command]) + "\n").encode("utf-8"))
        stream.flush()
        result = subprocess.run([str(x) for x in command], cwd=ROOT, stdout=stream,
                                stderr=subprocess.STDOUT, timeout=timeout, check=False)
    if result.returncode:
        tail = "\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-30:])
        raise RuntimeError(f"命令执行失败（退出码 {result.returncode}）：\n{tail}\n完整日志：{log}")


def download(name):
    """获取固定工具；name 为白名单文件名；返回已验证的缓存路径，缓存存在时无需联网。"""
    url, expected = DOWNLOADS[name]
    target = CACHE / "tools" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if sha256(target) != expected:
            raise RuntimeError(f"工具缓存校验失败，请检查并移走该文件后重试：{target}")
        return target
    print(f"下载打包工具：{name}", flush=True)
    partial = target.with_suffix(target.suffix + ".download")
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "BigScreen-Packager/1.0"})
            with urllib.request.urlopen(request, timeout=30) as source, partial.open("wb") as output:
                shutil.copyfileobj(source, output)
            if sha256(partial) != expected:
                raise RuntimeError(f"下载校验失败：{name}")
            partial.replace(target)
            return target
        except (OSError, RuntimeError) as error:
            if attempt == 2:
                raise RuntimeError(f"无法获取 {name}；首次制作需要联网：{error}") from error


def prepare_environment(log):
    """准备独立环境；log 为安装日志；返回打包环境 Python，不修改项目虚拟环境或全局依赖。"""
    directory = CACHE / "venv"
    python = directory / "Scripts" / "python.exe"
    if not python.is_file():
        print("创建独立打包环境……", flush=True)
        venv.EnvBuilder(with_pip=True).create(directory)
    requirements = [ROOT / "requirements.txt", HERE / "requirements-build.txt"]
    fingerprint = hashlib.sha256(b"".join(p.read_bytes() for p in requirements) + sys.version.encode()).hexdigest()
    marker = directory / "requirements.sha256"
    if not marker.exists() or marker.read_text(encoding="ascii") != fingerprint:
        print("安装打包依赖（首次制作需要联网，详情见日志）……", flush=True)
        run([python, "-m", "pip", "install", "--disable-pip-version-check", "--retries", "2",
             "--timeout", "30", "-r", requirements[0], "-r", requirements[1]], log)
        marker.write_text(fingerprint, encoding="ascii")
    return python


def validate_config(config):
    """校验制作配置；config 为配置字典；返回版本和端口，非法配置抛出可读错误。"""
    version = config.get("version", "")
    if not isinstance(version, str) or not re.fullmatch(r"\d{1,5}(?:\.\d{1,5}){2,3}", version):
        raise ValueError("version 必须是三或四段数字，例如 1.0.0")
    if any(int(part) > 65535 for part in version.split(".")):
        raise ValueError("版本号每段不能超过 65535")
    port = config.get("port")
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("port 必须是 1～65535 的整数")
    return version, port


def collect_licenses(python, package, log):
    """收集运行依赖许可；python 为打包解释器、package 为产物目录、log 为日志；无返回值。"""
    code = """
import importlib.metadata as metadata
from pathlib import Path
import sys
target = Path(sys.argv[1]); target.mkdir(exist_ok=True)
for name in ['Flask','Werkzeug','Jinja2','MarkupSafe','itsdangerous','click','blinker',
             'colorama','waitress','tzdata','xlrd','openpyxl','et_xmlfile','PyInstaller']:
    try:
        dist = metadata.distribution(name)
    except metadata.PackageNotFoundError:
        continue
    for file in dist.files or []:
        if any(word in file.name.lower() for word in ('license', 'licence', 'copying', 'notice')):
            source = dist.locate_file(file)
            if source.is_file():
                (target / (name + '-' + file.name)).write_bytes(source.read_bytes())
(target / 'Python-LICENSE.txt').write_bytes((Path(sys.base_prefix) / 'LICENSE.txt').read_bytes())
"""
    run([python, "-c", code, package / "THIRD_PARTY_LICENSES"], log)
    shutil.copy2(download("WinSW-LICENSE.txt"), package / "THIRD_PARTY_LICENSES" / "WinSW-LICENSE.txt")


def main():
    """执行一键制作；命令行可覆盖版本；成功时输出安装包、便携包和校验信息，失败返回非零码。"""
    parser = argparse.ArgumentParser(description="制作 BigScreen Windows 离线安装包")
    parser.add_argument("--version", help="覆盖 config.json 中的版本号，例如 1.0.1")
    options = parser.parse_args()
    if (os.name != "nt" or struct.calcsize("P") != 8 or sys.version_info < (3, 10)
            or platform.machine().lower() not in {"amd64", "x86_64"}):
        raise RuntimeError("请在 Windows 开发电脑上使用 Python 3.10+ 的 64 位版本制作")
    config = json.loads((HERE / "config.json").read_text(encoding="utf-8-sig"))
    if options.version:
        config["version"] = options.version
    version, port = validate_config(config)
    CACHE.mkdir(parents=True, exist_ok=True)
    # 防止两个制作窗口同时修改共享的工具环境；锁随进程退出自动释放。
    import msvcrt
    with (CACHE / "build.lock").open("a+b") as lock:
        lock.seek(0)
        if lock.read(1) == b"":
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise RuntimeError("已有制作任务在运行，请等待该窗口完成") from error
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        work = CACHE / f"build-{stamp}"
        work.mkdir()
        release = ROOT / "output" / "releases" / f"{version}-{stamp}"
        release.mkdir(parents=True)
        log = work / "build.log"
        print(f"制作版本：{version}；构建日志：{log}", flush=True)
        python = prepare_environment(log)
        print("检查并准备安装程序编译器……", flush=True)
        compiler = CACHE / "inno-6.4.3"
        archive = download("tools.innosetup.6.4.3.nupkg")
        # 编译器按固定清单从已校验的包中读取，不运行工具的安装或卸载程序。
        with zipfile.ZipFile(archive) as source:
            for name in source.namelist():
                if name.startswith("tools/") and not name.endswith("/"):
                    relative = Path(name).relative_to("tools")
                    target = compiler / relative
                    if not target.resolve().is_relative_to(compiler.resolve()):
                        raise RuntimeError("编译器压缩包包含越界路径")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(source.read(name))
        language = download("ChineseSimplified.isl")
        print("打包程序、Python 和本地资源……", flush=True)
        run([python, "-m", "PyInstaller", "--noconfirm", "--distpath", work / "dist",
             "--workpath", work / "freeze", HERE / "BigScreen.spec"], log)
        package = work / "dist" / "BigScreen"
        with zipfile.ZipFile(download("winsw.2.11.0.nupkg")) as source:
            (package / "BigScreen.Service.exe").write_bytes(source.read("lib/net461-full/WinSW.NET461.exe"))
        shutil.copy2(HERE / "BigScreen.Service.xml", package)
        (package / "BigScreen.ini").write_text(
            f"; 可修改端口和数据目录；相对路径以程序所在目录为基准。\n[server]\nport={port}\ndata_directory=data\n",
            encoding="utf-8-sig")
        (package / "启动.cmd").write_bytes(
            '@echo off\r\nchcp 65001 >nul\r\npushd "%~dp0"\r\n"%~dp0BigScreen.exe" %*\r\npopd\r\npause\r\n'.encode("utf-8"))
        shutil.copy2(HERE / "客户使用说明.txt", package / "使用说明.txt")
        collect_licenses(python, package, log)
        print("验证离线程序：自选数据路径、登录读写、静态资源、重启和备份……", flush=True)
        smoke_result = work / "smoke-result.json"
        run([python, HERE / "smoke.py", package, "--output", smoke_result], log, timeout=120)
        print("生成中文安装包……", flush=True)
        run([compiler / "ISCC.exe", "/Qp", f"/DAppVersion={version}", f"/DDefaultPort={port}",
             f"/DSourceDir={package}", f"/DOutputDir={release}", f"/DLanguageFile={language}",
             HERE / "installer.iss"], log)
        print("生成便携包与校验信息……", flush=True)
        portable = release / f"BigScreen-{version}-win-x64-portable.zip"
        with zipfile.ZipFile(portable, "w", compression=zipfile.ZIP_DEFLATED) as output:
            for path in sorted(package.rglob("*")):
                if path.is_file():
                    output.write(path, Path("BigScreen") / path.relative_to(package))
        artifacts = [release / f"BigScreen-{version}-win-x64-setup.exe", portable]
        manifest = {
            "version": version, "built_at": datetime.now(timezone.utc).isoformat(),
            "python": platform.python_version(), "default_port": port,
            "data_directory": "由安装向导选择；便携包默认使用程序旁的 data 目录",
            "verification": json.loads(smoke_result.read_text(encoding="utf-8")),
            "installer_test": "已编译；未在本机注册服务或修改防火墙，需在隔离 Windows 验证完整安装",
            "artifacts": [{"file": p.name, "bytes": p.stat().st_size, "sha256": sha256(p)} for p in artifacts],
        }
        (release / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        (release / "SHA256SUMS.txt").write_text(
            "".join(f"{item['sha256']}  {item['file']}\n" for item in manifest["artifacts"]), encoding="ascii")
        shutil.copy2(HERE / "客户使用说明.txt", release)
        print(f"\n制作成功！\n安装包：{artifacts[0]}\n便携包：{portable}\n交付目录：{release}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (Exception, KeyboardInterrupt) as error:
        print(f"\n制作失败：{error}", file=sys.stderr, flush=True)
        sys.exit(1)
