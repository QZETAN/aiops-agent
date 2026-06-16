"""
端到端诊断测试脚本。
运行方式：python scripts/test_e2e.py
"""
import sys
sys.path.insert(0, ".")

from langchain_core.messages import HumanMessage

print("=" * 60)
print("AIOps Agent 端到端诊断测试")
print("=" * 60)

initial_state = {
    "messages": [
        HumanMessage(content="[告警] order-service 的 5xx 错误率突然升高，请帮我排查根因。")
    ],
    "next_agent": "",
    "intermediate_steps": [],
    "evidence": {},
    "iteration_count": 0,
    "reflection_round": 0,
}

print("\n[初始告警] order-service 的 5xx 错误率突然升高，请帮我排查根因。\n")

from agent.agents.graph import build_graph

try:
    graph = build_graph()
    print("Graph 编译成功，开始诊断...\n")

    for step_idx, event in enumerate(graph.stream(initial_state, {"recursion_limit": 50})):
        node_name = list(event.keys())[0]
        node_output = event[node_name]
        messages = node_output.get("messages", [])
        print(f"--- 步骤 {step_idx + 1}: {node_name} ---")
        for msg in messages:
            content = msg.content if hasattr(msg, 'content') else str(msg)
            if len(content) > 500:
                content = content[:500] + "...(已截断)"
            print(f"  [{type(msg).__name__}] {content[:200]}")
        print()

    print("=" * 60)
    print("✅ 诊断流程执行完毕")

except KeyboardInterrupt:
    print("\n⚠️ 用户中断")
except Exception as e:
    print(f"\n❌ 执行异常: {e}")
    import traceback
    traceback.print_exc()
