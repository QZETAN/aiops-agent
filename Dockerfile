# ============================================================================
# AIOps Agent Docker 镜像
# ============================================================================
# 构建：
#   docker build -t aiops-agent:0.2.0 .
#
# 运行（CLI 模式，单次诊断）：
#   docker run --rm -e DEEPSEEK_API_KEY=sk-xxx \
#     -e PROMETHEUS_URL=http://prometheus:9090 \
#     aiops-agent:0.2.0 diagnose --alert "服务A 5xx升高"
#
# 运行（HTTP 服务模式）：
#   docker run -d --name aiops -p 8000:8000 \
#     -e DEEPSEEK_API_KEY=sk-xxx \
#     -e PROMETHEUS_URL=http://prometheus:9090 \
#     -e LOKI_URL=http://loki:3100 \
#     -e JAEGER_URL=http://jaeger:16686 \
#     aiops-agent:0.2.0 serve --port 8000
# ============================================================================

FROM python:3.12-slim

LABEL org.opencontainers.image.title="AIOps Agent"
LABEL org.opencontainers.image.description="LangGraph 多智能体故障诊断 Agent"
LABEL org.opencontainers.image.version="0.2.0"

WORKDIR /app

# 安装系统依赖（Git 用于代码变更分析工具）
RUN apt-get update && apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

# 先复制依赖清单，利用 Docker 缓存层加速构建
COPY pyproject.toml .
COPY app.py .
COPY agent/__init__.py agent/
COPY agent/config.py agent/
COPY agent/utils.py agent/

# 安装 Python 依赖
RUN pip install --no-cache-dir -e . && \
    pip install --no-cache-dir fastapi uvicorn tenacity

# 复制项目源码
COPY agent/ agent/
COPY ui/ ui/
COPY scripts/ scripts/
COPY README.md .

# 重新安装（让源码变更生效）
RUN pip install --no-cache-dir -e .

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "from agent.agents.graph import build_graph; build_graph()" || exit 1

# 单容器同时运行 API + Web UI
RUN chmod +x /app/scripts/entrypoint.sh
EXPOSE 8000 8501
ENTRYPOINT ["/app/scripts/entrypoint.sh"]
