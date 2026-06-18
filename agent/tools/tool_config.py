"""
MCP 工具配置兼容层。

===========================================================================
之前的问题：
  PROMETHEUS_URL / LOKI_URL / JAEGER_URL 各自在 os.getenv 里写了
  硬编码的 fallback 值 "http://192.168.101.100:9090" 等。
  这是你自己虚拟机的 IP，别人 clone 下来连不上，也不知道该改哪里。

现在的方案：
  所有配置统一由 agent.config 管理。本模块只是兼容层，从 config 导入后
  重新导出，保证 MCP Server 的 import 路径不变。

  如果 agent.config 导入失败（比如在没 pip install 的情况下直接运行
  MCP Server 脚本），回退到纯环境变量模式。
===========================================================================
"""
import logging

logger = logging.getLogger("aiops.tool_config")

# ── 优先从统一配置模块导入 ──────────────────────────────────────────
try:
    from agent.config import (
        PROMETHEUS_URL,
        LOKI_URL,
        JAEGER_URL,
        REQUEST_TIMEOUT,
    )
    logger.debug("从 agent.config 加载后端地址")

except ImportError:
    # 回退：直接运行 MCP Server 脚本时，agent 包不在 sys.path
    import os

    PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "")
    LOKI_URL = os.environ.get("LOKI_URL", "")
    JAEGER_URL = os.environ.get("JAEGER_URL", "")
    REQUEST_TIMEOUT = float(os.environ.get("TOOL_TIMEOUT", "30.0"))
    logger.debug("从环境变量直接加载后端地址（agent.config 不可用）")

# ── 日志格式 ──────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format=(
        '{"timestamp":"%(asctime)s","level":"%(levelname)s",'
        '"tool":"%(name)s","message":"%(message)s"}'
    ),
)
