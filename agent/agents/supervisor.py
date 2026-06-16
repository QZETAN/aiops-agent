"""
Supervisor —— 多智能体诊断系统的调度中心。

职责：
  读取当前对话历史（messages），决定下一步调哪个 Expert，
  或者判断"证据足够，可以结案"。

设计要点：
  1. Few-Shot 示例是 Prompt 最重要的部分
  2. 输出格式只返回 JSON
  3. 10 步强制终止
"""
import json
import logging
import os

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

logger = logging.getLogger("supervisor")

llm = ChatOpenAI(
    model="deepseek-chat",
    api_key=os.environ["DEEPSEEK_API_KEY"],
    base_url="https://api.deepseek.com/v1",
    temperature=0.0,
)

# ==================== System Prompt ====================

SUPERVISOR_SYSTEM_PROMPT = """\
你是 AIOps 智能运维系统的调度指挥官（Supervisor）。

## 你的职责
根据当前的诊断进展，决定下一步应该找哪位专家。你只需要判断"现在最需要什么信息"，不需要亲自分析数据。

## 可调度的专家
- metrics_expert：指标分析专家（查 Prometheus：QPS、CPU、内存、错误率）
- logs_expert：日志分析专家（查 Loki：ERROR 日志、异常堆栈）
- traces_expert：调用链分析专家（查 Jaeger：错误 Span、慢请求定位）
- code_expert：代码变更专家（查 Git 提交历史，关联新上线的变更）

## 诊断策略（推荐的排查顺序）
1. **先看指标**（metrics_expert）：确认"有没有问题"
2. **再看日志**（logs_expert）：确认"什么问题"
3. **下钻调用链**（traces_expert）：确认"哪里出问题"
4. **最后查变更**（code_expert）：确认"为什么出问题"
5. 以上信息都收集齐全了，输出 FINISH

## 重要规则
- 严格按"指标→日志→调用链→变更"的顺序排查，不要跳步骤
- 每次只调一个专家，等它返回结果后再决定下一步
- **"无数据" ≠ "没问题"**：如果一个专家报告服务未接入监控、查不到数据，这不等于系统正常。继续按顺序调下一个专家
- 同一个专家最多调 1 次
- 只有（a）多个数据源都查过但全部无数据，或（b）已拿到足够的指标+日志+Trace 证据，才 FINISH
- 调了 8 步以上还没找到根因，也 FINISH

## 输出格式（严格遵守）
只输出一行 JSON，不要任何其他文字：
{"next_agent": "<专家名或FINISH>", "reason": "一句话说明为什么做这个决策"}
"""

# ==================== Few-Shot 示例 ====================

FEWSHOT_EXAMPLE_1_USER = """[告警] order-service 在 15:03 开始 5xx 错误率飙升到 25%"""

FEWSHOT_EXAMPLE_1_STEPS = [
    (
        '{"next_agent": "metrics_expert", "reason": "告警提到错误率飙升，先查指标确认异常范围"}',
        'metrics_expert 返回：15:03 CPU 从 30% 升至 92%，5xx 错误率从 0 升至 25%。指标确认异常。'
    ),
    (
        '{"next_agent": "logs_expert", "reason": "指标确认 15:03 异常，查日志看具体报错类型"}',
        'logs_expert 返回：15:03:12 出现 NullPointerException，位置 UserController.java:42，trace_id=abc123'
    ),
    (
        '{"next_agent": "traces_expert", "reason": "日志拿到 trace_id=abc123，下钻调用链确认根因 Span"}',
        'traces_expert 返回：trace_id=abc123 中 user-service 的 GET /user/1 Span 返回 500，确认为根因'
    ),
    (
        '{"next_agent": "FINISH", "reason": "已拿到指标异常+错误日志+出错 Trace，三源交叉验证，证据充分"}',
        ''
    ),
]

FEWSHOT_EXAMPLE_2_USER = """[告警] gateway-service 整体响应延迟 P99 超过 3 秒"""

FEWSHOT_EXAMPLE_2_STEPS = [
    (
        '{"next_agent": "metrics_expert", "reason": "告警提到延迟升高，先查 P99 延迟和 QPS"}',
        'metrics_expert 返回：P99 从 200ms 升至 3500ms，QPS 无变化，CPU 正常。延迟异常但资源正常。'
    ),
    (
        '{"next_agent": "traces_expert", "reason": "指标显示延迟异常但 CPU/QPS 正常，可能是下游变慢，查调用链"}',
        'traces_expert 返回：trace xyz789 中 order-service 的 Span 耗时 4800ms，远超正常值 150ms'
    ),
    (
        '{"next_agent": "logs_expert", "reason": "调用链确认 order-service 慢，查其日志看原因"}',
        'logs_expert 返回：order-service 日志显示 "Connection timeout to user-service"'
    ),
    (
        '{"next_agent": "FINISH", "reason": "完整链路：指标延迟→order慢→超时日志，根因定位完毕"}',
        ''
    ),
]

FEWSHOT_EXAMPLE_3_USER = """[告警] user-service CPU 突然飙升到 95%，大量请求超时"""

FEWSHOT_EXAMPLE_3_STEPS = [
    (
        '{"next_agent": "metrics_expert", "reason": "告警提到 CPU 飙升，先查 CPU 和 QPS 确认异常"}',
        'metrics_expert 返回：Prometheus 中只有自身指标，user-service 未接入 Prometheus，无指标数据'
    ),
    (
        '{"next_agent": "logs_expert", "reason": "Prometheus 没有 user-service 数据，但告警说有异常。继续查日志"}',
        'logs_expert 返回：15:03 大量 "OutOfMemoryError: Java heap space"，伴随 "Connection timeout"'
    ),
    (
        '{"next_agent": "traces_expert", "reason": "日志确认 OOM，查调用链看超时分布"}',
        'traces_expert 返回：user-service 的 /user/{id} Span 大量超时（>30000ms）'
    ),
    (
        '{"next_agent": "FINISH", "reason": "虽然 Prometheus 无数据，但日志+调用链交叉验证指向 user-service OOM 导致超时"}',
        ''
    ),
]


def _build_fewshot_messages() -> list:
    """将 Few-Shot 示例构建为 LangChain Message 列表。"""
    messages = []

    messages.append(HumanMessage(content=f"[示例1开始 - 学习错误率升高的诊断步骤]\n\n{FEWSHOT_EXAMPLE_1_USER}"))
    for i, (supervisor_json, expert_response) in enumerate(FEWSHOT_EXAMPLE_1_STEPS):
        messages.append(AIMessage(content=supervisor_json))
        if expert_response:
            messages.append(HumanMessage(content=f"[{['metrics','logs','traces','code'][min(i,3)]}_expert 返回]\n{expert_response}"))
    messages.append(HumanMessage(content="[示例1结束]"))

    messages.append(HumanMessage(content=f"[示例2开始 - 学习延迟故障的诊断路径]\n\n{FEWSHOT_EXAMPLE_2_USER}"))
    for i, (supervisor_json, expert_response) in enumerate(FEWSHOT_EXAMPLE_2_STEPS):
        messages.append(AIMessage(content=supervisor_json))
        if expert_response:
            messages.append(HumanMessage(content=f"[{['metrics','traces','logs','code','FINISH'][min(i,4)]}_expert 返回]\n{expert_response}"))
    messages.append(HumanMessage(content="[示例2结束]"))

    messages.append(HumanMessage(content=f"[示例3开始 - 学习当一个数据源无数据时如何处理]\n\n{FEWSHOT_EXAMPLE_3_USER}"))
    for i, (supervisor_json, expert_response) in enumerate(FEWSHOT_EXAMPLE_3_STEPS):
        messages.append(AIMessage(content=supervisor_json))
        if expert_response:
            messages.append(HumanMessage(content=f"[{['metrics','logs','traces','code','FINISH'][min(i,4)]}_expert 返回]\n{expert_response}"))
    messages.append(HumanMessage(content="[示例3结束]"))

    return messages


def supervisor_node(state: dict) -> dict:
    """Supervisor 节点的核心逻辑。"""
    iteration = state.get("iteration_count", 0)
    logger.info(f"Supervisor 第 {iteration + 1} 轮调度")

    full_messages = [
        SystemMessage(content=SUPERVISOR_SYSTEM_PROMPT),
        *_build_fewshot_messages(),
        *state["messages"],
    ]

    response = llm.invoke(full_messages)
    content = response.content.strip()
    logger.info(f"Supervisor 原始输出: {content[:200]}")

    try:
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        parsed = json.loads(content)
        next_agent = parsed.get("next_agent", "FINISH")
        reason = parsed.get("reason", "")
    except json.JSONDecodeError:
        logger.warning(f"Supervisor 返回非法 JSON，默认 FINISH: {content[:300]}")
        next_agent = "FINISH"
        reason = "JSON 解析失败，强制结束"

    valid_agents = {"metrics_expert", "logs_expert", "traces_expert", "code_expert", "FINISH", ""}
    if next_agent not in valid_agents:
        logger.warning(f"Supervisor 返回非法 agent: {next_agent}，改为 FINISH")
        next_agent = "FINISH"
        reason = f"非法路由目标 '{next_agent}'，强制结束"

    logger.info(f"Supervisor 决策: next_agent={next_agent}, reason={reason}")

    return {
        "messages": [AIMessage(content=f"[Supervisor] 下一步: {next_agent}，原因: {reason}")],
        "next_agent": next_agent,
        "iteration_count": iteration + 1,
    }
