"""
MCP Server: Jaeger 调用链查询工具。
让 AI Agent 能搜索 Trace 并下钻 Span 详情。
"""
import json
import logging
from datetime import datetime, timedelta, timezone

import httpx
from mcp.server.fastmcp import FastMCP

try:
    from .tool_config import JAEGER_URL, REQUEST_TIMEOUT
except ImportError:
    from tool_config import JAEGER_URL, REQUEST_TIMEOUT

logger = logging.getLogger("mcp-jaeger")

mcp = FastMCP("mcp-server-jaeger")


def _build_error_spans(spans: list, processes: dict) -> list[dict]:
    """从 span 列表中提取含 error=true 或 HTTP 5xx 的 span"""
    error_spans = []
    for span in spans:
        svc = processes.get(span.get("processID", ""), {}).get("serviceName", "unknown")
        for tag in span.get("tags", []):
            if tag.get("key") == "error" and tag.get("value") is True:
                error_spans.append({"operation": span.get("operationName", ""), "service": svc, "reason": "error=true"})
            if tag.get("key") == "http.status_code":
                try:
                    if int(tag["value"]) >= 500:
                        error_spans.append({"operation": span.get("operationName", ""), "service": svc, "reason": f"http_status={tag['value']}"})
                except (ValueError, TypeError):
                    pass
    return error_spans


@mcp.tool()
async def find_traces(service: str, minutes: int = 30, limit: int = 20, tag: str = "") -> str:
    """
    按服务名和时间范围搜索 Jaeger 调用链摘要。

    Args:
        service: 服务名
        minutes: 最近 N 分钟
        limit: 返回条数
        tag: 附加过滤，如 "error=true"

    Returns:
        traces 数组，每条含 trace_id/spans_count/duration_ms/services/error_spans
    """
    logger.info(f"find_traces: service={service}, minutes={minutes}")
    now = datetime.now(timezone.utc)
    start_us = str(int((now - timedelta(minutes=minutes)).timestamp() * 1_000_000))
    end_us = str(int(now.timestamp() * 1_000_000))

    params = {"service": service, "start": start_us, "end": end_us, "limit": limit, "lookback": f"{minutes}m"}
    if tag:
        key, sep, val = tag.partition("=")
        if sep:
            params["tags"] = json.dumps({key: val})

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.get(f"{JAEGER_URL}/api/traces", params=params)
            resp.raise_for_status()
            data = resp.json()
            traces = data.get("data", data)
            if not isinstance(traces, list):
                traces = []

            summaries = []
            for trace in traces[:limit]:
                spans = trace.get("spans", [])
                processes = trace.get("processes", {})
                durations_ms = [s.get("duration", 0) / 1_000 for s in spans]
                services_list = sorted({p.get("serviceName", "") for p in processes.values() if p.get("serviceName")})

                summaries.append({
                    "trace_id": trace.get("traceID", ""),
                    "spans_count": len(spans),
                    "duration_ms": round(max(durations_ms) if durations_ms else 0, 2),
                    "services": services_list,
                    "error_spans": _build_error_spans(spans, processes),
                    "has_error": any(t.get("key") == "error" and t.get("value") is True for s in spans for t in s.get("tags", [])),
                })

            return json.dumps({"total": len(summaries), "service": service, "time_range": f"最近 {minutes} 分钟", "traces": summaries}, ensure_ascii=False)

    except httpx.ConnectError:
        return json.dumps({"error": f"无法连接到 Jaeger ({JAEGER_URL})"}, ensure_ascii=False)
    except httpx.TimeoutException:
        return json.dumps({"error": f"Jaeger 查询超时（>{REQUEST_TIMEOUT}秒）"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"查询异常: {type(e).__name__}: {str(e)}"}, ensure_ascii=False)


@mcp.tool()
async def get_trace_detail(trace_id: str) -> str:
    """
    根据 TraceID 获取完整 Span 树。

    Args:
        trace_id: Jaeger Trace ID（从 find_traces 获得）

    Returns:
        每个 Span 含 span_id/operation_name/service_name/duration_ms/references/tags
    """
    logger.info(f"get_trace_detail: trace_id={trace_id}")
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.get(f"{JAEGER_URL}/api/traces/{trace_id}")
            resp.raise_for_status()
            data = resp.json()
            traces = data.get("data", [data])
            trace = traces[0] if (isinstance(traces, list) and traces) else (traces if not isinstance(traces, list) else None)

            if not trace:
                return json.dumps({"error": f"未找到 Trace: {trace_id}"}, ensure_ascii=False)

            processes = trace.get("processes", {})
            spans_out = []
            for span in trace.get("spans", []):
                svc = processes.get(span.get("processID", ""), {}).get("serviceName", "unknown")
                spans_out.append({
                    "span_id": span.get("spanID", ""),
                    "operation_name": span.get("operationName", ""),
                    "service_name": svc,
                    "duration_ms": round(span.get("duration", 0) / 1_000, 2),
                    "start_time": span.get("startTime", 0),
                    "references": [{"type": r.get("refType", ""), "span_id": r.get("spanID", "")} for r in span.get("references", [])],
                    "tags": {t["key"]: str(t.get("value", "")) for t in span.get("tags", [])},
                })
            return json.dumps({"trace_id": trace_id, "total_spans": len(spans_out), "spans": spans_out}, ensure_ascii=False)

    except httpx.ConnectError:
        return json.dumps({"error": f"无法连接到 Jaeger ({JAEGER_URL})", "trace_id": trace_id}, ensure_ascii=False)
    except httpx.TimeoutException:
        return json.dumps({"error": f"Jaeger 查询超时", "trace_id": trace_id}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"查询异常: {type(e).__name__}: {str(e)}", "trace_id": trace_id}, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run(transport="stdio")
