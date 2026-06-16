"""
AIOps 智能运维 Agent —— CLI 入口。

用法：
  python app.py --alert "order-service 的 5xx 错误率突然升高"
  python app.py                                  # 交互式输入
"""
import argparse
import json
import sys

sys.path.insert(0, ".")

from langchain_core.messages import HumanMessage
from agent.agents.graph import build_graph


def run_diagnosis(alert_text: str):
    """运行一次完整的故障诊断，流式输出每一步。"""
    initial_state = {
        "messages": [HumanMessage(content=f"[告警] {alert_text}")],
        "next_agent": "",
        "intermediate_steps": [],
        "evidence": {},
        "iteration_count": 0,
        "reflection_round": 0,
    }

    print(f"\n{'='*60}")
    print(f"  AIOps Agent 诊断启动")
    print(f"  告警: {alert_text}")
    print(f"{'='*60}\n")

    graph = build_graph()

    step_count = 0

    for step_idx, event in enumerate(graph.stream(initial_state, {"recursion_limit": 50})):
        node_name = list(event.keys())[0]
        node_output = event[node_name]
        messages = node_output.get("messages", [])

        if node_name == "supervisor":
            step_count += 1
            for msg in messages:
                if hasattr(msg, 'content') and '下一步' in msg.content:
                    content = msg.content
                    print(f"  [{step_count}] {content}")
                    break

        elif node_name in ("metrics_expert", "logs_expert", "traces_expert", "code_expert"):
            ai_msgs = [
                m for m in messages
                if hasattr(m, 'content') and hasattr(m, 'type') and m.type == 'ai'
            ]
            if ai_msgs:
                last_ai = ai_msgs[-1]
                content = last_ai.content if hasattr(last_ai, 'content') else str(last_ai)
                if len(content) > 400:
                    content = content[-400:]
                print(f"     └─ {node_name}: {content.strip()[:300]}")

        elif node_name == "infer":
            print(f"\n{'='*60}")
            print(f"  根因分析报告")
            print(f"{'='*60}\n")
            for msg in messages:
                if hasattr(msg, 'content'):
                    try:
                        report = json.loads(msg.content)
                        print(json.dumps(report, ensure_ascii=False, indent=2))
                    except json.JSONDecodeError:
                        print(msg.content[:2000])

    print(f"\n{'='*60}")
    print(f"  诊断完成（共 {step_count} 轮调度）")
    print(f"{'='*60}\n")


def main():
    parser = argparse.ArgumentParser(
        description="AIOps 智能运维 Agent —— 自动定位微服务故障根因"
    )
    parser.add_argument(
        "--alert", "-a",
        type=str,
        help='告警内容，如 "order-service 的 5xx 错误率突然升高"',
    )
    args = parser.parse_args()

    if args.alert:
        run_diagnosis(args.alert)
    else:
        print("╔══════════════════════════════════════════════╗")
        print("║    AIOps 智能运维 Agent v0.1                ║")
        print("║    基于 LangGraph 多智能体 + MCP 工具集     ║")
        print("╚══════════════════════════════════════════════╝")
        print()
        print("输入告警内容开始诊断（输入 quit 退出）:")
        print()

        while True:
            alert = input(">>> ").strip()
            if alert.lower() in ("quit", "exit", "q"):
                print("再见。")
                break
            if alert:
                run_diagnosis(alert)


if __name__ == "__main__":
    main()
