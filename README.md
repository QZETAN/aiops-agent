# AIOps 智能运维 Agent

基于 **LangGraph 多智能体 + MCP 协议** 的微服务故障自动诊断系统。输入一条告警，Agent 自主调度专家（指标/日志/调用链/代码变更），自动定位根因并输出修复建议。

## 架构

```
用户告警 → Supervisor(LLM路由) → Metrics/Logs/Traces/Code Expert → 回Supervisor → 推理节点 → 根因报告
                                    ↕
                              MCP 工具层
                              (Prometheus/Loki/Jaeger/Git)
                                    ↕
                              可观测性数据层
                              (OTel Collector → Prometheus/Loki/Jaeger/Grafana)
                                    ↕
                              Spring Cloud 微服务
                              (user/order/gateway + OpenTelemetry)
```

## 技术栈

| 层级 | 技术 |
|------|------|
| 多智能体编排 | LangGraph + LangChain |
| LLM | DeepSeek Chat |
| 工具协议 | MCP (Model Context Protocol) |
| 可观测性 | Prometheus + Loki + Jaeger + Grafana + OpenTelemetry Collector |
| 微服务 | Spring Boot 3.2 + Spring Cloud + OpenFeign + OTel Java Agent |
| 前端 | Streamlit |
| 语言 | Python 3.12 + Java 21 |

## 快速开始

### 1. 启动监控栈

```bash
cd docker
docker compose up -d
```

### 2. 启动微服务

在 IDEA 中分别启动 `user-service`、`order-service`、`gateway-service`，VM Options 已配置 OpenTelemetry Agent。

### 3. 安装 Python 依赖

```bash
python -m venv venv
source venv/Scripts/activate  # Windows
pip install langgraph langchain langchain-openai mcp httpx streamlit
```

### 4. 配置 DeepSeek API Key

```bash
export DEEPSEEK_API_KEY=sk-xxx
```

### 5. 验证环境

```bash
python scripts/test_graph.py    # 验证 Agent 编译
python scripts/test_tools.py    # 验证 MCP 工具连通性
```

### 6. 运行诊断

**CLI 模式:**
```bash
python app.py --alert "order-service 调用延迟突然升高"
```

**Web UI:**
```bash
streamlit run ui/streamlit_app.py
```

### 7. 注入故障测试

```bash
# CPU 飙升
python scripts/fault_injector.py --service user --fault cpu --duration 30

# 慢请求
python scripts/fault_injector.py --service order --fault slow --duration 5

# 空指针异常
python scripts/fault_injector.py --service user --fault npe
```

## 项目结构

```
aiops-agent/
├── agent/                       # LangGraph 多智能体系统
│   ├── agents/
│   │   ├── state.py             # AgentState 定义
│   │   ├── supervisor.py        # Supervisor 调度节点
│   │   ├── experts.py           # 4 个专业 Agent
│   │   ├── graph.py             # 工作流图组装
│   │   └── reflect.py           # 反思节点
│   └── tools/
│       ├── mcp_server_prometheus.py  # Prometheus MCP 工具
│       ├── mcp_server_loki.py        # Loki MCP 工具
│       ├── mcp_server_jaeger.py      # Jaeger MCP 工具
│       ├── mcp_server_git.py         # Git MCP 工具
│       ├── tool_loader.py            # MCP→LangChain 工具转换
│       └── tool_config.py            # 工具统一配置
├── microservices/               # Spring Cloud 微服务
│   ├── user-service/
│   ├── order-service/
│   ├── gateway-service/
│   └── otel-agent/              # OpenTelemetry Java Agent
├── docker/                      # Docker Compose 监控栈
│   ├── docker-compose.yml
│   └── config/
├── scripts/
│   ├── fault_injector.py        # 故障注入脚本
│   ├── test_tools.py            # MCP 工具连通性测试
│   ├── test_graph.py            # Graph 编译验证
│   ├── test_e2e.py              # 端到端诊断测试
│   └── test_pipeline.py         # 自动化诊断测试
├── ui/
│   └── streamlit_app.py         # Web UI
├── docs/                        # 设计文档
├── app.py                       # CLI 入口
└── README.md
```

## 核心设计

### Supervisor + Worker 模式

Supervisor 作为调度中心，负责"先找谁、后找谁"，不直接分析数据。4 个 Expert 各司其职：

- **MetricsExpert**: 查询 Prometheus 指标（QPS/CPU/内存/错误率）
- **LogsExpert**: 查询 Loki 日志（ERROR/异常堆栈/trace_id）
- **TracesExpert**: 查询 Jaeger 调用链（定位错误 Span/慢请求）
- **CodeExpert**: 查询 Git 提交历史（关联代码变更时间线）

### 反思机制

推理节点输出报告后，如果置信度 < 0.7，系统自动生成补充查询计划，回到 Supervisor 重新调度。最多反思 2 轮。

### 安全阀

- Supervisor 最多调度 10 轮，超限强制终止
- 反思最多 2 轮，超限输出已有报告
- 每个 Expert 最多查询 3-5 次 MCP 工具

## License

MIT
