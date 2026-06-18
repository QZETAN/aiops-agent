"""
Reflect —— 反思节点，在推理报告置信度不足时触发补充查询。

职责：
  1. 检查推理节点输出的根因报告置信度
  2. 如果置信度 < 0.7，分析"还缺什么信息"，生成补充查询计划
  3. 将计划追加到 messages，回到 Supervisor 重新调度

设计原则：
  1. 反思不是"重来一遍"——是带着明确目标的定向补充
  2. 最多反思 2 轮（reflection_round >= 2 时跳过）

重构说明（Phase 1）：
  - LLM 从 agent.config.create_llm() 获取，不再硬编码 ChatOpenAI
  - JSON 清理使用 agent.utils.clean_json_response
  - messages 格式化使用 agent.utils.format_messages_for_log
"""
import json
import logging

from langchain_core.messages import HumanMessage, SystemMessage

from agent.config import create_llm, MIN_CONFIDENCE as _CFG_MIN_CONFIDENCE, MAX_REFLECTIONS as _CFG_MAX_REFLECTIONS
from agent.utils import clean_json_response, format_messages_for_log

logger = logging.getLogger("aiops.reflect")

REFLECT_SYSTEM_PROMPT = """\
你是 AIOps 智能运维系统的诊断质量审查员。推理节点已经生成了一份根因报告，但置信度不够高。

## 你的任务
1. 分析为什么置信度不够——是哪个数据源缺失？哪个排查步骤被跳过了？
2. 生成不超过 3 条补充查询指令，每条指令告诉 Supervisor 具体需要什么信息

## 输出格式（严格遵守）
{
  "confidence_gap_analysis": "一句话说明当前证据链的缺口是什么",
  "supplementary_tasks": [
    {"target_expert": "metrics_expert", "instruction": "查 <服务名> 的 CPU 和内存趋势"}
  ],
  "note": "这是第 N 轮反思"
}
"""


def _extract_confidence_from_report(messages: list) -> float:
    """从 messages 中找到推理节点的报告，提取置信度。"""
    for msg in reversed(messages):
        if not hasattr(msg, 'content'):
            continue
        content = msg.content
        if not isinstance(content, str):
            continue
        try:
            report = json.loads(content)
            root_causes = report.get("root_causes", [])
            if root_causes:
                return float(root_causes[0].get("confidence", 0.5))
        except (json.JSONDecodeError, ValueError, TypeError):
            continue
    return 0.5


def reflect_node(state: dict) -> dict:
    """反思节点（Reflect Node）。"""
    reflection_round = state.get("reflection_round", 0)
    messages = state["messages"]

    logger.info("反思节点：第 %d 轮反思检查...", reflection_round + 1)

    if reflection_round >= _CFG_MAX_REFLECTIONS:
        logger.info("反思节点：已达 %d 轮上限，跳过反思", _CFG_MAX_REFLECTIONS)
        return {"reflection_round": reflection_round + 1}

    confidence = _extract_confidence_from_report(messages)
    logger.info("反思节点：当前报告置信度 = %s", confidence)

    if confidence >= _CFG_MIN_CONFIDENCE:
        logger.info("反思节点：置信度 >= %.1f，无需反思", _CFG_MIN_CONFIDENCE)
        return {}

    logger.info("反思节点：置信度 %s < %.1f，生成补充查询计划...", confidence, _CFG_MIN_CONFIDENCE)

    reflect_messages = [
        SystemMessage(content=REFLECT_SYSTEM_PROMPT),
        HumanMessage(content=f"""推理节点输出的置信度只有 {confidence}，请分析证据缺口并生成补充计划。

当前诊断轮数：已执行 {state.get('iteration_count', 0)} 轮
已反思轮数：第 {reflection_round + 1} 轮

以下是完整的诊断对话历史：
---
{format_messages_for_log(messages)}
---
"""),
    ]

    try:
        llm = create_llm()  # ← 统一从 config 获取
        response = llm.invoke(reflect_messages)
        # 使用统一的 JSON 清理函数
        content = clean_json_response(response.content)
        plan = json.loads(content)
        logger.info("反思节点：补充计划生成完成，%d 条任务", len(plan.get("supplementary_tasks", [])))
    except (json.JSONDecodeError, Exception) as e:
        logger.warning("反思节点异常，使用默认计划: %s", e)
        plan = {
            "confidence_gap_analysis": "证据不足，需要补充日志和调用链数据",
            "supplementary_tasks": [
                {"target_expert": "logs_expert", "instruction": "检查所有相关服务的 ERROR 日志"},
                {"target_expert": "traces_expert", "instruction": "检查最近的错误 Trace"},
            ],
            "note": f"自动生成（原因：{e}）",
        }

    tasks_text = "\n".join(
        f"  {i+1}. 调 {t['target_expert']}：{t['instruction']}"
        for i, t in enumerate(plan.get("supplementary_tasks", []))
    )

    reflect_message = HumanMessage(content=f"""[反思节点 - 第 {reflection_round + 1} 轮补充查询]

当前置信度：{confidence}，不满足 0.7 阈值。

证据缺口：{plan.get('confidence_gap_analysis', '')}

补充查询计划：
{tasks_text}

请 Supervisor 按顺序调度这些专家，收集缺失的信息。""")

    return {
        "messages": [reflect_message],
        "reflection_round": reflection_round + 1,
    }


# 注意：原来的 _format_messages_for_reflect() 已迁移到 agent.utils.format_messages_for_log()
# 所有模块共用同一个实现，不再各自复制。
