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
"""
import json
import logging
import os

from langgraph.graph import StateGraph, END
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

from agent.agents.state import AgentState
from agent.agents.supervisor import supervisor_node
from agent.agents.experts import EXPERTS
from agent.agents.reflect import reflect_node, _extract_confidence_from_report

logger = logging.getLogger("graph")

# ==================== LLM（推理节点专用） ====================

_infer_llm = ChatOpenAI(
    model="deepseek-chat",
    api_key=os.environ["DEEPSEEK_API_KEY"],
    base_url="https://api.deepseek.com/v1",
    temperature=0.0,
)

# ==================== 常量 ====================

MAX_ITERATIONS = 10      # Supervisor 最大调度轮数
MAX_REFLECTIONS = 2      # 最大反思轮数
MIN_CONFIDENCE = 0.7     # 最低置信度阈值

# 合法的 Expert 名列表
_VALID_AGENTS = ["metrics_expert", "logs_expert", "traces_expert", "code_expert"]

# ==================== 路由函数 ====================


def _route_supervisor(state: AgentState) -> str:
    """Supervisor 之后的条件边路由。读 next_agent → 分发到对应 Expert 或 infer。"""
    next_agent = state.get("next_agent", "")
    iteration = state.get("iteration_count", 0)

    if iteration >= MAX_ITERATIONS:
        logger.warning(f"达到最大迭代次数 {MAX_ITERATIONS}，强制结束")
        return "infer"

    if next_agent in _VALID_AGENTS:
        logger.info(f"路由到: {next_agent}")
        return next_agent

    if next_agent == "FINISH":
        logger.info("路由到: infer（Supervisor 决定结案）")
        return "infer"

    logger.warning(f"非法 next_agent: '{next_agent}'，兜底到 infer")
    return "infer"


def _route_after_infer(state: AgentState) -> str:
    """推理节点之后的条件边路由。检查置信度和反思轮数，决定是否需要反思。"""
    reflection_round = state.get("reflection_round", 0)
    messages = state["messages"]

    if reflection_round >= MAX_REFLECTIONS:
        logger.info(f"反思已达 {MAX_REFLECTIONS} 轮上限，直接结束")
        return "end"

    confidence = _extract_confidence_from_report(messages)

    if confidence < MIN_CONFIDENCE:
        logger.info(f"置信度 {confidence} < {MIN_CONFIDENCE}，进入反思节点")
        return "reflect"

    logger.info(f"置信度 {confidence} >= {MIN_CONFIDENCE}，直接结束")
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
✅ 好："将 user-service CPU 限制从 1 核扩至 2 核"
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
        response = _infer_llm.invoke(full_messages)
        content = response.content.strip()

        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        try:
            report = json.loads(content)
            conf = report.get('root_causes', [{}])[0].get('confidence', 'N/A')
            logger.info(f"推理节点：报告生成完成，置信度={conf}")
        except json.JSONDecodeError:
            logger.warning("推理节点：LLM 返回非标准 JSON，保留原始内容")
            report = {"title": "故障诊断报告", "raw_output": content, "note": "LLM 输出非标准 JSON"}

    except Exception as e:
        logger.error(f"推理节点异常: {e}")
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
    logger.info("工作流图构建完成 ✅（含反思循环）")
    return compiled_graph
