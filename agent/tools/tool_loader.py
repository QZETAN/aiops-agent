"""
Tool Loader —— 将 MCP Server 的异步函数包装为 LangChain 同步工具。

为什么工具函数是 sync（同步）而不是 async？
  LangGraph 的 create_react_agent 内部使用 ToolNode，ToolNode 在独立线程池中
  同步调用 tool.invoke()。如果工具是 async def，会报
  "StructuredTool does not support sync invocation"。

解决方案：
  工具函数写成 def（同步），内部用 asyncio.run() 调用 MCP Server 的异步函数。
  asyncio.run() 在无事件循环的线程中会自动创建新的事件循环。
"""
import asyncio
import json

import requests
from langchain_core.tools import tool

from agent.config import PROMETHEUS_URL, JAEGER_URL

# ==================== 导入 MCP Server 函数 ====================

from agent.tools.mcp_server_prometheus import query_promql as _query_promql
from agent.tools.mcp_server_loki import query_logs as _query_logs
from agent.tools.mcp_server_jaeger import find_traces as _find_traces
from agent.tools.mcp_server_jaeger import get_trace_detail as _get_trace_detail
from agent.tools.mcp_server_git import get_recent_commits as _get_recent_commits


# ==================== 包装为 LangChain 同步工具 ====================

@tool
def query_promql(query: str, start: str = "-30m", end: str = "now", step: str = "15s") -> str:
    """
    执行 PromQL 查询，获取微服务监控指标的时序数据摘要。

    使用前先调 discover_services() 获取环境中的服务列表和确切的服务名。

    使用场景：
      - 检查服务健康状态：query="up"
      - 查 QPS：query="rate(http_server_duration_seconds_count{service='<服务名>'}[5m])"
      - 查 5xx 错误率：query="rate(http_server_duration_seconds_count{status=~'5..'}[5m])"
      - 查 CPU：query="process_cpu_usage{service='<服务名>'}"
      - 查 JVM 内存：query="jvm_memory_used_bytes{service='<服务名>'}"
      （将 <服务名> 替换为 discover_services() 返回的实际服务名）

    参数：
      query: PromQL 查询语句（必填）
      start: 起始时间偏移，如 "-30m"（30分钟前）、"-1h"（1小时前）
      end: 结束时间，默认 "now"（当前时间）
      step: 采样步长，默认 "15s"

    返回：
      JSON 字符串，包含 series_count（序列数）和每条序列的 avg/max/min/latest。
      如果 Prometheus 不可达或查询语法错误，返回 {"error": "..."}。
    """
    return asyncio.run(_query_promql(query=query, start=start, end=end, step=step))


@tool
def query_logs(service: str, keyword: str = "", minutes: int = 30, limit: int = 100) -> str:
    """
    按服务名和关键字查询 Loki 日志，返回最近的日志条目列表。

    使用前先调 discover_services() 获取环境中实际存在的服务名。

    参数：
      service: 服务名（必填）。从 discover_services() 返回的列表中选择
      keyword: 日志内容过滤关键字（可选），如 "ERROR"、"Exception"。为空则不过滤
      minutes: 查询最近 N 分钟的日志，默认 30
      limit: 最多返回的日志条数，默认 100

    返回：
      JSON 字符串，包含 total（匹配数）、logql（执行的查询语句）和 logs 数组。
      每条日志包含 timestamp、level、message、trace_id、span_id、service。
      如果 Loki 不可达或地址未配置，返回 {"error": "..."}。
    """
    return asyncio.run(_query_logs(service=service, keyword=keyword, minutes=minutes, limit=limit))


@tool
def find_traces(service: str, minutes: int = 30, limit: int = 20, tag: str = "") -> str:
    """
    搜索 Jaeger 调用链摘要列表，快速发现错误或慢请求 Trace。

    使用前先调 discover_services() 获取环境中实际存在的服务名。

    参数：
      service: 服务名（必填）。从 discover_services() 返回的列表中选择
      minutes: 查询最近 N 分钟的 Trace，默认 30
      limit: 最多返回的 Trace 数量，默认 20
      tag: 附加过滤标签，如 "error=true"。空字符串表示不过滤

    返回：
      JSON 字符串，包含 total（Trace 数量）和 traces 数组。
      每条 Trace 包含 trace_id、spans_count、duration_ms、services、error_spans、has_error。
      如果 Jaeger 不可达或地址未配置，返回 {"error": "..."}。
    """
    return asyncio.run(_find_traces(service=service, minutes=minutes, limit=limit, tag=tag))


@tool
def get_trace_detail(trace_id: str) -> str:
    """
    根据 TraceID 获取完整调用链的 Span 树详情。

    使用场景：
      - find_traces 发现可疑 Trace → 用此工具下钻，看具体哪个 Span 出错/慢
      - 确认故障发生在调用链的哪个环节（gateway→order→user 哪一环断了）

    参数：
      trace_id: Jaeger Trace ID（必填），从 find_traces 的返回结果中获取

    返回：
      JSON 字符串，包含 trace_id、total_spans（Span 总数）和 spans 数组。
      每个 Span 包含 span_id、operation_name、service_name、duration_ms、references、tags。
      如果 Trace 不存在或 Jaeger 不可达，返回 {"error": "..."}。
    """
    return asyncio.run(_get_trace_detail(trace_id=trace_id))


@tool
def get_recent_commits(repo_path: str, hours: int = 24, limit: int = 20) -> str:
    """
    查询 Git 仓库最近 N 小时内的提交记录。

    使用场景：
      - 故障发生时间附近是否有新代码上线？
      - 关联"刚合并的 PR"和"刚出现的故障"

    参数：
      repo_path: Git 仓库的本地路径（必填），如 "d:/py_project/AIOPS/microservices"
      hours: 查询最近 N 小时内的提交，默认 24
      limit: 最多返回的提交数量，默认 20

    返回：
      JSON 字符串，包含 total（提交数）、since（起始时间）和 commits 数组。
      每条 commit 包含 hash、author、timestamp、message。
      如果 Git 不可用或路径不是仓库，返回 {"error": "..."}。
    """
    return asyncio.run(_get_recent_commits(repo_path=repo_path, hours=hours, limit=limit))


# ==================== 6. discover_services ====================


@tool
def discover_services() -> str:
    """
    自动发现当前环境中所有被监控的微服务。

    双数据源策略：
      1. 优先查 Prometheus up 指标（适合 Prometheus 直接 scrape 的场景）
      2. 如果 Prometheus 只发现基础设施（prometheus/jaeger 自身），
         自动回退到 Jaeger API（适合通过 OTel Collector 上报的场景）

    返回的服务名可以直接用于：
      - query_promql 的标签过滤：service='<返回的服务名>'
      - query_logs 的 service 参数
      - find_traces 的 service 参数

    返回：
      JSON，包含 total_services 和 services 字典
    """
    services: dict[str, dict] = {}
    data_sources: list[str] = []

    # ── 数据源 1：Prometheus up 指标 ──────────────────────────
    if PROMETHEUS_URL:
        try:
            resp = requests.get(
                f"{PROMETHEUS_URL}/api/v1/query",
                params={"query": "up"},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("status") == "success":
                for item in data["data"]["result"]:
                    metric = item["metric"]
                    svc = metric.get("service") or metric.get("job", "")
                    # 过滤掉基础设施（只保留微服务）
                    if svc and svc not in ("prometheus", "jaeger-all-in-one", "jaeger", "loki", "grafana", "otel-collector"):
                        if svc not in services:
                            services[svc] = {"instances": 0, "status": "unknown"}
                        services[svc]["instances"] += 1
                        services[svc]["status"] = "up" if item["value"][1] == "1" else "down"
                if services:
                    data_sources.append("prometheus")
        except Exception:
            pass  # Prometheus 不可达，静默回退

    # ── 数据源 2：Jaeger 服务列表（回退方案）─────────────────
    if not services and JAEGER_URL:
        try:
            resp = requests.get(f"{JAEGER_URL}/api/services", timeout=10)
            resp.raise_for_status()
            data = resp.json()
            jaeger_services = data.get("data", []) or []
            for svc in jaeger_services:
                # 过滤掉 Jaeger 自身
                if svc and svc not in ("jaeger-all-in-one", "jaeger-query", "jaeger-collector"):
                    if svc not in services:
                        services[svc] = {"instances": 1, "status": "up"}
            if jaeger_services:
                data_sources.append("jaeger")
        except Exception:
            pass  # Jaeger 不可达，静默回退

    # ── 汇总 ──────────────────────────────────────────────────
    if not services:
        return json.dumps({
            "error": "未发现任何微服务",
            "help": "请确认：1) 微服务已接入 Prometheus 或 Jaeger  2) 最近有请求产生过数据",
            "prometheus_url": PROMETHEUS_URL or "未配置",
            "jaeger_url": JAEGER_URL or "未配置",
        }, ensure_ascii=False)

    return json.dumps({
        "total_services": len(services),
        "services": services,
        "data_sources": data_sources,
        "hint": "请用以上服务名作为 query_promql / query_logs / find_traces 的 service 参数",
    }, ensure_ascii=False, indent=2)


# ==================== 工具列表 ====================

ALL_TOOLS: list = [
    discover_services,
    query_promql,
    query_logs,
    find_traces,
    get_trace_detail,
    get_recent_commits,
]
