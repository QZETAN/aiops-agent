"""
Graph 构建与组件验证脚本。
运行方式：python scripts/test_graph.py（需要先 pip install -e .）
"""
print("=" * 60)
print("[1/6] State 字段检查")
print("=" * 60)
from agent.agents.state import AgentState

expected_fields = {"messages", "next_agent", "intermediate_steps", "evidence", "iteration_count", "reflection_round", "diagnosis_id", "total_tokens"}
actual_fields = set(AgentState.__annotations__.keys())
if actual_fields == expected_fields:
    print(f"  [OK] AgentState {len(actual_fields)} 个字段完整: {sorted(actual_fields)}")
else:
    missing = expected_fields - actual_fields
    extra = actual_fields - expected_fields
    if missing:
        print(f"  [FAIL] 缺少: {missing}")
    if extra:
        print(f"  [WARN]  多余: {extra}")

print("\n" + "=" * 60)
print("[2/6] 逐模块导入")
print("=" * 60)
try:
    from agent.agents.supervisor import supervisor_node
    print("  [OK] supervisor.py")
except Exception as e:
    print(f"  [FAIL] supervisor.py: {e}")

try:
    from agent.agents.experts import EXPERTS
    print("  [OK] experts.py")
except Exception as e:
    print(f"  [FAIL] experts.py: {e}")

try:
    from agent.agents.reflect import reflect_node
    print("  [OK] reflect.py")
except Exception as e:
    print(f"  [FAIL] reflect.py: {e}")

print("\n" + "=" * 60)
print("[3/6] Graph 编译")
print("=" * 60)
try:
    from agent.agents.graph import build_graph
    graph = build_graph()
    print("  [OK] build_graph() 成功")
except Exception as e:
    print(f"  [FAIL] 编译失败: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("\n" + "=" * 60)
print("[4/6] 节点列表")
print("=" * 60)
expected_nodes = {"supervisor", "metrics_expert", "logs_expert", "traces_expert", "code_expert", "infer", "reflect"}
actual_nodes = set(graph.get_graph().nodes.keys()) - {"__start__", "__end__"}
if actual_nodes == expected_nodes:
    print(f"  [OK] 7 个节点全部就位")
else:
    missing = expected_nodes - actual_nodes
    extra = actual_nodes - expected_nodes
    if missing:
        print(f"  [FAIL] 缺少: {missing}")
    if extra:
        print(f"  [WARN]  多余: {extra}")

print("\n" + "=" * 60)
print("[5/6] Expert 映射")
print("=" * 60)
expected_experts = {"metrics_expert", "logs_expert", "traces_expert", "code_expert"}
actual_experts = set(EXPERTS.keys())
if actual_experts == expected_experts:
    for key, (_, name) in EXPERTS.items():
        print(f"  {key:20s} → {name}")
    print("  [OK] 4 个 Expert 就绪")
else:
    print(f"  [FAIL] 不匹配")

print("\n" + "=" * 60)
print("[6/6] 初始状态结构验证")
print("=" * 60)
try:
    from langchain_core.messages import HumanMessage
    test_state = {
        "messages": [HumanMessage(content="测试告警")],
        "next_agent": "",
        "intermediate_steps": [],
        "evidence": {},
        "iteration_count": 0,
        "reflection_round": 0,
        "diagnosis_id": "test-001",
        "total_tokens": 0,
    }
    for k in expected_fields:
        assert k in test_state, f"缺少字段: {k}"
    print("  [OK] 初始状态结构正确")
except Exception as e:
    print(f"  [FAIL] {e}")

print("\n" + "=" * 60)
print("验证完成")
print("=" * 60)
