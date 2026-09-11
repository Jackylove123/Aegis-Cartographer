import asyncio

from aegis_cartographer.mcp_server import serve


def main():
    """启动 Aegis Cartographer MCP 服务器"""
    asyncio.run(serve())


if __name__ == "__main__":
    main()
