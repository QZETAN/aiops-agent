"""
AIOps Agent 自动化诊断测试脚本。
运行方式：python scripts/test_pipeline.py（需要先 pip install -e .）
"""
import argparse
import json
import sys
import time

# 修复 Windows GBK 编码下 emoji 输出报错的问题
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from langchain_core.messages import HumanMessage
from agent.agents.graph import build_graph

TEST_CASES = [
    {
        "id": "npe-01",
        "alert": "order-service 在 15:03 开始 5xx 错误率飙升到 25%，大量请求返回 500",
        "expected": "NullPointerException 或 NPE 或空指针",
        "category": "代码异常",
    },
    {
        "id": "latency-01",
        "alert": "gateway-service 整体响应延迟 P99 超过 3 秒，用户反馈页面打不开",
        "expected": "超时 或 慢请求 或 timeout 或 slow",
        "category": "性能劣化",
    },
    {
        "id": "cpu-01",
        "alert": "user-service CPU 使用率突然飙升到 95%，机器风扇狂转",
        "expected": "CPU 或 cpu 或 资源耗尽",
        "category": "资源异常",
    },
    {
        "id": "oom-01",
        "alert": "order-service 频繁 Full GC 后 OOM 重启，每次重启后几分钟又挂",
        "expected": "内存 或 OOM 或 OutOfMemory 或 堆内存",
        "category": "资源异常",
    },
    {
        "id": "timeout-01",
        "alert": "gateway 调用 order-service 超时，order-service 调用 user-service 也超时，形成连锁超时",
        "expected": "超时 或 timeout 或 调用链",
        "category": "级联故障",
    },
]


def run_single_test(test_case: dict, graph) -> dict:
    """运行单次诊断，返回结果摘要。"""
    alert = test_case["alert"]
    initial_state = {
        "messages": [HumanMessage(content=f"[告警] {alert}")],
        "next_agent": "",
        "intermediate_steps": [],
        "evidence": {},
        "iteration_count": 0,
        "reflection_round": 0,
        "diagnosis_id": test_case["id"],
        "total_tokens": 0,
    }

    start_time = time.time()

    try:
        step_count = 0
        final_report = None

        for event in graph.stream(initial_state, {"recursion_limit": 60}):
            node_name = list(event.keys())[0]
            node_output = event[node_name]
            if node_name == "supervisor":
                step_count += 1
            if node_name == "infer":
                messages = node_output.get("messages", [])
                for msg in messages:
                    if hasattr(msg, 'content'):
                        try:
                            final_report = json.loads(msg.content)
                        except json.JSONDecodeError:
                            pass

        elapsed = round(time.time() - start_time, 1)
        confidence = 0.0
        root_cause_text = ""
        if final_report:
            root_causes = final_report.get("root_causes", [])
            if root_causes:
                confidence = root_causes[0].get("confidence", 0.0)
                root_cause_text = root_causes[0].get("description", "")

        return {
            "id": test_case["id"],
            "alert": alert[:80],
            "expected": test_case["expected"],
            "category": test_case["category"],
            "status": "ok" if final_report else "no_report",
            "confidence": confidence,
            "root_cause": root_cause_text[:200],
            "steps": step_count,
            "elapsed_sec": elapsed,
            "report": final_report,
        }
    except Exception as e:
        elapsed = round(time.time() - start_time, 1)
        return {
            "id": test_case["id"],
            "alert": alert[:80],
            "expected": test_case["expected"],
            "category": test_case["category"],
            "status": "error",
            "confidence": 0.0,
            "root_cause": str(e)[:200],
            "steps": 0,
            "elapsed_sec": elapsed,
            "report": None,
        }


def print_report(results: list):
    """打印 Markdown 测试报告。"""
    total = len(results)
    ok = sum(1 for r in results if r["status"] == "ok")
    error = sum(1 for r in results if r["status"] == "error")
    avg_confidence = round(sum(r["confidence"] for r in results) / total, 2) if total else 0
    avg_steps = round(sum(r["steps"] for r in results) / total, 1) if total else 0
    avg_elapsed = round(sum(r["elapsed_sec"] for r in results) / total, 1) if total else 0
    high_conf = sum(1 for r in results if r["confidence"] >= 0.7)

    print("\n" + "=" * 70)
    print("# AIOps Agent 自动化诊断测试报告")
    print("=" * 70)
    print(f"""
## 总体指标
| 指标 | 值 |
|------|-----|
| 测试用例总数 | {total} |
| 成功执行 | {ok} |
| 执行失败 | {error} |
| 平均置信度 | {avg_confidence} |
| 平均诊断步数 | {avg_steps} |
| 平均耗时（秒） | {avg_elapsed} |
| 高置信度 (≥0.7) | {high_conf}/{total} |
""")
    print("## 逐用例详情\n")
    print("| ID | 类型 | 置信度 | 步数 | 耗时 | 根因摘要 |")
    print("|----|------|--------|------|------|---------|")
    for r in results:
        emoji = "✅" if r["confidence"] >= 0.7 else ("⚠️" if r["status"] == "ok" else "❌")
        desc = r["root_cause"].replace("|", "\\|")[:60]
        print(f"| {emoji} {r['id']} | {r['category']} | {r['confidence']:.2f} | {r['steps']} | {r['elapsed_sec']}s | {desc} |")

    print("\n## 完整诊断报告\n")
    for r in results:
        print(f"### {r['id']}: {r['alert']}")
        print(f"状态: {r['status']} | 置信度: {r['confidence']} | 步数: {r['steps']} | 耗时: {r['elapsed_sec']}s")
        if r["report"]:
            print("```json")
            print(json.dumps(r["report"], ensure_ascii=False, indent=2))
            print("```")
        else:
            print(f"根因: {r['root_cause']}")
        print()


def main():
    parser = argparse.ArgumentParser(description="AIOps Agent 自动化诊断测试")
    parser.add_argument("--dry-run", action="store_true", help="只验证环境不实际测试")
    parser.add_argument("--single", type=str, help="只测试指定 ID 的用例")
    parser.add_argument("--rounds", type=int, default=1, help="每个用例测试几轮，默认 1")
    args = parser.parse_args()

    print("验证环境...")
    try:
        graph = build_graph()
        print("✅ Graph 编译成功")
    except Exception as e:
        print(f"❌ Graph 编译失败: {e}")
        sys.exit(1)

    if args.dry_run:
        print("Dry-run 模式，跳过实际测试。环境就绪。")
        return

    if args.single:
        cases = [c for c in TEST_CASES if c["id"] == args.single]
        if not cases:
            print(f"未找到用例: {args.single}")
            return
    else:
        cases = TEST_CASES

    all_results = []
    for case in cases:
        for round_idx in range(args.rounds):
            label = f"{case['id']}" if args.rounds == 1 else f"{case['id']}-R{round_idx+1}"
            print(f"  测试 {label}...", end=" ", flush=True)
            result = run_single_test(case, graph)
            result["id"] = label
            all_results.append(result)
            print(f"置信度={result['confidence']:.2f} 步数={result['steps']} 耗时={result['elapsed_sec']}s")

    print_report(all_results)


if __name__ == "__main__":
    main()
