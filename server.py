"""使用 Waitress 启动局域网编辑与展示服务。"""

import argparse

from waitress import serve

from backend import create_app


def main():
    """解析启动选项并运行服务；命令行支持 host、port、database；无返回值。"""
    parser = argparse.ArgumentParser(description="智能制造大屏及编辑服务")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--database", help="指定 SQLite 文件路径")
    arguments = parser.parse_args()
    config = {"DATABASE": arguments.database} if arguments.database else {}
    app = create_app(config)
    print(f"大屏：http://127.0.0.1:{arguments.port}/screen", flush=True)
    print(f"编辑：http://127.0.0.1:{arguments.port}/edit", flush=True)
    serve(app, host=arguments.host, port=arguments.port, threads=4)


if __name__ == "__main__":
    main()
