"""
AIOps Agent Web UI —— 基于 Streamlit 构建。
运行方式：streamlit run ui/streamlit_app.py
"""
import json
import sys
import time

sys.path.insert(0, ".")

import streamlit as st
from langchain_core.messages import HumanMessage
from agent.agents.graph import build_graph

st.set_page_config(
    page_title="AIOps 智能运维 Agent",
    page_icon="🔍",
    layout="wide",
)

st.title("🔍 AIOps 智能运维 Agent")
st.caption("基于 LangGraph 多智能体 + MCP 工具集 · 自动定位微服务故障根因")

with st.sidebar:
    st.header("⚙️ 配置")
    st.markdown("**数据源**")
    st.code("Prometheus  ·  Loki  ·  Jaeger  ·  Git", language=None)
    st.markdown("**LLM**")
    st.code("DeepSeek Chat", language=None)
    st.divider()
    st.header("📋 故障注入快捷方式")
    st.code("python scripts/fault_injector.py --service order --fault slow --duration 5", language="bash")
    st.code("python scripts/fault_injector.py --service user --fault cpu --duration 30", language="bash")
    st.code("python scripts/fault_injector.py --service user --fault npe", language="bash")
    st.divider()
    st.caption("AIOps Agent v0.1 · 2026")

col1, col2 = st.columns([4, 1])
with col1:
    alert_input = st.text_input(
        "告警内容",
        placeholder="例如：order-service 调用延迟突然升高，请求响应很慢",
        label_visibility="collapsed",
    )
with col2:
    start_btn = st.button("🚀 开始诊断", type="primary", use_container_width=True)

if "diagnosis_history" not in st.session_state:
    st.session_state.diagnosis_history = []

if start_btn and alert_input.strip():
    graph = build_graph()

    initial_state = {
        "messages": [HumanMessage(content=f"[告警] {alert_input.strip()}")],
        "next_agent": "",
        "intermediate_steps": [],
        "evidence": {},
        "iteration_count": 0,
        "reflection_round": 0,
    }

    status_container = st.empty()
    report_container = st.empty()

    step_count = 0
    supervisor_decisions = []
    expert_findings = []

    start_time = time.time()

    for event in graph.stream(initial_state, {"recursion_limit": 60}):
        node_name = list(event.keys())[0]
        node_output = event[node_name]
        messages = node_output.get("messages", [])

        if node_name == "supervisor":
            step_count += 1
            for msg in messages:
                if hasattr(msg, "content") and "下一步" in msg.content:
                    supervisor_decisions.append(msg.content)

        elif node_name in ("metrics_expert", "logs_expert", "traces_expert", "code_expert"):
            ai_msgs = [m for m in messages if hasattr(m, "type") and m.type == "ai"]
            if ai_msgs:
                content = ai_msgs[-1].content
                if len(content) > 300:
                    content = content[-300:]
                expert_name = {
                    "metrics_expert": "📊 指标专家",
                    "logs_expert": "📝 日志专家",
                    "traces_expert": "🔗 调用链专家",
                    "code_expert": "💻 代码专家",
                }.get(node_name, node_name)
                expert_findings.append(f"**{expert_name}**\n{content.strip()[:500]}")

        elif node_name == "infer":
            for msg in messages:
                if hasattr(msg, "content"):
                    try:
                        report = json.loads(msg.content)
                        elapsed = round(time.time() - start_time, 1)
                        root_causes = report.get("root_causes", [])
                        top_cause = root_causes[0] if root_causes else {}
                        confidence = top_cause.get("confidence", 0)
                        conf_emoji = "🟢" if confidence >= 0.7 else ("🟡" if confidence >= 0.5 else "🔴")

                        with report_container.container():
                            st.success(f"诊断完成 · 耗时 {elapsed} 秒 · 共 {step_count} 轮调度")
                            col_a, col_b, col_c = st.columns(3)
                            col_a.metric("置信度", f"{conf_emoji} {confidence:.0%}")
                            col_b.metric("调度轮数", step_count)
                            col_c.metric("根因数", len(root_causes))

                            st.subheader("📋 根因分析报告")
                            for cause in root_causes:
                                rank = cause.get("rank", "?")
                                desc = cause.get("description", "")
                                conf = cause.get("confidence", 0)
                                fix = cause.get("fix_suggestion", "")
                                with st.expander(f"#{rank} {desc[:80]}... (置信度 {conf:.0%})", expanded=(rank == 1)):
                                    st.markdown(f"**根因描述**: {desc}")
                                    st.markdown(f"**置信度**: {conf:.0%}")
                                    evidence = cause.get("evidence", [])
                                    if evidence:
                                        st.markdown("**证据链**:")
                                        for e in evidence:
                                            st.markdown(f"- {e}")
                                    if fix:
                                        st.markdown(f"**修复建议**: {fix}")
                            summary = report.get("diagnosis_summary", "")
                            if summary:
                                st.caption(f"诊断摘要: {summary}")
                            with st.expander("查看完整 JSON 报告"):
                                st.json(report)
                    except json.JSONDecodeError:
                        pass

        with status_container.container():
            if supervisor_decisions:
                st.info(f"**调度决策**\n" + "\n".join(f"{i+1}. {d}" for i, d in enumerate(supervisor_decisions)))
            if expert_findings:
                st.text("最新专家分析:\n" + expert_findings[-1][:500] if expert_findings else "")

    if not supervisor_decisions:
        st.warning("诊断未产生任何调度决策，请检查告警输入")

elif start_btn and not alert_input.strip():
    st.warning("请输入告警内容")
