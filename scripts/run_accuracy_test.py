"""
准确率测试脚本 —— 5 种故障各跑 1 轮，自动注入、诊断、记录。

用法：
  PYTHONIOENCODING=utf-8 python scripts/run_accuracy_test.py
"""

import json
import sys
import time
import uuid
import requests
from datetime import datetime

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from langchain_core.messages import HumanMessage
from agent.agents.graph import build_graph
from agent.db import save_diagnosis

# ==================== 服务地址 ====================

SERVICES = {
    "gateway": "http://localhost:8080",
    "order":   "http://localhost:8081",
    "user":    "http://localhost:8082",
}

# ==================== 测试用例 ====================

TEST_CASES = [
    {
        "id": "cpu-01",
        "category": "资源异常",
        "fault_service": "user",
        "fault_type": "cpu",
        "fault_params": {"seconds": 30},
        "alert": "有服务 CPU 突然飙升到 95%，响应明显变慢",
        "expected_keywords": ["CPU", "cpu", "飙升", "fault", "故障注入"],
    },
    {
        "id": "slow-01",
        "category": "性能劣化",
        "fault_service": "order",
        "fault_type": "slow",
        "fault_params": {"seconds": 5},
        "alert": "有服务响应突然变慢，用户反馈页面卡顿",
        "expected_keywords": ["慢", "延迟", "slow", "sleep", "fault"],
    },
    {
        "id": "npe-01",
        "category": "代码异常",
        "fault_service": "gateway",
        "fault_type": "npe",
        "fault_params": {},
        "alert": "有服务突然返回大量 500 错误",
        "expected_keywords": ["NullPointer", "NPE", "空指针", "null", "fault"],
    },
    {
        "id": "memory-01",
        "category": "资源异常",
        "fault_service": "order",
        "fault_type": "memory",
        "fault_params": {"mbPerCall": 15, "calls": 5},
        "alert": "有服务内存持续上涨，怀疑内存泄漏",
        "expected_keywords": ["内存", "memory", "leak", "泄漏", "fault"],
    },
    {
        "id": "timeout-01",
        "category": "级联故障",
        "fault_service": "order",
        "fault_type": "slow",
        "fault_params": {"seconds": 8},
        "alert": "有服务间调用超时，上游大量请求堆积",
        "expected_keywords": ["超时", "timeout", "慢", "slow", "延迟", "fault"],
    },
]


def inject_fault(service: str, fault_type: str, params: dict) -> bool:
    """注入故障，返回是否成功。"""
    url = f"{SERVICES[service]}/fault/{fault_type}"
    method = "POST" if fault_type != "slow" else "GET"
    try:
        if method == "POST":
            resp = requests.post(url, params=params, timeout=10)
        else:
            resp = requests.get(url, params=params, timeout=10)
        print(f"  故障已注入: {service} {fault_type} → {resp.status_code}")
        return True
    except Exception as e:
        print(f"  故障注入失败: {e}")
        return False


def generate_traffic(count: int = 15):
    """产生流量，触发 trace 和日志。"""
    for _ in range(count):
        try:
            requests.get("http://localhost:8080/api/order/1", timeout=5)
        except Exception:
            pass
        time.sleep(0.2)


def run_diagnosis(alert_text: str) -> dict:
    """运行一次诊断，返回结果 dict。"""
    diag_id = str(uuid.uuid4())[:8]
    start = time.time()

    state = {
        "messages": [HumanMessage(content=f"[告警] {alert_text}")],
        "next_agent": "",
        "intermediate_steps": [],
        "evidence": {},
        "iteration_count": 0,
        "reflection_round": 0,
        "diagnosis_id": diag_id,
        "total_tokens": 0,
    }

    graph = build_graph()
    steps = 0
    report = {}
    services_found: list[str] = []

    try:
        for event in graph.stream(state, {"recursion_limit": 60}):
            node = list(event.keys())[0]
            output = event[node]
            if node == "supervisor":
                steps += 1
            elif node == "metrics_expert":
                msgs = [m for m in output.get("messages", []) if hasattr(m, "type") and m.type == "ai"]
                if msgs:
                    try:
                        data = json.loads(msgs[-1].content)
                        if "services_found" in data:
                            services_found = data["services_found"]
                    except Exception:
                        pass
            elif node == "infer":
                for m in output.get("messages", []):
                    if hasattr(m, "content"):
                        try:
                            report = json.loads(m.content)
                        except json.JSONDecodeError:
                            report = {"raw_output": str(m.content)[:1000]}

        elapsed = round(time.time() - start, 1)
        root_causes = report.get("root_causes", [])
        top = root_causes[0] if root_causes else {}
        confidence = top.get("confidence", 0.0)
        root_cause = top.get("description", "")

        save_diagnosis({
            "diagnosis_id": diag_id,
            "alert_text": alert_text,
            "status": "completed",
            "services": services_found,
            "root_cause": root_cause,
            "confidence": confidence,
            "steps": steps,
            "elapsed_seconds": elapsed,
            "report": report,
        })

        return {
            "diagnosis_id": diag_id,
            "status": "completed",
            "confidence": confidence,
            "root_cause": root_cause,
            "steps": steps,
            "elapsed_seconds": elapsed,
            "services_found": services_found,
            "report": report,
        }
    except Exception as e:
        elapsed = round(time.time() - start, 1)
        return {
            "diagnosis_id": diag_id,
            "status": "error",
            "confidence": 0.0,
            "root_cause": str(e)[:200],
            "steps": steps,
            "elapsed_seconds": elapsed,
            "services_found": [],
            "report": {},
        }


def check_accuracy(root_cause: str, keywords: list[str]) -> bool:
    """检查根因描述是否包含预期关键词。"""
    return any(kw.lower() in root_cause.lower() for kw in keywords)


def main():
    print("=" * 70)
    print("  AIOps Agent 准确率测试")
    print(f"  开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  测试用例: {len(TEST_CASES)} 个")
    print("=" * 70)

    results = []

    for i, tc in enumerate(TEST_CASES, 1):
        print(f"\n{'─' * 70}")
        print(f"[{i}/{len(TEST_CASES)}] {tc['id']} ({tc['category']})")
        print(f"  告警: {tc['alert']}")
        print(f"{'─' * 70}")

        # 注入故障
        ok = inject_fault(tc["fault_service"], tc["fault_type"], tc["fault_params"])
        if not ok:
            results.append({"id": tc["id"], "status": "fault_injection_failed"})
            continue

        # 产生流量（触发 trace）
        print("  产生流量...")
        generate_traffic(10)

        # 多等几秒让 OTel 上报数据
        time.sleep(3)

        # 诊断
        print("  诊断中...")
        result = run_diagnosis(tc["alert"])
        result["id"] = tc["id"]
        result["category"] = tc["category"]
        result["expected_keywords"] = tc["expected_keywords"]

        # 判断是否命中
        result["accurate"] = check_accuracy(result.get("root_cause", ""), tc["expected_keywords"])

        # 打印摘要
        conf = result.get("confidence", 0)
        bar = "🟢" if conf >= 0.7 else ("🟡" if conf >= 0.5 else "🔴")
        acc = "✅ 命中" if result["accurate"] else "❌ 未命中"

        print(f"  结果: {acc} | 置信度: {bar} {conf:.0%} | 步数: {result['steps']} | 耗时: {result['elapsed_seconds']}s")
        print(f"  根因: {result.get('root_cause', '')[:120]}")
        print(f"  发现服务: {result.get('services_found', [])}")

        results.append(result)

    # ==================== 汇总报告 ====================
    completed = [r for r in results if r.get("status") == "completed"]
    accurate = [r for r in completed if r.get("accurate")]
    errors = [r for r in results if r.get("status") == "error"]

    print(f"\n{'=' * 70}")
    print(f"  准确率测试报告")
    print(f"{'=' * 70}")
    print(f"")
    print(f"  总用例数:       {len(results)}")
    print(f"  成功执行:       {len(completed)}")
    print(f"  执行异常:       {len(errors)}")
    print(f"  命中（准确）:   {len(accurate)}")
    print(f"  未命中:         {len(completed) - len(accurate)}")
    print(f"  准确率:         {len(accurate)/max(len(completed),1):.0%} ({len(accurate)}/{len(completed)})")
    if completed:
        avg_conf = sum(r["confidence"] for r in completed) / len(completed)
        avg_steps = sum(r["steps"] for r in completed) / len(completed)
        avg_time = sum(r["elapsed_seconds"] for r in completed) / len(completed)
        high_conf = sum(1 for r in completed if r["confidence"] >= 0.7)
        print(f"  平均置信度:     {avg_conf:.0%}")
        print(f"  高置信度(>=0.7): {high_conf}/{len(completed)}")
        print(f"  平均诊断步数:   {avg_steps:.1f}")
        print(f"  平均诊断时间:   {avg_time:.1f} 秒")
    print(f"")

    # 详细结果
    print(f"  {'─' * 65}")
    print(f"  {'ID':<12} {'类型':<10} {'命中':<6} {'置信度':<8} {'步数':<5} {'耗时':<6} {'根因摘要'}")
    print(f"  {'─' * 12} {'─' * 10} {'─' * 6} {'─' * 8} {'─' * 5} {'─' * 6} {'─' * 20}")
    for r in results:
        acc = "✅" if r.get("accurate") else ("❌" if r.get("status") == "completed" else "⚠️")
        root = (r.get("root_cause") or "")[:60]
        print(f"  {r['id']:<12} {r.get('category',''):<10} {acc:<6} "
              f"{r.get('confidence',0):.0%}     {r.get('steps',0):<5} "
              f"{r.get('elapsed_seconds',0):.1f}s  {root}")
    print(f"  {'─' * 65}")

    print(f"\n  测试完成: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # 保存结果到文件
    with open("data/accuracy_test_result.json", "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "summary": {
                "total": len(results),
                "completed": len(completed),
                "accurate": len(accurate),
                "accuracy": f"{len(accurate)/max(len(completed),1):.0%}",
            },
            "details": [{k: v for k, v in r.items() if k != "report"} for r in results],
        }, f, ensure_ascii=False, indent=2)

    print(f"  详细结果已保存到 data/accuracy_test_result.json")


if __name__ == "__main__":
    main()
