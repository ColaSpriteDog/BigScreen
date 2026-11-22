"""运行后端、浏览器联合测试及 JavaScript 单元测试，保存可复查结果。"""

from pathlib import Path
import os
import shutil
import subprocess
import sys


def run_command(arguments, log_path, root):
    """执行一组测试；arguments 为命令、log_path 为输出位置、root 为工作目录；返回退出码。"""
    environment = dict(os.environ, PYTHONIOENCODING="utf-8")
    result = subprocess.run(arguments, cwd=root, env=environment, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, encoding="utf-8", errors="replace")
    log_path.write_text(result.stdout, encoding="utf-8")
    print(result.stdout, flush=True)
    return result.returncode


def main():
    """运行全部约定测试；无参数；返回零表示全部成功，非零表示测试或开发依赖有问题。"""
    root = Path(__file__).resolve().parent
    output = root / "output" / "tests"
    output.mkdir(parents=True, exist_ok=True)
    node = shutil.which("node")
    failures = run_command([sys.executable, "-m", "pytest", "-q", "--junitxml=" + str(output / "pytest.xml")], output / "python-tests.log", root)
    if not node:
        print("JavaScript 单元测试需要 Node.js 18 或更新版本；服务运行不需要 Node.js。")
        return 1
    for filename in ["model.test.cjs", "api.test.cjs"]:
        failures |= run_command([node, "tests/js/" + filename], output / (filename + ".log"), root)
    print("全部测试通过。" if failures == 0 else "存在失败，请查看 output/tests。", flush=True)
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
