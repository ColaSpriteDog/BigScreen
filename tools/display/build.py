"""使用 Windows 自带编译器制作原生展示程序，不修改全局软件或项目 Python 依赖。"""

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from tools.packaging.build import CACHE, DOWNLOADS, download, run

SDK_NAME = "microsoft.web.webview2.1.0.2903.40.nupkg"
DOWNLOADS[SDK_NAME] = (
    "https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/1.0.2903.40/" + SDK_NAME,
    "ef128016dd1e51c59178c827ed5b8aa3322c57afa8675d930f8109505542ad74",
)
ASSEMBLIES = ("Microsoft.Web.WebView2.Core.dll", "Microsoft.Web.WebView2.WinForms.dll")


def build_client(destination, log):
    """编译专用展示程序；destination 为产物目录、log 为日志；返回 EXE 路径，不启动程序。"""
    compiler = Path(os.environ.get("SystemRoot", "C:/Windows")) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    if os.name != "nt" or not compiler.is_file():
        raise RuntimeError("请在安装了 .NET Framework 4.6.2+ 的 64 位 Windows 上编译展示程序")
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(download(SDK_NAME)) as archive:
        # 仅提取三份运行文件，不执行 NuGet 中的脚本，也不引入 Python GUI 依赖。
        for name in ASSEMBLIES:
            (destination / name).write_bytes(archive.read("lib/net462/" + name))
        (destination / "WebView2Loader.dll").write_bytes(archive.read("runtimes/win-x64/native/WebView2Loader.dll"))
        for notice in ("LICENSE.txt", "NOTICE.txt"):
            if notice in archive.namelist():
                (destination / ("WebView2-SDK-" + notice)).write_bytes(archive.read(notice))
    executable = destination / "BigScreen.Display.exe"
    sources = sorted(HERE.glob("*.cs"))
    run([compiler, "/nologo", "/utf8output", "/target:winexe", "/platform:x64", "/optimize+",
         "/win32manifest:" + str(HERE / "app.manifest"), "/out:" + str(executable),
         "/r:System.dll", "/r:System.Core.dll", "/r:System.Drawing.dll", "/r:System.Windows.Forms.dll",
         *["/r:" + str(destination / name) for name in ASSEMBLIES], *sources], Path(log), timeout=60)
    executable.with_suffix(".exe.config").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n<configuration><startup>'
        '<supportedRuntime version="v4.0" sku=".NETFramework,Version=v4.6.2" />'
        '</startup></configuration>\n', encoding="utf-8")
    return executable


def prepare_offline_runtime(destination, log):
    """准备微软官方离线运行库；destination 为交付目录、log 为日志；验证微软签名后复制，不执行安装。"""
    installer = CACHE / "tools" / "MicrosoftEdgeWebView2RuntimeInstallerX64.exe"
    installer.parent.mkdir(parents=True, exist_ok=True)
    if not installer.exists():
        print("下载 WebView2 x64 离线运行库（仅制作安装包时需要联网）……", flush=True)
        temporary = installer.with_suffix(".download")
        with urllib.request.urlopen("https://go.microsoft.com/fwlink/p/?LinkId=2124701", timeout=30) as source:
            with temporary.open("wb") as target:
                shutil.copyfileobj(source, target)
        temporary.replace(installer)
    # Evergreen 地址会更新版本，使用系统 Authenticode 验证微软发布者，而不是信任可变下载链接。
    # 单引号字面量转义路径，避免目录名中的空格、&、反引号或 $ 被 PowerShell 当作代码。
    literal = "'" + str(installer).replace("'", "''") + "'"
    command = (
        "$runtimePath = " + literal + "; $signature = Get-AuthenticodeSignature -LiteralPath $runtimePath; "
        "if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch "
        "'(^|,\\s*)O=Microsoft Corporation(,|$)') { throw 'WebView2 runtime signature verification failed' }"
    )
    run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command], Path(log), timeout=60)
    target = Path(destination) / installer.name
    shutil.copy2(installer, target)
    return target


def main():
    """执行轻量客户端编译；参数支持自选产物目录；无返回值，不构建或启动业务服务。"""
    parser = argparse.ArgumentParser(description="编译 Windows 专用大屏显示程序")
    parser.add_argument("--output", type=Path, default=ROOT / "output" / "display")
    options = parser.parse_args()
    options.output.mkdir(parents=True, exist_ok=True)
    executable = build_client(options.output, options.output / "build.log")
    print(f"展示程序：{executable}\n首次启动选择显示屏；按 Ctrl + Shift + Q 确认退出，无需密码。")


if __name__ == "__main__":
    main()
