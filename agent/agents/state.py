"""
AgentState —— 多智能体系统的共享状态定义。

所有节点（Supervisor、Expert、推理、反思）通过读写同一份 AgentState 来协作。

设计原则：
  1. messages 用 add_messages reducer —— 各节点追加消息，不覆盖
  2. evidence 存结构化证据 —— 推理节点靠它拼报告
  3. iteration_count 是安全阀 —— 防止 LLM 无限循环烧 token
  4. reflection_round 是反思的安全阀 —— 最多反思 2 轮
"""
from typing import TypedDict, Annotated

from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    """
    多智能体诊断系统的全局状态。

    字段说明：
      messages:
          完整的对话历史。add_messages 注解确保各节点追加而非覆盖。

      next_agent:
          Supervisor 节点的路由输出。
          合法值：
            ""               → 初始空值
            "metrics_expert" → 调指标专家
            "logs_expert"    → 调日志专家
            "traces_expert"  → 调调用链专家
            "code_expert"    → 调代码变更专家
            "FINISH"         → 停止调度，进入推理节点

      intermediate_steps:
          工具调用的原始记录列表。

      evidence:
          结构化的关键证据，dict。key 是证据名，value 是总结。

      iteration_count:
          Supervisor 调度轮数，从 0 开始，达到 10 强制终止。

      reflection_round:
          反思轮数，从 0 开始。最多 2 轮。
    """
    messages: Annotated[list, add_messages]
    next_agent: str
    intermediate_steps: list
    evidence: dict
    iteration_count: int
    reflection_round: int
