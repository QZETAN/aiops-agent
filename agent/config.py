"""
统一配置模块 —— AIOPS Agent 所有配置的单一数据源。

===========================================================================
为什么要有这个模块？
===========================================================================

之前的问题：
  LLM 配置（model / base_url / api_key / temperature）在 4 个文件里
  各自独立创建 ChatOpenAI 实例，一模一样的代码复制了 4 遍：
    - agent/agents/supervisor.py 第 22-27 行
    - agent/agents/experts.py    第 17-22 行
    - agent/agents/reflect.py    第 22-27 行
    - agent/agents/graph.py      第 38-43 行

  后果：改一个配置要改 4 个地方，改漏一个行为就不一致。
        想换 LLM 厂商（比如从 DeepSeek 换到 OpenAI）要改 4 个文件。

现在的方案：
  全局只有一个地方定义 LLM 配置 → agent/config.py
  所有模块通过 create_llm() 获取 LLM 实例 → 配置统一、行为一致

===========================================================================
设计原则
===========================================================================

1. 单一数据源（Single Source of Truth）
   所有配置项只在这里定义一次，其他模块通过 import 或函数调用获取。

2. 启动时校验（Fail Fast）
   必需的配置缺失时立即报错，给出明确的修复指引。
   不在运行到一半时才崩溃。

3. 12-Factor App
   配置通过环境变量注入，不与代码耦合。
   .env 文件辅助本地开发，不进入版本控制。

4. 无静默 Fallback
   后端地址不提供默认值。未配置时由工具层返回友好错误，
   而不是静默连到一个不存在的地址。

===========================================================================
配置项速查表
===========================================================================

  DEEPSEEK_API_KEY    必需  LLM API 密钥，如 sk-xxx
  LLM_MODEL           可选  模型名，默认 deepseek-chat
  LLM_BASE_URL        可选  API 地址，默认 https://api.deepseek.com/v1
  LLM_TEMPERATURE     可选  温度参数，默认 0.0

  PROMETHEUS_URL      可选  Prometheus 地址，如 http://192.168.101.100:9090
  LOKI_URL            可选  Loki 地址，如 http://192.168.101.100:3100
  JAEGER_URL          可选  Jaeger 地址，如 http://192.168.101.100:16686

  TOOL_TIMEOUT        可选  HTTP 请求超时（秒），默认 30
  MAX_ITERATIONS      可选  Supervisor 最大调度轮数，默认 10
  MAX_REFLECTIONS     可选  最大反思轮数，默认 2
  MIN_CONFIDENCE      可选  最低置信度阈值，默认 0.7
  MAX_TOKENS_PER_DIAGNOSIS  可选  单次诊断最大 token 量，默认 0=不限
"""

import os
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("aiops.config")

# ============================================================================
# 加载 .env 文件（本地开发辅助，生产环境通过 K8s ConfigMap/Secret 注入）
# ============================================================================

_PROJECT_ROOT = Path(__file__).parent.parent.resolve()  # agent/ → 项目根


def _load_dotenv() -> None:
    """尝试加载项目根目录的 .env 文件。python-dotenv 未安装时静默跳过。"""
    env_path = _PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv(env_path)
        logger.info("已加载环境变量文件: %s", env_path)
    except ImportError:
        pass  # python-dotenv 是可选的，没有也不影响生产环境


_load_dotenv()


# ============================================================================
# 工具函数
# ============================================================================


class ConfigError(RuntimeError):
    """配置缺失时抛出的异常，包含明确的修复指引。"""


def _require(key: str, example: str = "") -> str:
    """
    读取必需的环境变量。缺失时抛出 ConfigError，不给默认值。

    Args:
        key:     环境变量名，如 "DEEPSEEK_API_KEY"
        example: 示例值，展示在错误信息中，如 "sk-xxxxxxxx"

    Raises:
        ConfigError: 环境变量未设置时
    """
    val = os.environ.get(key)
    if val:
        return val

    msg = f"缺少必需环境变量: {key}"
    if example:
        msg += f"\n  示例: set {key}={example}"
    msg += "\n  提示: 可在项目根目录创建 .env 文件（参考 .env.example）"
    raise ConfigError(msg)


def _get(key: str, default: str = "") -> str:
    """读取可选的环境变量，未设置时返回 default。"""
    return os.environ.get(key, default)


# ============================================================================
# LLM 配置
# ============================================================================

# 必需：没有 API Key 什么都干不了，启动即报错
LLM_API_KEY = _require("DEEPSEEK_API_KEY", "sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")

# 可选：有合理默认值，大多数情况不需要改
LLM_MODEL = _get("LLM_MODEL", "deepseek-chat")
LLM_BASE_URL = _get("LLM_BASE_URL", "https://api.deepseek.com/v1")
LLM_TEMPERATURE = float(_get("LLM_TEMPERATURE", "0.0"))

# 缓存的 LLM 实例（避免每次 create_llm() 都 new 对象）
_llm_instance: Optional[object] = None


def create_llm(temperature: Optional[float] = None):
    """
    创建（或复用）带自动重试的 LLM 实例。

    所有 Agent 节点统一通过此函数获取 LLM，不再各自创建 ChatOpenAI。

    **Phase 3 新增**：自动重试机制。
      - 当 LLM API 返回 429（限流）、503（服务不可用）、连接超时等可恢复错误时
      - 自动重试最多 3 次，指数退避（2s → 4s → 8s）
      - 不可恢复的错误（如 401 认证失败）不重试，直接抛出

    为什么用工厂函数而不是模块级全局变量？
      - 模块级变量在 import 时立即初始化
      - 工厂函数延迟到第一次调用才创建，不影响 import 速度
      - 也方便将来支持按节点类型给不同 temperature

    切换 LLM 厂商只需改环境变量：
      DeepSeek  → LLM_BASE_URL=https://api.deepseek.com/v1
      OpenAI    → LLM_BASE_URL=https://api.openai.com/v1    LLM_MODEL=gpt-4o
      Ollama    → LLM_BASE_URL=http://localhost:11434/v1    LLM_MODEL=llama3
    """
    from langchain_openai import ChatOpenAI

    global _llm_instance
    if _llm_instance is None:
        base_llm = ChatOpenAI(
            model=LLM_MODEL,
            api_key=LLM_API_KEY,
            base_url=LLM_BASE_URL,
            temperature=temperature if temperature is not None else LLM_TEMPERATURE,
        )
        _llm_instance = _RetryableLLM(base_llm)
    return _llm_instance


# ============================================================================
# LLM 重试包装器（Phase 3）
# ============================================================================

class _RetryableLLM:
    """
    LLM 调用自动重试包装器。

    重试条件：
      - RateLimitError (429)：API 限流，等一会儿通常能恢复
      - APITimeoutError / ConnectError：网络波动
      - APIStatusError 且状态码 >= 500：服务端临时故障

    不重试：
      - AuthenticationError (401)：API Key 错了，重试没用
      - BadRequestError (400)：请求本身就错了，重试也错
    """

    def __init__(self, llm):
        self._llm = llm

    def __getattr__(self, name):
        """将所有未匹配的属性访问委托给底层 LLM（如 bind_tools）。"""
        return getattr(self._llm, name)

    def invoke(self, messages):
        """调用 LLM，自动重试可恢复错误。"""
        from tenacity import (
            retry,
            stop_after_attempt,
            wait_exponential,
            retry_if_exception_type,
        )

        @retry(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=2, max=30),
            retry=retry_if_exception_type(_RETRYABLE_EXCEPTIONS),
            reraise=True,
            before_sleep=lambda rs: logger.warning(
                "LLM 调用失败（第 %d/%d 次），%ds 后重试: %s",
                rs.attempt_number,
                3,
                rs.next_action.sleep if rs.next_action else 0,
                rs.outcome.exception() if rs.outcome else "未知错误",
            ),
        )
        def _do_invoke():
            result = self._llm.invoke(messages)
            # 从返回消息中提取 token 用量并记录
            try:
                usage = getattr(result, "response_metadata", {}).get("token_usage", {})
                if usage:
                    logger.debug(
                        "LLM token: input=%d output=%d total=%d",
                        usage.get("prompt_tokens", 0),
                        usage.get("completion_tokens", 0),
                        usage.get("total_tokens", 0),
                    )
            except Exception:
                pass  # token 统计失败不影响主流程
            return result

        return _do_invoke()


def _is_retryable(exception: Exception) -> bool:
    """判断异常是否可重试。"""
    try:
        import httpx
        from openai import (
            RateLimitError,
            APITimeoutError,
            APIConnectionError,
            InternalServerError,
        )

        # OpenAI/DeepSeek SDK 定义的重试类异常
        if isinstance(exception, (RateLimitError, APITimeoutError, APIConnectionError, InternalServerError)):
            return True

        # httpx 网络层异常
        if isinstance(exception, (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError)):
            return True
    except ImportError:
        pass

    # 兜底：检查错误信息中的关键字
    msg = str(exception).lower()
    retryable_keywords = ("429", "503", "502", "504", "timeout", "connection", "rate limit", "too many requests")
    return any(kw in msg for kw in retryable_keywords)


# tenacity 需要的异常类型元组
try:
    import httpx
    from openai import RateLimitError, APITimeoutError, APIConnectionError, InternalServerError

    _RETRYABLE_EXCEPTIONS = (
        RateLimitError,
        APITimeoutError,
        APIConnectionError,
        InternalServerError,
        httpx.ConnectError,
        httpx.TimeoutException,
        httpx.RemoteProtocolError,
    )
except ImportError:
    _RETRYABLE_EXCEPTIONS = Exception  # 兜底：全部重试


# ============================================================================
# 监控后端地址
# ============================================================================
#
# 设计决策：这些 URL 不设默认值（空字符串）。
#
# 为什么不用 localhost 做默认值？
#   localhost 在 Docker 环境里指向容器自身，不是宿主机。
#   给一个大概率不对的默认值，比不给默认值更危险——用户以为配置好了，
#   实际连不上，查半天才发现是默认值不对。
#
# 为什么不保留 192.168.101.100 作为默认值？
#   那是你自己虚拟机的 IP，别人 clone 下来不可能连得上。
#   开源项目的默认值不能绑定某个人的开发环境。
#
# 怎么办？
#   开发环境：在 .env 文件里配好，项目根目录的 .env 会被自动加载
#   生产环境：通过 K8s ConfigMap / docker-compose environment 注入

PROMETHEUS_URL = _get("PROMETHEUS_URL", "")
LOKI_URL = _get("LOKI_URL", "")
JAEGER_URL = _get("JAEGER_URL", "")

# ============================================================================
# 超时与限制（有合理默认值，一般不需要改）
# ============================================================================

REQUEST_TIMEOUT = float(_get("TOOL_TIMEOUT", "30.0"))
MAX_ITERATIONS = int(_get("MAX_ITERATIONS", "10"))
MAX_REFLECTIONS = int(_get("MAX_REFLECTIONS", "2"))
MIN_CONFIDENCE = float(_get("MIN_CONFIDENCE", "0.7"))

# ============================================================================
# Git 仓库路径（可选，不配就不查代码变更）
# ============================================================================

GIT_REPO_PATH = _get("GIT_REPO_PATH", "")

# ============================================================================
# 成本控制（Phase 3 用）
# ============================================================================

MAX_TOKENS_PER_DIAGNOSIS = int(_get("MAX_TOKENS_PER_DIAGNOSIS", "0"))
