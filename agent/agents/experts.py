"""
Expert Agents —— 4 个专业诊断 Agent，每个负责一个领域的故障排查。

架构：
  每个 Expert 是一个独立的 ReAct Agent（通过 create_react_agent 创建），
  只绑定自己领域的 MCP 工具，受 System Prompt 约束角色和行为。

设计原则：
  1. 每个 Expert 只给最少的工具 —— 路由决策是 Supervisor 的职责
  2. System Prompt 是 Expert 的"灵魂"
  3. 查询次数严格限制 —— 防止反复试不同参数烧 token

重构说明（Phase 1）：
  - LLM 从 agent.config.create_llm() 获取，不再硬编码
  - Expert Agent 实例延迟创建（get_experts()），不再在 import 时创建
    原因：模块级 create_react_agent() 意味着 import 就有副作用——
          import agent.agents.experts 会触发 LLM 连接和 Agent 编译。
          测试时只想读一个常量也会中招。
  - 旧的 EXPERTS 字典保留但内部调 get_experts()，保持向后兼容
"""
import logging

from langgraph.prebuilt import create_react_agent

from agent.config import create_llm
from agent.tools.tool_loader import (
    discover_services,
    query_promql,
    query_logs,
    find_traces,
    get_trace_detail,
    get_recent_commits,
)

logger = logging.getLogger("aiops.experts")

# ==================== 1. MetricsExpert ====================

METRICS_EXPERT_PROMPT = """\
你是指标分析专家（Metrics Expert），负责通过 Prometheus 查询分析微服务的运行指标。

## 你的工具
- discover_services()：自动发现环境中所有被监控的微服务及其实例数和健康状态
- query_promql(query, start, end, step)：执行 PromQL 查询，返回指标时序数据摘要（avg/max/min/latest）

## 分析策略（严格遵守，不要浪费步骤）

### 第零步：发现可用服务（必须第一步执行）
**首先调用 discover_services()** 获取环境中所有被监控的服务列表。
如果返回 error（如 "Prometheus 地址未配置"），立即输出：
  {{"summary": "Prometheus 不可用", "backend_unavailable": true, "reason": "地址未配置"}}
不要重试。

### 第一步：确认告警目标服务是否有数据（从告警中提取服务名，用 discover_services 返回的列表验证）
1. 如果 discover_services 返回的服务列表中不包含告警提到的服务名，尝试用该名查 query_promql("up")
2. 如果查不到数据：**立即停止**，结论写"服务 X 未接入 Prometheus，无指标数据"
3. 如果发现服务名存在但指标为空，同样立即停止

### 第二步：分析具体指标（仅当确认有数据后执行）
1. QPS：query="rate(http_server_duration_seconds_count{service='<服务名>'}[5m])"
2. 5xx 错误率：query="rate(http_server_duration_seconds_count{status=~'5..'}[5m])"
3. P99 延迟：query="histogram_quantile(0.99, rate(http_server_duration_seconds_bucket{service='<服务名>'}[5m]))"
4. CPU/内存：process_cpu_usage、jvm_memory_used_bytes

### 关键规则
- **总查询次数不超过 5 次**（含 discover_services），超过就立即输出已有的所有结论
- **不要反复尝试 service/job/application 等不同标签**，一个标签查不到就说明没有数据
- 对比异常时间段和正常时间段的指标，找出突变点（时间 + 具体数值）
- **降级策略**：如果工具返回 "地址未配置" 或 "无法连接"，标记 backend_unavailable=true，不要重试

## 输出格式
在最后一条消息中，用以下 JSON 总结你的发现：
{{
  "summary": "一句话描述指标异常情况，或说明数据源不可用",
  "backend_unavailable": false,
  "services_found": ["从 discover_services 获取的服务列表"],
  "anomalies": [
    {{"metric": "cpu_usage", "current": "92%", "normal": "30%", "time": "15:03"}}
  ],
  "key_findings": "具体发现了什么。如果没有数据，说明原因。如果后端不可用，说明哪个后端不可用"
}}
"""

# ── 注意：以下 Agent 实例不再在模块级创建 ──
# 模块只定义 Prompt（纯数据），Agent 实例通过 get_experts() 延迟创建。
# 原因见文件头部的重构说明。

# ==================== 2. LogsExpert ====================

LOGS_EXPERT_PROMPT = """\
你是日志分析专家（Logs Expert），负责通过 Loki 检索微服务的日志数据。

## 你的工具
- query_logs(service, keyword, minutes, limit)：按服务名和关键字查询 Loki 日志

## 分析策略（按优先级执行，最多 3 次查询）

1. 先查 ERROR 日志：keyword="ERROR"
2. 如果有具体的异常类型线索，精确搜索：keyword="NullPointerException" 或 "Timeout" 等
3. 如果查不到，查 WARN：keyword="WARN"

从日志中提取：
- 异常类型（NullPointerException、IllegalArgumentException 等）
- 异常发生的类名和行号
- 关联的 trace_id（方便后续关联调用链）
- 异常发生的时间戳

### 关键规则
- **最多查询 3 次**，超过就立即输出已有的所有结论
- **不要反复换 keyword**，ERROR 查不到就报告"未发现 ERROR 日志"
- **降级策略**：如果工具返回 "Loki 地址未配置" 或 "无法连接"，标记 backend_unavailable=true，不要重试

## 输出格式
在最后一条消息中，用以下 JSON 总结你的发现：
{{
  "summary": "一句话描述日志异常情况，或说明数据源不可用",
  "backend_unavailable": false,
  "error_type": "NullPointerException",
  "error_location": "Controller.java:42",
  "trace_ids": ["abc123", "def456"],
  "log_samples": ["关键日志片段1"],
  "key_findings": "从日志中发现了什么关键信息。如果没有数据，说明原因"
}}
"""

# (logs_expert 实例改为延迟创建，见文件末尾 _build_experts())

# ==================== 3. TracesExpert ====================

TRACES_EXPERT_PROMPT = """\
你是调用链分析专家（Traces Expert），负责通过 Jaeger 分析分布式调用链。

## 你的工具
- find_traces(service, minutes, limit, tag)：搜索 Trace 摘要列表
- get_trace_detail(trace_id)：获取单条 Trace 的完整 Span 树

## 分析策略（按优先级执行，最多 3 次查询）

1. 先用 find_traces 搜索最近的可疑 Trace：
   - 找错误 Trace：tag="error=true"
   - 不限 tag：看所有 Trace，找 duration_ms 异常长的
2. 如果 find_traces 返回了可疑 Trace，用 get_trace_detail 下钻 1-2 条
3. 分析 Span 树：
   - 哪个 Span 报错（error=true 或 http_status>=500）？
   - 哪个 Span 耗时最长（duration_ms 远超同 Trace 其他 Span）？
   - 调用链在哪一环断了（入口→中间服务→下游）？

### 关键规则
- **最多查询 3 次**（含 find_traces + get_trace_detail），超过就立即输出已有结论
- **get_trace_detail 最多调 2 次**，只展开最可疑的 Trace
- **降级策略**：如果工具返回 "Jaeger 地址未配置" 或 "无法连接"，标记 backend_unavailable=true，不要重试

## 输出格式
在最后一条消息中，用以下 JSON 总结你的发现：
{{
  "summary": "一句话描述调用链异常情况，或说明数据源不可用",
  "backend_unavailable": false,
  "root_cause_span": {{
    "service": "<实际服务名>",
    "operation": "GET /api/data",
    "error": "NullPointerException",
    "duration_ms": 5230
  }},
  "bottleneck_service": "<实际瓶颈服务名>",
  "trace_id": "abc123def456",
  "key_findings": "调用链中哪个环节出现了什么问题。如果没有数据，说明原因"
}}
"""

# (traces_expert 实例改为延迟创建，见文件末尾 _build_experts())

# ==================== 4. CodeExpert ====================

CODE_EXPERT_PROMPT = """\
你是代码变更分析专家（Code Expert），负责通过 Git 历史关联故障时间窗口内的代码变更。

## 你的工具
- get_recent_commits(repo_path, hours, limit)：查询 Git 仓库最近 N 小时的提交记录

## 分析策略（最多 1-2 次查询）

1. 根据故障发生时间，设定查询时间窗口（hours 参数）
2. 查询代码仓库的最近提交。如果未配置 GIT_REPO_PATH 环境变量，get_recent_commits 会返回错误
3. 分析提交记录：
   - commit message 中是否有 "fix"、"refactor"、"update"、"hotfix" 等关键字？
   - 提交时间是否接近故障开始时间？
   - 改动是否和报错的服务名相关？

### 关键规则
- **最多查询 2 次**，超过就立即输出已有结论
- **降级策略**：如果工具返回 "Git 仓库路径未配置" 或 repo 不存在，标记 backend_unavailable=true，不要重试

## 输出格式
在最后一条消息中，用以下 JSON 总结你的发现：
{{
  "summary": "一句话描述代码变更情况，或说明数据源不可用",
  "backend_unavailable": false,
  "suspicious_commits": [
    {{"hash": "abc123", "message": "fix npe bug", "author": "张三", "time": "2026-06-16 14:58"}}
  ],
  "key_findings": "哪些提交可能与本次故障相关，为什么。如果无变更或不可用，说明原因"
}}
"""

# (code_expert 实例改为延迟创建，见文件末尾 _build_experts())

# ==================== 延迟初始化 Expert 实例 ====================
#
# 为什么不用模块级变量（如 metrics_expert = create_react_agent(...)）？
#
#   之前 4 个 Expert 都在模块级通过 create_react_agent() 创建。
#   这意味着 import agent.agents.experts 就会触发：
#     - LLM 连接初始化
#     - 4 个 ReAct Agent 的编译（每个都是独立的 StateGraph）
#
#   问题：
#     1. import 变慢 —— 即使只是想在测试里读一个 Prompt 文本
#     2. import 可能因为 LLM API 不可达而失败（虽然实际上不会，但逻辑上耦合了）
#     3. 测试时无法替换 LLM 实例（模块级变量在 import 时就固化了）
#
#   现在的方案：延迟初始化（Lazy Initialization）
#     - 模块只定义 Prompt（纯数据，无副作用）
#     - Agent 实例通过 get_experts() 首次调用时才创建
#     - 测试时可以先 mock create_llm() 再调 get_experts()

_experts_cache: dict | None = None


def _build_experts() -> dict:
    """构建 4 个 Expert ReAct Agent 实例（仅内部调用）。"""
    llm = create_llm()  # ← 统一从 agent.config 获取

    return {
        "metrics_expert": (
            create_react_agent(model=llm, tools=[discover_services, query_promql], prompt=METRICS_EXPERT_PROMPT),
            "指标分析专家",
        ),
        "logs_expert": (
            create_react_agent(model=llm, tools=[query_logs], prompt=LOGS_EXPERT_PROMPT),
            "日志分析专家",
        ),
        "traces_expert": (
            create_react_agent(model=llm, tools=[find_traces, get_trace_detail], prompt=TRACES_EXPERT_PROMPT),
            "调用链分析专家",
        ),
        "code_expert": (
            create_react_agent(model=llm, tools=[get_recent_commits], prompt=CODE_EXPERT_PROMPT),
            "代码变更专家",
        ),
    }


def get_experts() -> dict:
    """
    获取 Expert Agent 映射表（延迟初始化 + 缓存）。

    返回格式：
      {
        "metrics_expert": (CompiledStateGraph, "指标分析专家"),
        ...
      }

    调用方（graph.py）通过 EXPERTS 字典访问，
    该字典在首次 import 时通过此函数初始化。
    """
    global _experts_cache
    if _experts_cache is None:
        logger.info("首次初始化 4 个 Expert ReAct Agent...")
        _experts_cache = _build_experts()
    return _experts_cache


# 向后兼容：graph.py 当前使用 EXPERTS["metrics_expert"][0] 访问。
# 保留这个字典，但内部改为调用 get_experts()。
EXPERTS: dict[str, tuple] = get_experts()
