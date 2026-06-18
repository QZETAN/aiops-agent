"""
MCP Server: Prometheus 指标查询工具。
让 AI Agent 能查 Prometheus 指标数据。
"""
import json
import logging
from datetime import datetime, timedelta, timezone

import httpx
from mcp.server.fastmcp import FastMCP

try:
    from .tool_config import PROMETHEUS_URL, REQUEST_TIMEOUT
except ImportError:
    from tool_config import PROMETHEUS_URL, REQUEST_TIMEOUT

logger = logging.getLogger("mcp-prometheus")

mcp = FastMCP("mcp-server-prometheus")


def _parse_relative_time(time_str: str) -> str:
    """将 "-30m" / "-2h" / "now" 转为 UTC ISO 8601 时间戳"""
    if time_str == "now":
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if time_str.startswith("-"):
        num = int(time_str[1:-1])
        unit = time_str[-1]
        delta = {"m": timedelta(minutes=num), "h": timedelta(hours=num), "d": timedelta(days=num)}.get(unit)
        if delta:
            return (datetime.now(timezone.utc) - delta).strftime("%Y-%m-%dT%H:%M:%SZ")
    return time_str


def _summarize_series(series_list: list) -> list[dict]:
    """将 Prometheus 原始数据点压缩为每条序列的 avg/max/min/latest"""
    summaries = []
    for series in series_list:
        metric = series.get("metric", {})
        values = series.get("values", [])
        numeric_vals = [float(v[1]) for v in values if v[1] not in ("NaN", "+Inf", "-Inf")] if values else []

        summary = {"metric": metric, "total_points": len(values)}
        if numeric_vals:
            summary["avg"] = round(sum(numeric_vals) / len(numeric_vals), 4)
            summary["max"] = round(max(numeric_vals), 4)
            summary["min"] = round(min(numeric_vals), 4)
            summary["latest"] = round(numeric_vals[-1], 4)
            summary["first_ts"] = values[0][0]
            summary["last_ts"] = values[-1][0] if len(values) > 1 else values[0][0]
        else:
            summary.update({"avg": None, "max": None, "min": None, "latest": None, "first_ts": None, "last_ts": None})
        summaries.append(summary)
    return summaries


@mcp.tool()
async def query_promql(query: str, start: str = "-30m", end: str = "now", step: str = "15s") -> str:
    """
    执行 PromQL 查询，返回指标时序数据摘要。

    Args:
        query: PromQL 语句
        start: 起始时间，支持 "-30m"、"-1h"、ISO 8601
        end: 结束时间，默认 "now"
        step: 采样步长，默认 "15s"

    Returns:
        JSON 字符串，含 series_count 和每条序列的 avg/max/min/latest
    """
    if not PROMETHEUS_URL:
        return json.dumps({
            "error": "Prometheus 地址未配置",
            "help": "请在 .env 文件中设置 PROMETHEUS_URL=http://你的地址:9090",
        }, ensure_ascii=False)

    logger.info("query_promql: query=%s, start=%s", query[:80], start)
    start_ts = _parse_relative_time(start)
    end_ts = _parse_relative_time(end)

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.get(
                f"{PROMETHEUS_URL}/api/v1/query_range",
                params={"query": query, "start": start_ts, "end": end_ts, "step": step},
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("status") != "success":
                return json.dumps({"error": f"Prometheus 返回错误: {data.get('error')}", "query": query}, ensure_ascii=False)

            result = data["data"]
            summaries = _summarize_series(result.get("result", []))
            return json.dumps({
                "result_type": result["resultType"],
                "series_count": len(summaries),
                "series": summaries,
                "query": query, "start": start_ts, "end": end_ts,
            }, ensure_ascii=False)

    except httpx.ConnectError:
        return json.dumps({"error": f"无法连接到 Prometheus ({PROMETHEUS_URL})", "query": query}, ensure_ascii=False)
    except httpx.TimeoutException:
        return json.dumps({"error": f"PromQL 查询超时（>{REQUEST_TIMEOUT}秒）", "query": query}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"查询异常: {type(e).__name__}: {str(e)}", "query": query}, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run(transport="stdio")
