# AIOps 智能运维 Agent

> 输入一条告警，自动定位微服务故障根因。**不需要运维经验，一条命令部署。**

```
你: "有服务 CPU 突然飙升，响应变慢"
Agent: 自动发现服务 → 查指标 → 查日志 → 查调用链 → 2 分钟后输出根因报告
```

---

## 一、它能解决什么问题

| 场景 | 人工排查 | AIOps Agent |
|------|---------|------------|
| CPU 飙升 | 10-20 分钟（打开 Prometheus → 查 Grafana → 搜 Loki → 翻 Jaeger） | **1-2 分钟** |
| 凌晨 3 点告警 | 起床开电脑，半小时清醒 | **Agent 自动诊断** |
| 新人排查故障 | 不知道从哪查起，到处问 | **和资深运维一样快** |
| 需要知道服务名？ | 必须知道，不知道没法查 | **Agent 自动发现** |
| 需要会 PromQL / LogQL？ | 必须会 | **不需要** |

---

## 二、快速开始（3 步）

### 你需要准备

- **Docker Desktop** — [下载](https://www.docker.com/products/docker-desktop)，启动后右下角图标是绿的
- **Python 3.11+** — [下载](https://www.python.org/downloads/)（只有 Web UI 需要）
- **DeepSeek API Key** — [免费注册](https://platform.deepseek.com)，充 5 块能用很久

### 第 1 步：克隆项目

```bash
git clone https://github.com/QZETAN/aiops-agent.git
cd aiops-agent
```

### 第 2 步：配置 API Key

```bash
cp .env.example .env
```

编辑 `.env`，改一行：

```bash
DEEPSEEK_API_KEY=sk-你的真实key
```

### 第 3 步：一键启动

```bash
# Windows：双击运行
scripts\start.bat

# Mac / Linux：
bash scripts/start.sh
```

**这一个脚本会自动完成**：

```
启动 Docker Compose（6 个容器）:
  ├── AIOps Agent     诊断引擎    :8000
  ├── OTel Collector  数据接收    :4317
  ├── Prometheus      指标存储    :9090
  ├── Loki            日志存储    :3100
  ├── Jaeger          调用链存储  :16686
  └── Grafana         可视化看板  :3000

安装 Python 依赖（首次）
启动 Web UI → 自动打开浏览器 http://localhost:8501
```

浏览器打开后，输入告警就开始诊断。

---

## 三、对接你的微服务

Agent 和监控基础设施已经一键部署好了。**现在需要让你的微服务把数据上报进来。**

### 你需要理解的关键概念

```
【监控基础设施】        【你的微服务】
Docker Compose 一键启动  需要你手动加一个启动参数

Prometheus  ←──────────┐
Loki        ←──────────┤   数据从哪来？
Jaeger      ←──────────┤       ↓
                        └── OTel Collector :4317
                              ↑
                        你的微服务需要把数据发给它
```

**OTel（OpenTelemetry）不是一个能"安装"的服务。** 它是一个 JAR 包，挂到你的微服务启动参数里，自动采集数据并发给 Collector。

类比：Prometheus 是"数据库"，OTel 是"埋点 SDK"。就像 MySQL 可以 docker run，但 JDBC Driver 要加到 Java 项目里一样。

### 给你的微服务加上 OTel（一行 JVM 参数）

**第 1 步：下载 OTel Java Agent JAR**

```bash
# 下载到你的项目目录（只需一次）
curl -L -o opentelemetry-javaagent.jar \
  https://github.com/open-telemetry/opentelemetry-java-instrumentation/releases/latest/download/opentelemetry-javaagent.jar
```

**第 2 步：在启动参数里加上这一行**

```bash
java -javaagent:/path/to/opentelemetry-javaagent.jar \
     -Dotel.service.name=你的服务名 \
     -Dotel.exporter.otlp.endpoint=http://localhost:4317 \
     -Dotel.metrics.exporter=otlp \
     -Dotel.logs.exporter=otlp \
     -Dotel.traces.exporter=otlp \
     -jar your-service.jar
```

如果你是 IDEA 启动，把这行加到 Run Configuration 的 VM Options：

```
-javaagent:C:\path\to\opentelemetry-javaagent.jar
-Dotel.service.name=order-service
-Dotel.exporter.otlp.endpoint=http://localhost:4317
-Dotel.metrics.exporter=otlp
-Dotel.logs.exporter=otlp
-Dotel.traces.exporter=otlp
```

**第 3 步：验证数据进来了**

对着你的服务发几个请求，然后：

```bash
# 应该能看到你的服务名
curl http://localhost:16686/api/services
# 返回 {"data": ["order-service", ...]}   ← 数据进来了！
```

**不是 Java？** OTel 支持所有主流语言：[Python](https://opentelemetry.io/docs/languages/python/) · [Go](https://opentelemetry.io/docs/languages/go/) · [Node.js](https://opentelemetry.io/docs/languages/js/) · [.NET](https://opentelemetry.io/docs/languages/net/) · [Rust](https://opentelemetry.io/docs/languages/rust/)

---

## 四、它能输出什么

输入 "有服务 CPU 突然飙升"，输出一份带置信度的报告：

```
置信度：0.85  ← 0.70+ 可作为修复依据，0.90+ 确定性极高

根因：
  user-service 的 /fault/cpu 端点被调用，JVM CPU 从 0.12% 飙升至 6.31%

证据：
  ✅ 指标：CPU 在 14:03 飙升 50 倍，同时 /fault/cpu 端点有请求记录
  ✅ 日志：所有服务无 ERROR/WARN，排除代码异常
  ✅ 调用链：gateway → order → user，user 环节耗时异常

修复建议：
  1. 立即停止对 /fault/cpu 的调用
  2. 给故障注入端点加鉴权或 IP 白名单
  3. 监控 CPU 在 5 分钟内回落至正常水平
```

**置信度评分**（Agent 根据多少数据源验证了同一结论）：

| 分数 | 条件 |
|------|------|
| 0.90+ | 指标 + 日志 + 调用链三源一致，确定性极高 |
| 0.70-0.89 | 两个数据源一致，可作为修复依据 |
| 0.50-0.69 | 只有一个数据源有信号，方向对但需人工确认 |
| < 0.50 | 证据不足，建议人工介入 |

---

## 五、工作原理

```
你的告警
    ↓
Supervisor（LLM 路由决策）
    ↓           ↓           ↓           ↓
指标专家    日志专家    调用链专家    代码专家
(查 CPU/   (查 ERROR/  (查慢请求/   (查 Git
QPS/内存)  Exception)  错误 Span)   提交历史)
    ↓           ↓           ↓           ↓
    └───────────┴───────────┴───────────┘
                    ↓
            根因推理 + 置信度评分
            如果置信度 < 0.7 → 自动反思补充查询
                    ↓
              诊断报告（自动存入 SQLite）
```

---

## 六、其他功能

```bash
# CLI 单次诊断
aiops diagnose --alert "服务CPU飙高"

# 查看历史记录
aiops history

# 统计报告（最近 30 天哪个服务故障最多）
aiops stats --days 30

# HTTP 服务模式（接 AlertManager Webhook）
aiops serve --port 8000
```

Web UI（`http://localhost:8501`）还有「📋 历史记录」「📊 统计分析」两个页签。

---

## 七、常见问题

**Q: 我没有任何微服务，想先看效果？**
项目自带 Demo 微服务（`microservices/` 目录），用 IDEA 打开即可运行。OTel Agent JAR 已打包在内，VM Options 已配好。启动后用 `scripts/fault_injector.py` 注入故障即可测试。

**Q: 对接现有微服务，需要改代码吗？**
不需要。只加一行 JVM 启动参数（见第三章）。

**Q: 微服务不在本地，在远程服务器？**
把 JVM 参数里的 `localhost:4317` 换成你的 Collector IP。`.env` 里的 URL 也相应修改。

**Q: 没有 Prometheus / Loki / Jaeger？**
一键部署已包含（`docker compose up -d`），不需要额外安装。

**Q: 诊断准确率怎么样？**
取决于你接了几个数据源。三个都接 → 置信度 0.90+，只接一个 → 0.50-0.70。

---

## 八、项目结构

```
aiops-agent/
├── agent/              # LangGraph 多智能体核心
│   ├── agents/         # Supervisor + 4 Expert + 反思节点
│   ├── tools/          # MCP 工具层（Prometheus/Loki/Jaeger/Git）
│   ├── config.py       # 统一配置 + LLM 重试
│   ├── db.py           # SQLite 诊断记录
│   └── server.py       # HTTP 服务（/health /diagnose /metrics）
├── docker/             # Docker Compose（监控栈 + Agent）
├── microservices/      # Demo 微服务（含 OTel Agent JAR）
├── scripts/
│   ├── start.bat       # 一键启动（Windows）
│   └── fault_injector.py
├── ui/
│   └── streamlit_app.py  # Web UI
├── app.py              # CLI 入口
└── .env.example        # 配置模板
```

---

## License

MIT
