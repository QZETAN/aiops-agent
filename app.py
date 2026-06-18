"""
AIOps 智能运维 Agent —— CLI 入口。

用法：
  aiops diagnose --alert "服务A 的 5xx 错误率突然升高"   单次诊断
  aiops serve --port 8000                                HTTP 服务模式
  aiops stats --days 30                                  最近 30 天统计
  aiops history --limit 20                               最近诊断记录
  aiops                                                   交互式输入

Phase 4 新增：
  - 每次诊断自动保存到 SQLite（data/diagnoses.db）
  - stats / history 子命令
"""

import argparse
import json
import logging
import sys
import time
import uuid
from datetime import datetime

from langchain_core.messages import HumanMessage
from agent.agents.graph import build_graph
from agent.db import save_diagnosis, get_stats, get_recent

logger = logging.getLogger("aiops.app")


def run_diagnosis(alert_text: str):
    """运行一次完整的故障诊断，流式输出每一步，并自动保存到数据库。"""
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

    print(f"\n{'='*60}")
    print(f"  AIOps Agent 诊断启动")
    print(f"  ID: {diag_id}")
    print(f"  告警: {alert_text}")
    print(f"{'='*60}\n")

    graph = build_graph()
    step_count = 0
    partial_evidence: list[str] = []
    final_report: dict = {}
    services_found: list[str] = []

    try:
        for event in graph.stream(initial_state, {"recursion_limit": 50}):
            node_name = list(event.keys())[0]
            node_output = event[node_name]
            messages = node_output.get("messages", [])

            if node_name == "supervisor":
                step_count += 1
                for msg in messages:
                    if hasattr(msg, "content") and "下一步" in msg.content:
                        print(f"  [{step_count}] {msg.content}")
                        break

            elif node_name in ("metrics_expert", "logs_expert", "traces_expert", "code_expert"):
                ai_msgs = [
                    m for m in messages
                    if hasattr(m, "content") and hasattr(m, "type") and m.type == "ai"
                ]
                if ai_msgs:
                    content = ai_msgs[-1].content
                    if isinstance(content, str):
                        partial_evidence.append(f"[{node_name}] {content[:200]}")
                        # 尝试提取服务列表
                        try:
                            data = json.loads(content)
                            if "services_found" in data:
                                services_found = data["services_found"]
                        except json.JSONDecodeError:
                            pass
                        if len(content) > 400:
                            content = content[-400:]
                        print(f"     L-- {node_name}: {content.strip()[:300]}")

            elif node_name == "infer":
                print(f"\n{'='*60}")
                print(f"  根因分析报告")
                print(f"{'='*60}\n")
                for msg in messages:
                    if hasattr(msg, "content"):
                        try:
                            final_report = json.loads(msg.content)
                            print(json.dumps(final_report, ensure_ascii=False, indent=2))
                        except json.JSONDecodeError:
                            final_report = {"raw_output": msg.content[:2000]}
                            print(msg.content[:2000])

        elapsed = round(time.time() - start_time, 1)
        root_causes = final_report.get("root_causes", [])
        top_cause = root_causes[0] if root_causes else {}
        confidence = top_cause.get("confidence", 0.0)
        root_cause_text = top_cause.get("description", "")

        logger.info("[%s] 诊断完成: %d 轮, %.1f 秒", diag_id, step_count, elapsed)
        print(f"\n{'='*60}")
        print(f"  诊断完成（共 {step_count} 轮调度，耗时 {elapsed} 秒）")
        print(f"{'='*60}\n")

        # ── 保存到数据库 ──────────────────────────────────────────
        try:
            save_diagnosis({
                "diagnosis_id": diag_id,
                "alert_text": alert_text,
                "status": "completed",
                "services": services_found,
                "root_cause": root_cause_text,
                "confidence": confidence,
                "steps": step_count,
                "elapsed_seconds": elapsed,
                "report": final_report,
            })
            print(f"  [已保存到数据库: {diag_id}]\n")
        except Exception as e:
            logger.warning("[%s] 数据库写入失败: %s", diag_id, e)

        return final_report

    except KeyboardInterrupt:
        elapsed = round(time.time() - start_time, 1)
        print("\n[WARN] 用户中断诊断")
        logger.warning("[%s] 用户中断", diag_id)
        _print_partial_result(diag_id, step_count, elapsed, partial_evidence, interrupted=True)
        _save_error_record(diag_id, alert_text, "aborted", step_count, elapsed, "用户中断")

    except Exception as exc:
        elapsed = round(time.time() - start_time, 1)
        print(f"\n[ERROR] 诊断过程异常: {type(exc).__name__}: {exc}")
        logger.error("[%s] 诊断异常: %s", diag_id, exc, exc_info=True)
        _print_partial_result(diag_id, step_count, elapsed, partial_evidence, interrupted=True)
        _save_error_record(diag_id, alert_text, "error", step_count, elapsed, str(exc))


def _save_error_record(diag_id: str, alert_text: str, status: str, steps: int, elapsed: float, error: str):
    """异常/中断时也保存记录到数据库。"""
    try:
        save_diagnosis({
            "diagnosis_id": diag_id,
            "alert_text": alert_text,
            "status": status,
            "services": [],
            "root_cause": "",
            "confidence": 0.0,
            "steps": steps,
            "elapsed_seconds": elapsed,
            "report": {},
            "error": error,
        })
    except Exception as e:
        logger.warning("[%s] 异常记录保存失败: %s", diag_id, e)


def _print_partial_result(diag_id: str, step_count: int, elapsed: float, evidence: list[str], interrupted: bool = False):
    """异常中断时输出已收集到的部分诊断证据。"""
    print(f"\n{'='*60}")
    print(f"  诊断{'中断' if interrupted else '异常'}（ID: {diag_id}）")
    print(f"  已完成 {step_count} 轮调度，耗时 {elapsed} 秒")
    print(f"{'='*60}")
    if evidence:
        print(f"\n  已收集的部分证据:")
        for e in evidence:
            print(f"    {e[:200]}")
    print(f"\n  提示: 错误记录已保存到数据库\n")


# ============================================================================
# 统计命令
# ============================================================================


def _cmd_stats(days: int):
    """输出最近 N 天的诊断统计报告。"""
    stats = get_stats(days)
    by_date = stats.get("by_date", [])

    print(f"\n{'='*70}")
    print(f"  诊断统计报告（最近 {days} 天）")
    print(f"{'='*70}\n")

    # ── 总览 ──────────────────────────────────────────────────────
    print(f"  总诊断次数:    {stats['total']}")
    print(f"  成功:          {stats['completed']}  ({stats['success_rate']:.1%})")
    print(f"  失败/中断:     {stats['error']}")
    print(f"  平均置信度:    {stats['avg_confidence']}")
    print(f"  平均调度轮数:  {stats['avg_steps']}")
    print(f"  平均耗时:      {stats['avg_seconds']} 秒")
    print()

    # ── 按日期分布 ────────────────────────────────────────────────
    if by_date:
        print(f"  {'─'*60}")
        print(f"  按日期分布")
        print(f"  {'─'*60}")
        print(f"  {'日期':<12} {'次数':>5} {'平均置信度':>10}")
        print(f"  {'─'*12} {'─'*5} {'─'*10}")
        for d in by_date:
            print(f"  {d['date']:<12} {d['count']:>5} {d['avg_confidence']:>10.2f}")
        print()

    # ── 故障最多的服务 Top 10 ─────────────────────────────────────
    top_services = stats.get("top_services", [])
    if top_services:
        print(f"  {'─'*60}")
        print(f"  故障最多的服务 Top {len(top_services)}")
        print(f"  {'─'*60}")
        print(f"  {'服务名':<25} {'次数':>5} {'平均置信度':>10}")
        print(f"  {'─'*25} {'─'*5} {'─'*10}")
        for svc in top_services:
            print(f"  {svc['service']:<25} {svc['count']:>5} {svc['avg_confidence']:>10.2f}")
        print()

    # ── 最常见根因 Top 5 ──────────────────────────────────────────
    top_causes = stats.get("top_root_causes", [])
    if top_causes:
        print(f"  {'─'*60}")
        print(f"  最常见根因 Top {len(top_causes)}")
        print(f"  {'─'*60}")
        for i, c in enumerate(top_causes, 1):
            print(f"  {i}. [{c['count']}次] {c['cause']}")
        print()

    print(f"{'='*70}")


def _cmd_history(limit: int):
    """输出最近 N 条诊断记录。"""
    rows = get_recent(limit)

    print(f"\n  最近 {len(rows)} 条诊断记录")
    print(f"  {'─'*80}")
    print(f"  {'ID':<10} {'时间':<20} {'状态':<10} {'耗时':>6} {'置信度':>6} {'根因摘要'}")
    print(f"  {'─'*10} {'─'*20} {'─'*10} {'─'*6} {'─'*6} {'─'*30}")

    for r in rows:
        status_icon = {"completed": "OK", "error": "ERR", "aborted": "ABT"}.get(r.get("status", ""), "??")
        root = (r.get("root_cause") or "")[:60]
        print(f"  {r.get('diagnosis_id',''):<10} {r.get('created_at',''):<20} {status_icon:<10} "
              f"{r.get('elapsed_seconds',0):>5.1f}s {r.get('confidence',0):>5.0%} {root}")

    print(f"  {'─'*80}\n")


# ============================================================================
# 主入口
# ============================================================================


def main():
    parser = argparse.ArgumentParser(
        description="AIOps 智能运维 Agent —— 自动定位微服务故障根因"
    )
    sub = parser.add_subparsers(dest="command", help="运行模式")

    # 子命令: diagnose
    diag_parser = sub.add_parser("diagnose", help="运行一次诊断")
    diag_parser.add_argument("--alert", "-a", type=str, help="告警内容")

    # 子命令: serve
    serve_parser = sub.add_parser("serve", help="启动 HTTP 服务模式")
    serve_parser.add_argument("--port", "-p", type=int, default=8000, help="监听端口（默认 8000）")

    # 子命令: stats
    stats_parser = sub.add_parser("stats", help="查看诊断统计")
    stats_parser.add_argument("--days", "-d", type=int, default=30, help="统计最近 N 天（默认 30）")

    # 子命令: history
    hist_parser = sub.add_parser("history", help="查看最近诊断记录")
    hist_parser.add_argument("--limit", "-n", type=int, default=20, help="显示条数（默认 20）")

    args = parser.parse_args()

    if args.command == "serve":
        from agent.server import run_server
        run_server(args.port)

    elif args.command == "stats":
        _cmd_stats(args.days)

    elif args.command == "history":
        _cmd_history(args.limit)

    elif args.command == "diagnose" and args.alert:
        run_diagnosis(args.alert)

    elif args.command == "diagnose" or args.command is None:
        alert = getattr(args, "alert", None)
        if alert:
            run_diagnosis(alert)
        else:
            print("AIOps Agent v0.2.0")
            print()
            print("用法:")
            print("  aiops diagnose --alert \"告警内容\"    运行一次诊断")
            print("  aiops serve --port 8000              启动 HTTP 服务")
            print("  aiops stats --days 30                查看最近 30 天统计")
            print("  aiops history --limit 20             查看最近诊断记录")
            print()
            print("输入告警内容开始诊断（输入 quit 退出）:")
            while True:
                alert = input(">>> ").strip()
                if alert.lower() in ("quit", "exit", "q"):
                    print("再见。")
                    break
                if alert:
                    run_diagnosis(alert)


if __name__ == "__main__":
    main()
