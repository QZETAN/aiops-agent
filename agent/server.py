"""
Agent HTTP 服务 —— 将 AIOPS Agent 暴露为常驻服务。

提供三个端点：
  GET  /health     健康检查（K8s liveness/readiness probe）
  POST /diagnose   接收告警，返回诊断报告（AlertManager Webhook 兼容）
  GET  /metrics    Agent 自身 Prometheus 指标

启动方式：
  aiops serve --port 8000
  python agent/server.py --port 8000

Phase 4 新增。
"""

import json
import logging
import os
import time
import uuid
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from langchain_core.messages import HumanMessage

from agent.agents.graph import build_graph
from agent.db import save_diagnosis

logger = logging.getLogger("aiops.server")

# ============================================================================
# FastAPI 应用
# ============================================================================

app = FastAPI(
    title="AIOps Agent",
    description="基于 LangGraph 多智能体的微服务故障自动诊断服务",
    version="0.2.0",
)

# ============================================================================
# 内存指标（Agent 自身的可观测性）
# ============================================================================


class AgentMetrics:
    """Agent 自身运行指标（内存计数器，无外部依赖）。"""

    def __init__(self):
        self.diagnosis_total = 0
        self.diagnosis_success = 0
        self.diagnosis_error = 0
        self.total_steps = 0
        self.total_seconds = 0.0

    def record(self, success: bool, steps: int, elapsed: float):
        self.diagnosis_total += 1
        if success:
            self.diagnosis_success += 1
        else:
            self.diagnosis_error += 1
        self.total_steps += steps
        self.total_seconds += elapsed

    def snapshot(self) -> dict:
        total = self.diagnosis_total
        return {
            "diagnosis_total": total,
            "diagnosis_success": self.diagnosis_success,
            "diagnosis_error": self.diagnosis_error,
            "success_rate": round(self.diagnosis_success / total, 4) if total > 0 else 0,
            "avg_steps": round(self.total_steps / total, 1) if total > 0 else 0,
            "avg_seconds": round(self.total_seconds / total, 1) if total > 0 else 0,
        }


_metrics = AgentMetrics()

# ============================================================================
# 诊断执行（与 app.py 共用核心逻辑）
# ============================================================================


def _run_diagnosis_sync(alert_text: str) -> dict:
    """同步执行一次诊断，返回报告 dict。"""
    diag_id = str(uuid.uuid4())[:8]
    start_time = time.time()

    initial_state = {
        "messages": [HumanMessage(content=f"[告警] {alert_text}")],
        "next_agent": "",
        "intermediate_steps": [],
        "evidence": {},
        "iteration_count": 0,
        "reflection_round": 0,
        "diagnosis_id": diag_id,
        "total_tokens": 0,
    }

    logger.info("[%s] 诊断启动: %s", diag_id, alert_text[:100])

    graph = build_graph()
    step_count = 0
    final_report = None

    try:
        for event in graph.stream(initial_state, {"recursion_limit": 50}):
            node_name = list(event.keys())[0]
            node_output = event[node_name]
            if node_name == "supervisor":
                step_count += 1
            elif node_name == "infer":
                messages = node_output.get("messages", [])
                for msg in messages:
                    if hasattr(msg, "content"):
                        try:
                            final_report = json.loads(msg.content)
                        except json.JSONDecodeError:
                            final_report = {"raw_output": msg.content[:2000]}

        elapsed = round(time.time() - start_time, 1)
        _metrics.record(success=True, steps=step_count, elapsed=elapsed)
        logger.info("[%s] 诊断完成: %d 轮, %.1f 秒", diag_id, step_count, elapsed)

        # 提取报告摘要
        report = final_report or {}
        root_causes = report.get("root_causes", [])
        top = root_causes[0] if root_causes else {}

        # 保存到数据库
        try:
            save_diagnosis({
                "diagnosis_id": diag_id,
                "alert_text": alert_text,
                "status": "completed",
                "services": [],
                "root_cause": top.get("description", ""),
                "confidence": top.get("confidence", 0.0),
                "steps": step_count,
                "elapsed_seconds": elapsed,
                "report": report,
            })
        except Exception as e:
            logger.warning("[%s] DB 保存失败: %s", diag_id, e)

        return {
            "diagnosis_id": diag_id,
            "status": "completed",
            "steps": step_count,
            "elapsed_seconds": elapsed,
            "report": report,
        }

    except Exception as exc:
        elapsed = round(time.time() - start_time, 1)
        _metrics.record(success=False, steps=step_count, elapsed=elapsed)
        logger.error("[%s] 诊断异常: %s", diag_id, exc, exc_info=True)

        try:
            save_diagnosis({
                "diagnosis_id": diag_id,
                "alert_text": alert_text,
                "status": "error",
                "services": [],
                "root_cause": "",
                "confidence": 0.0,
                "steps": step_count,
                "elapsed_seconds": elapsed,
                "report": {},
                "error": f"{type(exc).__name__}: {str(exc)}",
            })
        except Exception:
            pass

        return {
            "diagnosis_id": diag_id,
            "status": "error",
            "steps": step_count,
            "elapsed_seconds": elapsed,
            "error": f"{type(exc).__name__}: {str(exc)}",
        }


# ============================================================================
# 端点
# ============================================================================


@app.get("/health")
async def health():
    """
    健康检查端点。

    K8s 用这个端点做 liveness/readiness probe。
    返回 200 表示 Agent 进程存活且 Graph 可编译。
    """
    try:
        # 验证核心组件可用
        graph = build_graph()
        nodes = list(graph.get_graph().nodes.keys())
        return {
            "status": "healthy",
            "version": "0.2.0",
            "nodes": len(nodes),
            "checks": {
                "graph": "ok",
                "llm_api_key": bool(os.environ.get("DEEPSEEK_API_KEY")),
            },
        }
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Agent unhealthy: {e}")


@app.post("/diagnose")
async def diagnose(request: Request):
    """
    诊断端点 —— 接收告警并返回诊断报告。

    兼容两种请求格式：

    1. AlertManager Webhook 格式：
       {"alerts": [{"labels": {"alertname": "..."}, "annotations": {"description": "..."}}]}

    2. 简单文本格式：
       {"alert": "服务A 5xx 错误率升高"}
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求体必须是 JSON")

    # 格式 1：AlertManager Webhook
    if "alerts" in body:
        results = []
        for alert in body.get("alerts", []):
            labels = alert.get("labels", {})
            annotations = alert.get("annotations", {})
            alert_name = labels.get("alertname", "未知告警")
            description = annotations.get("description", annotations.get("summary", alert_name))
            alert_text = f"[{alert_name}] {description}"
            results.append(_run_diagnosis_sync(alert_text))
        return JSONResponse({"diagnosed": len(results), "results": results})

    # 格式 2：简单文本
    alert_text = body.get("alert", body.get("text", ""))
    if not alert_text:
        raise HTTPException(status_code=400, detail="缺少 alert 字段")

    result = _run_diagnosis_sync(alert_text)
    return JSONResponse(result)


@app.get("/metrics")
async def metrics():
    """
    Prometheus 指标端点。

    暴露 Agent 自身的运行指标，供 Prometheus 抓取。
    输出格式：Prometheus text format（application/text）。
    """
    snap = _metrics.snapshot()
    lines = [
        "# HELP aiops_diagnosis_total 诊断总次数",
        "# TYPE aiops_diagnosis_total counter",
        f"aiops_diagnosis_total {snap['diagnosis_total']}",
        "# HELP aiops_diagnosis_success 诊断成功次数",
        "# TYPE aiops_diagnosis_success counter",
        f"aiops_diagnosis_success {snap['diagnosis_success']}",
        "# HELP aiops_diagnosis_error 诊断失败次数",
        "# TYPE aiops_diagnosis_error counter",
        f"aiops_diagnosis_error {snap['diagnosis_error']}",
        "# HELP aiops_avg_steps 平均调度轮数",
        "# TYPE aiops_avg_steps gauge",
        f"aiops_avg_steps {snap['avg_steps']}",
        "# HELP aiops_avg_seconds 平均诊断耗时（秒）",
        "# TYPE aiops_avg_seconds gauge",
        f"aiops_avg_seconds {snap['avg_seconds']}",
        f"# HELP aiops_success_rate 诊断成功率",
        f"# TYPE aiops_success_rate gauge",
        f"aiops_success_rate {snap['success_rate']}",
    ]
    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain")


# ============================================================================
# CLI 入口
# ============================================================================


def run_server(port: int = 8000):
    """启动 Agent HTTP 服务。"""
    import uvicorn

    logger.info("AIOps Agent HTTP 服务启动在端口 %d", port)
    print(f"\n  AIOps Agent HTTP 服务 v0.2.0")
    print(f"  端口: {port}")
    print(f"  健康检查: http://localhost:{port}/health")
    print(f"  诊断接口: http://localhost:{port}/diagnose")
    print(f"  指标接口: http://localhost:{port}/metrics")
    print()

    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="AIOps Agent HTTP 服务")
    parser.add_argument("--port", "-p", type=int, default=8000)
    args = parser.parse_args()
    run_server(args.port)
