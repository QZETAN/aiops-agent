"""
AIOps Agent —— 基于 LangGraph 多智能体的智能运维故障诊断系统。

自动通过 Prometheus / Loki / Jaeger / Git 四类 MCP 工具
采集可观测性数据，由 Supervisor 调度 4 个专业 Expert Agent
协同排查，最终生成带置信度的根因分析报告。
"""
