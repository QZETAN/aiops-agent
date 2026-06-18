"""
AIOps Agent Web UI —— 基于 Streamlit 构建。

运行方式：
  streamlit run ui/streamlit_app.py

v0.2.0 新增：诊断/历史/统计 三页签，DB 持久化，实时流式进度。
"""

import json
import logging
import time
import uuid
from datetime import datetime

import streamlit as st
from langchain_core.messages import HumanMessage
from agent.agents.graph import build_graph
from agent.db import init_db, save_diagnosis, get_recent, get_stats

logger = logging.getLogger("aiops.ui")

# ============================================================================
# 页面配置
# ============================================================================

st.set_page_config(
    page_title="AIOps 智能运维 Agent",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ============================================================================
# 侧边栏
# ============================================================================

with st.sidebar:
    st.image("https://img.icons8.com/fluency/96/search--v1.png", width=48) if False else None
    st.markdown("## 🔍 AIOps Agent")
    st.caption("v0.2.0 · 多智能体故障诊断")

    st.divider()

    # 后端状态
    st.markdown("**📡 数据源**")
    import os
    prom = os.environ.get("PROMETHEUS_URL", "")
    loki = os.environ.get("LOKI_URL", "")
    jaeger = os.environ.get("JAEGER_URL", "")

    status_color = lambda url: "🟢" if url else "🔴"
    st.caption(f"{status_color(prom)} Prometheus {'已配置' if prom else '未配置'}")
    st.caption(f"{status_color(loki)} Loki {'已配置' if loki else '未配置'}")
    st.caption(f"{status_color(jaeger)} Jaeger {'已配置' if jaeger else '未配置'}")

    st.divider()

    # 数据库
    st.markdown("**🗄️ 数据库**")
    try:
        init_db()
        recent = get_recent(1)
        st.caption(f"🟢 SQLite · {len(get_recent(1000))} 条记录")
    except Exception:
        st.caption("🔴 数据库不可用")

    st.divider()

    st.divider()
    st.caption("© 2026 AIOps Agent · MIT License")


# ============================================================================
# 主标题
# ============================================================================

st.title("🔍 AIOps 智能运维 Agent")
st.caption("输入一条告警 → Supervisor 调度 4 个诊断专家 → 自动定位根因 → 输出修复建议")

# ============================================================================
# 三页签
# ============================================================================

tab_diag, tab_history, tab_stats = st.tabs(["🩺 诊断", "📋 历史记录", "📊 统计分析"])

# ============================================================================
# Tab 1: 诊断
# ============================================================================

# 初始化 session state
if "alert_input" not in st.session_state:
    st.session_state.alert_input = ""

with tab_diag:
    col_input, col_btn = st.columns([5, 1])
    with col_input:
        alert_text = st.text_area(
            "告警内容",
            key="alert_input",
            placeholder="描述你看到的故障现象。提示：告警描述越具体，Agent 能查的数据源越多，置信度越高。\n\n例如：\n  \"某个服务 CPU 飙升到 95%，日志刷 OOM，调用链大量超时\"  → 三源交叉验证，置信度 0.90+\n  \"系统有点卡\"                                              → 也能诊断，但信号可能不足",
            height=100,
            label_visibility="collapsed",
        )
    with col_btn:
        st.write("")
        st.write("")
        start_btn = st.button("🚀 开始诊断", type="primary", use_container_width=True)

    # 快捷示例（直接写入 text_area 的 session_state key，点完即填）
    with st.expander("📝 告警示例（点击展开）"):
        examples = [
            "有服务 5xx 错误率突然飙升到 25%，大量请求返回 500",
            "有服务整体响应延迟 P99 超过 3 秒，用户反馈页面打开慢",
            "有服务 CPU 使用率突然飙升到 95%，机器风扇狂转",
        ]
        cols = st.columns(len(examples))
        for i, ex in enumerate(examples):
            if cols[i].button(f"示例 {i + 1}", key=f"ex_{i}", use_container_width=True):
                st.session_state.alert_input = ex
                st.rerun()

    if start_btn and alert_text.strip():
        diag_id = str(uuid.uuid4())[:8]
        start_time = time.time()

        initial_state = {
            "messages": [HumanMessage(content=f"[告警] {alert_text.strip()}")],
            "next_agent": "",
            "intermediate_steps": [],
            "evidence": {},
            "iteration_count": 0,
            "reflection_round": 0,
            "diagnosis_id": diag_id,
            "total_tokens": 0,
        }

        # 进度显示
        progress_placeholder = st.empty()
        expert_placeholder = st.empty()
        report_placeholder = st.empty()

        graph = build_graph()
        step_count = 0
        supervisor_decisions: list[str] = []
        expert_findings: list[dict] = []
        final_report: dict = {}
        services_found: list[str] = []

        try:
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
                        expert_name = {
                            "metrics_expert": "📊 指标专家",
                            "logs_expert": "📝 日志专家",
                            "traces_expert": "🔗 调用链专家",
                            "code_expert": "💻 代码专家",
                        }.get(node_name, node_name)

                        # 提取服务发现结果
                        try:
                            data = json.loads(content)
                            if "services_found" in data:
                                services_found = data["services_found"]
                        except (json.JSONDecodeError, TypeError):
                            pass

                        expert_findings.append({
                            "name": expert_name,
                            "content": content[:600] if isinstance(content, str) else str(content)[:600],
                        })

                elif node_name == "infer":
                    for msg in messages:
                        if hasattr(msg, "content"):
                            try:
                                final_report = json.loads(msg.content)
                            except json.JSONDecodeError:
                                final_report = {"raw_output": msg.content[:2000]}

                # 实时更新进度
                with progress_placeholder.container():
                    # 步骤指示器
                    steps_html = " → ".join(
                        ["🔍 启动"] +
                        [f"{'📊' if 'metrics' in d else '📝' if 'logs' in d else '🔗' if 'traces' in d else '💻' if 'code' in d else '✅'}"
                         for d in supervisor_decisions]
                    )
                    if step_count > 0:
                        st.info(f"**诊断进行中**（ID: `{diag_id}`）\n\n{steps_html}")

                with expert_placeholder.container():
                    if expert_findings:
                        latest = expert_findings[-1]
                        with st.expander(f"{latest['name']} · 最新分析", expanded=True):
                            st.text(latest["content"][:500])

            # ── 诊断完成 ──────────────────────────────────────────
            elapsed = round(time.time() - start_time, 1)
            root_causes = final_report.get("root_causes", [])
            top_cause = root_causes[0] if root_causes else {}
            confidence = top_cause.get("confidence", 0)

            # 保存到数据库
            try:
                save_diagnosis({
                    "diagnosis_id": diag_id,
                    "alert_text": alert_text.strip(),
                    "status": "completed",
                    "services": services_found,
                    "root_cause": top_cause.get("description", ""),
                    "confidence": confidence,
                    "steps": step_count,
                    "elapsed_seconds": elapsed,
                    "report": final_report,
                })
            except Exception as e:
                logger.warning("DB 保存失败: %s", e)

            # 清空进度
            progress_placeholder.empty()
            expert_placeholder.empty()

            # 结果展示
            with report_placeholder.container():
                conf_emoji = "🟢" if confidence >= 0.7 else ("🟡" if confidence >= 0.5 else "🔴")
                st.success(f"✅ 诊断完成 · 耗时 {elapsed}s · {step_count} 轮调度 · ID `{diag_id}`")

                col_a, col_b, col_c, col_d = st.columns(4)
                col_a.metric("置信度", f"{conf_emoji} {confidence:.0%}")
                col_b.metric("调度轮数", step_count)
                col_c.metric("根因数", len(root_causes))
                col_d.metric("发现服务", len(services_found) or "—")

                if services_found:
                    st.caption("发现服务: " + ", ".join(services_found))

                st.divider()
                st.subheader("📋 根因分析报告")

                if root_causes:
                    for cause in root_causes:
                        rank = cause.get("rank", "?")
                        desc = cause.get("description", "")
                        conf = cause.get("confidence", 0)
                        fix = cause.get("fix_suggestion", "")
                        evidence = cause.get("evidence", [])

                        with st.expander(f"#{rank} {desc[:100]} (置信度 {conf:.0%})", expanded=(rank == 1)):
                            st.markdown(f"**根因**: {desc}")
                            st.progress(float(conf))
                            if evidence:
                                st.markdown("**证据链**:")
                                for e in evidence:
                                    st.markdown(f"- {e}")
                            if fix:
                                st.info(f"**修复建议**: {fix}")
                else:
                    st.warning("未能提取到结构化的根因分析结果")

                summary = final_report.get("diagnosis_summary", "")
                if summary:
                    st.caption(f"📝 {summary}")

                with st.expander("📄 完整 JSON 报告"):
                    st.json(final_report)

                # 调度详情回顾
                with st.expander("🔍 诊断过程详情"):
                    st.markdown("**调度决策序列**")
                    for i, d in enumerate(supervisor_decisions, 1):
                        st.caption(f"{i}. {d}")
                    st.markdown("**专家分析摘要**")
                    for f in expert_findings:
                        st.caption(f"*{f['name']}*")
                        st.text(f["content"][:400])

        except Exception as exc:
            elapsed = round(time.time() - start_time, 1)
            progress_placeholder.empty()
            expert_placeholder.empty()

            with report_placeholder.container():
                st.error(f"❌ 诊断异常: {type(exc).__name__}: {str(exc)[:200]}")
                st.caption(f"ID: `{diag_id}` · 已完成 {step_count} 轮 · 耗时 {elapsed}s")

            try:
                save_diagnosis({
                    "diagnosis_id": diag_id,
                    "alert_text": alert_text.strip(),
                    "status": "error",
                    "services": [],
                    "root_cause": "",
                    "confidence": 0.0,
                    "steps": step_count,
                    "elapsed_seconds": elapsed,
                    "report": {},
                    "error": str(exc)[:500],
                })
            except Exception:
                pass

    elif start_btn and not alert_text.strip():
        st.warning("请输入告警内容")


# ============================================================================
# Tab 2: 历史记录
# ============================================================================

with tab_history:
    st.subheader("📋 诊断历史")

    col1, col2 = st.columns([2, 1])
    with col1:
        hist_limit = st.slider("显示条数", 5, 100, 20, key="hist_limit")
    with col2:
        if st.button("🔄 刷新", use_container_width=True):
            st.rerun()

    try:
        rows = get_recent(hist_limit)
        if not rows:
            st.info("暂无诊断记录。运行一次诊断后自动出现。")
        else:
            for r in rows:
                status_icon = {"completed": "✅", "error": "❌", "aborted": "⚠️"}.get(r.get("status", ""), "❓")
                conf = r.get("confidence", 0)
                conf_color = "green" if conf >= 0.7 else ("orange" if conf >= 0.5 else "red")

                with st.expander(
                    f"{status_icon} [{r.get('created_at','')}] {r.get('alert_text','')[:80]} "
                    f"· 置信度 :{conf_color}[{conf:.0%}] · {r.get('elapsed_seconds',0):.0f}s",
                    expanded=False,
                ):
                    col_a, col_b, col_c = st.columns(3)
                    col_a.metric("置信度", f"{conf:.0%}")
                    col_b.metric("调度轮数", r.get("steps", 0))
                    col_c.metric("耗时", f"{r.get('elapsed_seconds', 0):.0f}s")

                    root = r.get("root_cause", "")
                    if root:
                        st.markdown(f"**根因**: {root}")

                    services = r.get("services", [])
                    if services and isinstance(services, list):
                        st.caption("涉及服务: " + ", ".join(services))

                    err = r.get("error_message", "")
                    if err:
                        st.error(f"错误: {err}")

                    report = r.get("report", {})
                    if report:
                        with st.expander("报告详情"):
                            st.json(report)
    except Exception as e:
        st.warning(f"加载历史记录失败: {e}")


# ============================================================================
# Tab 3: 统计分析
# ============================================================================

with tab_stats:
    st.subheader("📊 统计分析")

    col1, col2 = st.columns([2, 1])
    with col1:
        stat_days = st.selectbox("统计周期", [7, 14, 30, 60, 90], index=2, key="stat_days",
                                 format_func=lambda d: f"最近 {d} 天")
    with col2:
        if st.button("🔄 刷新统计", use_container_width=True):
            st.rerun()

    try:
        stats = get_stats(stat_days)

        if stats["total"] == 0:
            st.info("暂无诊断数据。运行几次诊断后这里会出现统计图表。")
        else:
            # ── 顶部 KPI 卡片 ───────────────────────────────────
            col_a, col_b, col_c, col_d, col_e = st.columns(5)
            col_a.metric("总诊断数", stats["total"])
            col_b.metric("成功率", f"{stats['success_rate']:.1%}")
            col_c.metric("平均置信度", f"{stats['avg_confidence']:.0%}")
            col_d.metric("平均轮数", f"{stats['avg_steps']}")
            col_e.metric("平均耗时", f"{stats['avg_seconds']}s")

            st.divider()

            # ── 按日期分布 ──────────────────────────────────────
            by_date = stats.get("by_date", [])
            if by_date:
                st.subheader("📅 每日诊断趋势")
                import pandas as pd
                df_date = pd.DataFrame(by_date)
                if not df_date.empty and "date" in df_date.columns and "count" in df_date.columns:
                    df_date = df_date.rename(columns={"date": "日期", "count": "诊断次数", "avg_confidence": "平均置信度"})
                    st.bar_chart(df_date.set_index("日期")["诊断次数"], height=200)

            # ── 服务 + 根因 两列 ─────────────────────────────────
            col_svc, col_cause = st.columns(2)

            with col_svc:
                top_services = stats.get("top_services", [])
                if top_services:
                    st.subheader("🔝 故障最多服务")
                    import pandas as pd
                    df_svc = pd.DataFrame(top_services)
                    df_svc = df_svc.rename(columns={"service": "服务", "count": "次数", "avg_confidence": "平均置信度"})
                    st.dataframe(df_svc, use_container_width=True, hide_index=True)

            with col_cause:
                top_causes = stats.get("top_root_causes", [])
                if top_causes:
                    st.subheader("🎯 常见根因")
                    for i, c in enumerate(top_causes, 1):
                        st.markdown(f"{i}. **[{c['count']}次]** {c['cause'][:100]}")

    except Exception as e:
        st.warning(f"加载统计数据失败: {e}")
