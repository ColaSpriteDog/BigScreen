"""使用 Waitress 启动局域网编辑与展示服务。"""

import argparse
from contextlib import ExitStack
import socket
import sys

from waitress import serve

from backend import create_app
from backend.inventory_import import MAX_FILE_BYTES


def exclusive_listener(host, port):
    """创建 Windows 独占监听套接字；host、port 为监听地址和端口；返回已绑定套接字，失败时关闭并抛出异常。"""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    listener = socket.socket(family, socket.SOCK_STREAM)
    try:
        # Windows 的地址复用允许两个服务共用端口，必须在绑定前独占，避免新旧代码混用。
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind((host, port))
        return listener
    except OSError:
        listener.close()
        raise


def main():
    """解析启动选项并运行服务；命令行支持 host、port、database；无返回值。"""
    parser = argparse.ArgumentParser(description="智能制造大屏及编辑服务")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--database", help="指定 SQLite 文件路径")
    arguments = parser.parse_args()
    with ExitStack() as resources:
        endpoint = {"host": arguments.host, "port": arguments.port}
        if sys.platform == "win32":
            try:
                listener = resources.enter_context(exclusive_listener(arguments.host, arguments.port))
            except OSError as error:
                parser.exit(1, f"启动失败：无法独占监听 {arguments.host}:{arguments.port}，请先关闭已运行的服务。{error}\n")
            endpoint = {"sockets": [listener]}
        # 先占用端口再初始化数据库，重复启动时不触碰业务数据。
        config = {"DATABASE": arguments.database} if arguments.database else {}
        app = create_app(config)
        print(f"大屏：http://127.0.0.1:{arguments.port}/screen", flush=True)
        print(f"编辑：http://127.0.0.1:{arguments.port}/edit", flush=True)
        # 与 Flask 上传限额一致，避免 Waitress 将仅供解析的 Excel 请求体溢写到磁盘。
        upload_limit = MAX_FILE_BYTES + 1024 * 1024
        serve(app, **endpoint, threads=4,
              inbuf_overflow=upload_limit, max_request_body_size=upload_limit)


if __name__ == "__main__":
    main()
