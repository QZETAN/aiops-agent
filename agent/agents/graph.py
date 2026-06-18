"""
Graph —— 多智能体诊断工作流的图组装和编译。

完整数据流：
  入口 → supervisor（LLM 路由）
            ↓ 条件边：根据 next_agent 分发
       ┌────┼────┬────┬────┐
       ▼    ▼    ▼    ▼    ▼
    metrics logs traces code infer
       │    │    │     │    │
       └────┴────┴────┘    ▼
             │         (条件边: 置信度检查)
             ▼              │
         supervisor    ┌────┴────┐
                       ▼         ▼
                    reflect    END
                       │
                       ▼
                   supervisor（重新调度）

重构说明（Phase 1）：
  - LLM 从 agent.config.create_llm() 获取，不再模块级硬编码
  - JSON 清理使用 agent.utils.clean_json_response
  - 常量（MAX_ITERATIONS 等）从 agent.config 读取
"""
import json
import logging

from langgraph.graph import StateGraph, END
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

from agent.agents.state import AgentState
from agent.agents.supervisor import supervisor_node
from agent.agents.experts import EXPERTS
from agent.agents.reflect import reflect_node, _extract_confidence_from_report
from agent.config import (
    create_llm,
    MAX_ITERATIONS as _CFG_MAX_ITERATIONS,
    MAX_REFLECTIONS as _CFG_MAX_REFLECTIONS,
    MIN_CONFIDENCE as _CFG_MIN_CONFIDENCE,
)
from agent.utils import clean_json_response

logger = logging.getLogger("aiops.graph")

# ==================== 常量（从统一配置读取，可通过环境变量覆盖） ====================

MAX_ITERATIONS = _CFG_MAX_ITERATIONS       # Supervisor 最大调度轮数
MAX_REFLECTIONS = _CFG_MAX_REFLECTIONS     # 最大反思轮数
MIN_CONFIDENCE = _CFG_MIN_CONFIDENCE       # 最低置信度阈值

# 合法的 Expert 名列表
_VALID_AGENTS = ["metrics_expert", "logs_expert", "traces_expert", "code_expert"]

# ==================== 路由函数 ====================


def _route_supervisor(state: AgentState) -> str:
    """Supervisor 之后的条件边路由。读 next_agent → 分发到对应 Expert 或 infer。"""
    next_agent = state.get("next_agent", "")
    iteration = state.get("iteration_count", 0)

    if iteration >= MAX_ITERATIONS:
        logger.warning("达到最大迭代次数 %d，强制结束", MAX_ITERATIONS)
        return "infer"

    if next_agent in _VALID_AGENTS:
        logger.info("路由到: %s", next_agent)
        return next_agent

    if next_agent == "FINISH":
        logger.info("路由到: infer（Supervisor 决定结案）")
        return "infer"

    logger.warning("非法 next_agent: '%s'，兜底到 infer", next_agent)
    return "infer"


def _route_after_infer(state: AgentState) -> str:
    """推理节点之后的条件边路由。检查置信度和反思轮数，决定是否需要反思。"""
    reflection_round = state.get("reflection_round", 0)
    messages = state["messages"]

    if reflection_round >= MAX_REFLECTIONS:
        logger.info("反思已达 %d 轮上限，直接结束", MAX_REFLECTIONS)
        return "end"

    confidence = _extract_confidence_from_report(messages)

    if confidence < MIN_CONFIDENCE:
        logger.info("置信度 %s < %s，进入反思节点", confidence, MIN_CONFIDENCE)
        return "reflect"

    logger.info("置信度 %s >= %s，直接结束", confidence, MIN_CONFIDENCE)
    return "end"


# ==================== 推理节点 ====================

_INFER_SYSTEM_PROMPT = """\
你是 AIOps 智能运维系统的根因分析专家。所有诊断数据已经收集完毕，现在你需要基于这些信息生成最终的根因分析报告。

## 你的任务
1. 综合分析所有专家（指标、日志、调用链、代码）的发现
2. 推断故障的根因，给出置信度
3. 列出支撑结论的关键证据
4. 提出具体的修复建议

## 输出格式（严格遵守，不要输出任何其他文字）
{
  "title": "故障诊断报告：<一句话摘要>",
  "root_causes": [
    {
      "rank": 1,
      "description": "<根因描述，越具体越好>",
      "confidence": 0.90,
      "evidence": [
        "<引用指标专家的具体发现>",
        "<引用日志专家的具体发现>",
        "<引用调用链专家的具体发现>"
      ],
      "fix_suggestion": "<具体的修复建议>"
    }
  ],
  "diagnosis_summary": "<200字以内的诊断过程总结>"
}

## 置信度评估标准
- 0.90-1.00：三个数据源（指标+日志+Trace）都明确指向同一根因
- 0.70-0.89：两个数据源指向同一根因
- 0.50-0.69：只有一个数据源有明确信号
- 0.30-0.49：多个数据源都查不到数据，只能根据告警描述推测
- 0.30 以下：完全无法判断，建议人工排查

## 修复建议要具体
❌ 不好："检查代码"
✅ 好："回滚 abc123def 提交（张三，2026-06-16 14:58）"

❌ 不好："扩容"
✅ 好："将 <服务名> CPU 限制从 1 核扩至 2 核"

## 置信度评估标准
- 0.90-1.00：三个数据源（指标+日志+Trace）都明确指向同一根因
- 0.70-0.89：两个数据源指向同一根因
- 0.50-0.69：只有一个数据源有明确信号
- 0.30-0.49：多个数据源都查不到数据，只能根据告警描述推测
- 0.30 以下：完全无法判断，建议人工排查

## 特别注意
- 如果专家报告中标记了 backend_unavailable=true，说明该数据源不可用，
  这是环境配置问题，不是故障根因。在报告中注明"XX 数据源不可用，诊断不完整"
"""


def _infer_node(state: AgentState) -> dict:
    """推理节点：汇总所有 Expert 的发现，生成根因报告。"""
    logger.info("推理节点：开始生成根因报告...")

    infer_messages = [
        SystemMessage(content=_INFER_SYSTEM_PROMPT),
        HumanMessage(content="""请基于以上所有专家诊断结果，生成最终的根因分析报告。

注意：
- 每个结论必须引用具体的证据（指标值、日志行、Trace ID）
- 置信度要根据证据充分程度如实评估。如果多数数据源无数据，置信度应该显著降低
- 修复建议要具体可操作
- 严格按 JSON 格式输出，不要输出其他内容"""),
    ]

    full_messages = [*state["messages"], *infer_messages]

    try:
        llm = create_llm()  # ← 统一从 config 获取
        response = llm.invoke(full_messages)
        content = clean_json_response(response.content)  # ← 统一 JSON 清理

        try:
            report = json.loads(content)
            conf = report.get("root_causes", [{}])[0].get("confidence", "N/A")
            logger.info("推理节点：报告生成完成，置信度=%s", conf)
        except json.JSONDecodeError:
            logger.warning("推理节点：LLM 返回非标准 JSON，保留原始内容")
            report = {"title": "故障诊断报告", "raw_output": content, "note": "LLM 输出非标准 JSON"}

    except Exception as e:
        logger.error("推理节点异常: %s", e)
        report = {"title": "诊断报告生成失败", "error": str(e)}

    return {
        "messages": [AIMessage(content=json.dumps(report, ensure_ascii=False, indent=2))],
    }


# ==================== Graph 构建 ====================

def build_graph() -> StateGraph:
    """构建并编译完整的多智能体诊断工作流图。"""
    logger.info("开始构建 LangGraph 工作流...")

    workflow = StateGraph(AgentState)

    workflow.add_node("supervisor", supervisor_node)
    workflow.add_node("metrics_expert", EXPERTS["metrics_expert"][0])
    workflow.add_node("logs_expert", EXPERTS["logs_expert"][0])
    workflow.add_node("traces_expert", EXPERTS["traces_expert"][0])
    workflow.add_node("code_expert", EXPERTS["code_expert"][0])
    workflow.add_node("infer", _infer_node)
    workflow.add_node("reflect", reflect_node)

    workflow.set_entry_point("supervisor")

    workflow.add_conditional_edges(
        "supervisor",
        _route_supervisor,
        {
            "metrics_expert": "metrics_expert",
            "logs_expert": "logs_expert",
            "traces_expert": "traces_expert",
            "code_expert": "code_expert",
            "infer": "infer",
        },
    )

    for agent_name in _VALID_AGENTS:
        workflow.add_edge(agent_name, "supervisor")

    workflow.add_conditional_edges(
        "infer",
        _route_after_infer,
        {
            "reflect": "reflect",
            "end": END,
        },
    )

    workflow.add_edge("reflect", "supervisor")

    compiled_graph = workflow.compile()
    logger.info("工作流图构建完成（含反思循环）")
    return compiled_graph
