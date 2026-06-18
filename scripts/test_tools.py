"""
MCP 工具连通性测试脚本
用法：python scripts/test_tools.py（需要先 pip install -e .）
"""
import asyncio
import sys

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from agent.tools.mcp_server_prometheus import query_promql
from agent.tools.mcp_server_loki import query_logs
from agent.tools.mcp_server_jaeger import find_traces


async def test_prometheus():
    print("=" * 50)
    print("[1/3] 测试 Prometheus...")
    result = await query_promql("up", "-5m", "now")
    print(result[:500])
    print()


async def test_loki():
    print("=" * 50)
    print("[2/3] 测试 Loki...")
    result = await query_logs("order-service", "", 30, 5)
    print(result[:500])
    print()


async def test_jaeger():
    print("=" * 50)
    print("[3/3] 测试 Jaeger...")
    result = await find_traces("gateway-service", 60, 5)
    print(result[:500])
    print()


async def main():
    print("请先确保：")
    print("  1. 虚拟机上的 5 个 Docker 容器都在运行")
    print("  2. 最近 5 分钟有过 curl 请求产生数据\n")

    await test_prometheus()
    await test_loki()
    await test_jaeger()

    print("=" * 50)
    print("测试完成。看到 JSON 数据 = 通了，看到 error = 有问题。")


if __name__ == "__main__":
    asyncio.run(main())
