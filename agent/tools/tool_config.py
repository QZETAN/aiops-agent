"""
MCP 工具统一配置模块。

设计原因：
  4 个 MCP Server 各自需要知道后端地址（Prometheus/Loki/Jaeger 在虚拟机上，
  Git 在本地文件系统）。如果每个文件各自写死 URL，换一个环境（比如从开发虚拟机
  切到生产 K8s）就要改 4 个地方，容易漏、容易错。

  统一配置的好处：
    1. 一个环境变量改全局 —— PROMETHEUS_URL 一改，所有用到的地方自动生效
    2. 支持 CI/CD —— 测试环境和生产环境用不同的 env 文件
    3. 新人接手一眼看懂所有外部依赖

为什么用 os.getenv 而不是配置文件（YAML/JSON）？
  - 12-Factor App 原则：配置存环境变量，代码和配置分离
  - Docker Compose / K8s ConfigMap 天然支持注入环境变量
  - 比 JSON 配置文件少一个依赖、少一次文件 IO
"""
import os

# ==================== 后端地址 ====================

# Prometheus 时序指标数据库
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://192.168.101.100:9090")

# Loki 日志聚合系统
LOKI_URL = os.getenv("LOKI_URL", "http://192.168.101.100:3100")

# Jaeger 分布式调用链系统
JAEGER_URL = os.getenv("JAEGER_URL", "http://192.168.101.100:16686")

# ==================== 通用参数 ====================

# HTTP 请求超时（秒）
REQUEST_TIMEOUT = float(os.getenv("TOOL_TIMEOUT", "30.0"))

# ==================== 日志（可选） ====================

import logging

logging.basicConfig(
    level=logging.INFO,
    format='{"timestamp":"%(asctime)s","level":"%(levelname)s","tool":"%(name)s","message":"%(message)s"}',
)
