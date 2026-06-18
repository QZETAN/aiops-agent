# AIOps 智能运维 Agent

基于 **LangGraph 多智能体 + MCP 协议** 的微服务故障自动诊断系统。

输入一条告警，Agent 自动发现服务、自主调度 4 个诊断专家（指标/日志/调用链/代码变更），定位根因并输出带置信度的修复建议。

**零侵入**：对接你的微服务不需要改任何业务代码，只需 Prometheus / Loki / Jaeger 三件套。

---

## 它能做什么

```
你：  "有服务 CPU 突然飙升，响应变慢"
        ↓
Agent：discover_services() → 发现 3 个服务
       metrics_expert → user-service CPU 飙升 50 倍，发现 /fault/cpu 端点被调用
       logs_expert    → 无 ERROR 日志，排除代码异常
       Supervisor     → 证据充分，结案
        ↓
报告： 根因 = /fault/cpu 端点被触发，置信度 0.85
       修复建议 = 给故障端点加鉴权，生产环境禁用
```

---

## 架构

```
告警 → Supervisor(LLM路由) → Metrics/Logs/Traces/Code Expert → 推理 → 根因报告
              ↕                        ↕
         discover_services()      MCP 工具层
         (自动发现服务)           (Prometheus/Loki/Jaeger/Git)
```

| 组件 | 技术 |
|------|------|
| 多智能体编排 | LangGraph StateGraph + Supervisor-Worker 模式 |
| LLM | DeepSeek Chat（可换 OpenAI / Ollama） |
| 工具协议 | MCP (Model Context Protocol) |
| 可观测性数据源 | Prometheus + Loki + Jaeger |
| Web UI | Streamlit（诊断/历史/统计三页签） |
| 数据持久化 | SQLite（零依赖，自动创建） |
| 部署 | Docker Compose / Docker / 裸机 |

---

## 快速开始

### 前置条件

- Python 3.11+
- 可用的 Prometheus + Loki + Jaeger（或项目自带的 Docker Compose 监控栈）
- DeepSeek API Key（[platform.deepseek.com](https://platform.deepseek.com) 申请）

### 1. Clone 并安装

```bash
git clone https://github.com/QZETAN/aiops-agent.git
cd aiops-agent

# 创建虚拟环境
python -m venv venv
source venv/bin/activate      # Linux/Mac
venv\Scripts\activate         # Windows

# 一键安装所有依赖
pip install -e .
```

### 2. 配置

```bash
# 复制配置模板
cp .env.example .env

# 编辑 .env，填入你的实际值（必填项只有 4 个）
```

| 变量 | 说明 | 示例 |
|------|------|------|
| `DEEPSEEK_API_KEY` | **必填**。DeepSeek API Key | `sk-xxxxxxxxxxxxxxxx` |
| `PROMETHEUS_URL` | **必填**。Prometheus 地址 | `http://192.168.101.100:9090` |
| `LOKI_URL` | **必填**。Loki 地址 | `http://192.168.101.100:3100` |
| `JAEGER_URL` | **必填**。Jaeger 地址 | `http://192.168.101.100:16686` |

其余变量有合理默认值，一般不需要改。详见 `.env.example`。

### 3. 启动监控栈（如果没有现成的）

```bash
docker compose -f docker-compose.agent.yml up -d
```

这会启动 Prometheus + Loki + Jaeger + Grafana + OTel Collector。

### 4. 验证环境

```bash
# 验证 Agent 编译
python scripts/test_graph.py

# 验证 MCP 工具连通性
python scripts/test_tools.py
```

`test_graph.py` 全部 6 项 `[OK]` 表示环境就绪。

### 5. 运行诊断

```bash
# CLI 单次诊断
aiops diagnose --alert "有服务 CPU 飙升，响应变慢"

# HTTP 服务模式（可接收 AlertManager Webhook）
aiops serve --port 8000

# Web UI
streamlit run ui/streamlit_app.py
# 浏览器打开 http://localhost:8501

# 查看统计报告
aiops stats --days 30

# 查看历史记录
aiops history --limit 20
```

### 6. 故障注入测试（可选，需项目自带的 Demo 微服务）

```bash
# 注入 CPU 故障
curl -X POST "http://localhost:8082/fault/cpu?seconds=30"

# 注入慢请求
curl -X POST "http://localhost:8081/fault/slow?seconds=5"

# 产生流量
for i in $(seq 1 30); do curl -s http://localhost:8080/api/order/1 > /dev/null; done

# 立即诊断
aiops diagnose --alert "有服务 CPU 飙升"
```

---

## 对接你的微服务

**不需要改你的服务代码。** Agent 只通过 Prometheus / Loki / Jaeger 的 API 读取数据。

你的微服务只需要：
1. 被 Prometheus 采集指标（或通过 OTel Collector remote write）
2. 日志发送到 Loki
3. Trace 发送到 Jaeger

Agent 启动后自动通过 `discover_services()` 发现所有服务，不需要告诉它"你有哪些服务"。

如果你的微服务命名和 Demo 不同（比如叫 `payment-svc` 而不是 `order-service`），完全没问题——Agent 发现什么就用什么。

---

## 运行模式

| 模式 | 命令 | 场景 |
|------|------|------|
| CLI 单次 | `aiops diagnose --alert "..."` | 手动排查 |
| CLI 交互 | `aiops` | 开发调试 |
| HTTP 服务 | `aiops serve --port 8000` | 生产部署、AlertManager Webhook |
| Web UI | `streamlit run ui/streamlit_app.py` | 可视化操作 |
| Docker | `docker compose -f docker-compose.agent.yml up -d` | 容器化 |

HTTP 服务端点：
- `GET /health` — K8s liveness probe
- `POST /diagnose` — 接收告警（兼容 AlertManager Webhook）
- `GET /metrics` — Agent 自身 Prometheus 指标

---

## 设计要点

### Supervisor + 4 Expert 模式

Supervisor 作为调度中心，按"指标→日志→调用链→代码"顺序调度专家，不直接分析数据。

### 置信度评分

Agent 根据多个数据源的一致性给出 0.0～1.0 的置信度：

| 分数 | 条件 | 说明 |
|------|------|------|
| **0.90-1.00** | 指标 + 日志 + 调用链三个数据源一致 | 三源交叉验证，确定性极高 |
| **0.70-0.89** | 两个数据源一致 | 根因可靠，可作为修复依据 |
| **0.50-0.69** | 只有一个数据源有信号 | 方向对，但需人工确认 |
| **0.30-0.49** | 多个数据源无数据 | 证据不足，建议补充监控接入 |
| **< 0.30** | 完全无法判断 | 建议人工排查或检查监控配置 |

**想获得 0.90+ 的置信度？** 在告警中尽可能提供信息（如"XX 服务 CPU 飙升，日志报 OOM，调用链超时"），Agent 会调度三个专家全部查一遍，交叉验证后给出最高置信度。

低于 0.7 的也会自动触发**反思循环**——Agent 分析证据缺口，补充查询，重新生成报告。最多反思 2 轮，仍不足则以现有证据输出。

### 安全阀

| 限制项 | 默认值 | 说明 |
|--------|--------|------|
| 最大调度轮数 | 10 | 防止 LLM 无限循环 |
| 最大反思轮数 | 2 | 防止反复反思 |
| Expert 查询上限 | 3-5 次 | 防止单个 Expert 烧 token |
| LLM 自动重试 | 3 次 | API 429/503 自动重试 |
| Git 路径白名单 | GIT_REPO_PATH | 防止路径穿越攻击 |

### 成本控制

每次 LLM 调用自动记录 token 用量。诊断结果存入 SQLite，可通过 `aiops stats` 查看累计消耗趋势。

---

## 项目结构

```
aiops-agent/
├── agent/
│   ├── config.py          # 统一配置（环境变量 + LLM工厂 + 重试）
│   ├── utils.py           # JSON清理/修复、文本工具
│   ├── db.py              # SQLite 诊断记录持久化
│   ├── server.py          # FastAPI HTTP 服务
│   ├── agents/
│   │   ├── state.py       # AgentState（8 字段）
│   │   ├── supervisor.py  # Supervisor 调度（通用化 Few-Shot）
│   │   ├── experts.py     # 4 专家（延迟初始化 + 降级策略）
│   │   ├── graph.py       # 工作流图组装
│   │   └── reflect.py     # 反思节点
│   └── tools/
│       ├── tool_loader.py         # MCP→LangChain 工具（6个工具含 discover_services）
│       ├── tool_config.py         # 配置兼容层
│       ├── mcp_server_prometheus.py
│       ├── mcp_server_loki.py
│       ├── mcp_server_jaeger.py
│       └── mcp_server_git.py      # 含路径白名单安全校验
├── microservices/          # Spring Cloud Demo 微服务
├── docker/                 # Docker Compose 监控栈
├── scripts/
│   ├── fault_injector.py   # 故障注入
│   ├── test_tools.py       # MCP 连通性测试
│   ├── test_graph.py       # Graph 编译验证
│   └── test_pipeline.py    # 自动化诊断测试
├── ui/
│   └── streamlit_app.py    # Web UI（诊断/历史/统计）
├── app.py                  # CLI 入口
├── Dockerfile              # Agent 容器镜像
├── docker-compose.agent.yml # Agent + 监控栈 一体化
├── pyproject.toml          # 项目元数据 + 依赖
├── .env.example            # 环境变量模板
└── README.md
```

---

## 常见问题

**Q: 对接我的微服务需要改代码吗？**
不需要。Agent 只通过标准可观测性 API 读取数据。

**Q: 我的服务名和 Demo 不一样怎么办？**
Agent 会自动通过 `discover_services()` 发现，无论叫什么名字。

**Q: Prometheus / Loki 有一个挂了影响诊断吗？**
不会中断。Agent 识别到数据源不可用后自动跳过，用其余数据源继续。

**Q: 诊断一次花多少 token？**
取决于复杂度。简单故障约 5K-15K tokens，复杂故障（含反思）约 20K-50K tokens。

**Q: 为什么不用 MySQL？**
SQLite 零配置、零依赖、Python 标准库自带。单机部署一天几百条诊断记录，完全够用。

---

## License

MIT
