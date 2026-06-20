#!/bin/bash
# AIOps Agent 容器入口 — 同时启动 API 服务和 Web UI

# 后台启动 HTTP API（/health /diagnose /metrics）
python -m agent.server --port 8000 &

# 等待 API 就绪
sleep 3

# 前台启动 Web UI（保持容器存活）
exec streamlit run ui/streamlit_app.py \
    --server.port 8501 \
    --server.headless true \
    --server.address 0.0.0.0
