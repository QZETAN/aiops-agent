"""
MCP Server: Loki 日志查询工具。
让 AI Agent 能按服务名和关键字检索日志。
"""
import json
import logging
from datetime import datetime, timedelta, timezone

import httpx
from mcp.server.fastmcp import FastMCP

try:
    from .tool_config import LOKI_URL, REQUEST_TIMEOUT
except ImportError:
    from tool_config import LOKI_URL, REQUEST_TIMEOUT

logger = logging.getLogger("mcp-loki")

mcp = FastMCP("mcp-server-loki")


def _parse_log_entry(raw: str, ts_ns: str, fallback_service: str) -> dict:
    """解析单条 Loki 日志。优先 JSON（微服务格式），失败则纯文本。"""
    try:
        parsed = json.loads(raw)
        return {
            "timestamp": parsed.get("timestamp", ts_ns),
            "level": parsed.get("level", "UNKNOWN"),
            "message": str(parsed.get("message", raw))[:500],
            "trace_id": parsed.get("trace_id", ""),
            "span_id": parsed.get("span_id", ""),
            "service": parsed.get("service", fallback_service),
        }
    except json.JSONDecodeError:
        return {
            "timestamp": ts_ns, "level": "UNKNOWN", "message": raw[:500],
            "trace_id": "", "span_id": "", "service": fallback_service,
        }


@mcp.tool()
async def query_logs(service: str, keyword: str = "", minutes: int = 30, limit: int = 100) -> str:
    """
    按服务名和关键字查询 Loki 日志。

    Args:
        service: 服务名标签，如 "order-service"
        keyword: 内容过滤关键字，如 "ERROR"、"" 表示不过滤
        minutes: 最近 N 分钟，默认 30
        limit: 最多返回条数，默认 100

    Returns:
        JSON，含 logql（执行的查询）、total（匹配数）、logs 数组
    """
    logger.info(f"query_logs: service={service}, keyword={keyword!r}, minutes={minutes}")

    now = datetime.now(timezone.utc)
    start_ts = (now - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
    end_ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    logql = f'{{service="{service}"}}'
    if keyword:
        logql += f' |= "{keyword}"'

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.get(
                f"{LOKI_URL}/loki/api/v1/query_range",
                params={"query": logql, "start": start_ts, "end": end_ts, "limit": limit, "direction": "backward"},
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("status") != "success":
                return json.dumps({"error": f"Loki 返回错误: {data.get('error')}", "logql": logql}, ensure_ascii=False)

            logs = []
            for stream in data["data"].get("result", []):
                fallback_svc = stream.get("stream", {}).get("service", service)
                for entry in stream.get("values", []):
                    logs.append(_parse_log_entry(entry[1], entry[0], fallback_svc))

            return json.dumps({"total": len(logs), "logql": logql, "time_range": f"{start_ts} ~ {end_ts}", "logs": logs[:limit]}, ensure_ascii=False)

    except httpx.ConnectError:
        return json.dumps({"error": f"无法连接到 Loki ({LOKI_URL})", "logql": logql}, ensure_ascii=False)
    except httpx.TimeoutException:
        return json.dumps({"error": f"LogQL 查询超时（>{REQUEST_TIMEOUT}秒）", "logql": logql}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"查询异常: {type(e).__name__}: {str(e)}", "logql": logql}, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run(transport="stdio")
