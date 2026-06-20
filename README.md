# AIOps 智能运维 Agent

> 输入一条告警，自动定位微服务故障根因。**一条命令部署，不需要 Python，不需要运维经验。**

```
你: "有服务 CPU 突然飙升，响应变慢"
Agent: 2 分钟后输出置信度 0.85 的根因报告，附带修复建议
```

---

## 一、效果

| 场景 | 人工排查 | AIOps Agent |
|------|---------|------------|
| CPU 飙升定位 | 开 Prometheus → 切 Loki → 翻 Jaeger，20 分钟 | **1-2 分钟** |
| 凌晨告警 | 起床开电脑 | **Agent 自动诊断** |
| 新人上手 | 2-3 周学习 PromQL / LogQL | **1 分钟** |

---

## 二、快速开始（只需 Docker）

### 前提

- **Docker** 已安装并运行
- **DeepSeek API Key**（[免费注册](https://platform.deepseek.com)，充 5 块够用很久）

### 第 1 步：克隆 + 配 Key

```bash
git clone https://github.com/QZETAN/aiops-agent.git
cd aiops-agent
cp .env.example .env
```

编辑 `.env`，改这一行：

```bash
DEEPSEEK_API_KEY=sk-你的key
```

### 第 2 步：一键启动

```bash
# Windows：双击
scripts\start.bat

# Mac / Linux：
cd docker && docker compose --env-file ../.env up -d --build
```

**这会自动启动 7 个容器**：

```
docker compose up -d
  ├── aiops-agent    诊断引擎 API   :8000
  ├── aiops-ui       Web 界面       :8501
  ├── otel-collector 数据接收       :4317
  ├── prometheus     指标存储       :9090
  ├── loki           日志存储       :3100
  ├── jaeger         调用链存储     :16686
  └── grafana        可视化看板     :3000
```

### 第 3 步：打开浏览器

```
http://localhost:8501
```

输入告警（如 "有服务 CPU 突然飙升"），点「开始诊断」。

> **Docker 跑在远程服务器上？** 把 `localhost` 换成服务器 IP。例如服务器 IP 是 `192.168.1.100`，浏览器打开 `http://192.168.1.100:8501`。

---

## 三、对接你的微服务

前面启动的 7 个容器是**监控基础设施 + 诊断引擎**。现在需要让你的微服务把数据上报进来。

### OTel 是什么

OTel（OpenTelemetry）不是能独立安装的服务，而是一个 **JAR 包**，挂到你的微服务进程里自动采集数据。

```
类比：Prometheus = 数据库      OTel JAR = 数据库驱动（JDBC）
      docker run 启动            放到项目里，随服务启动
```

### OTel JAR 放哪 + 怎么挂

**第 1 步：下载 JAR，放到你项目的任意目录**

```bash
# 下载到项目根目录（只需一次）
curl -L -o opentelemetry-javaagent.jar \
  https://github.com/open-telemetry/opentelemetry-java-instrumentation/releases/latest/download/opentelemetry-javaagent.jar
```

JAR 放哪都行，只要启动时 `-javaagent:` 后面的路径指对了：

```
你的项目/
├── src/
├── opentelemetry-javaagent.jar   ← 放这里
└── pom.xml
```

**第 2 步：在 IDEA 的 Run Configuration 里加 VM Options**

打开你的服务 → Run → Edit Configurations → VM Options，加入：

```
-javaagent:opentelemetry-javaagent.jar
-Dotel.service.name=你的服务名
-Dotel.exporter.otlp.endpoint=http://localhost:4317
```

具体到你的三个 Demo 服务，VM Options 分别是：

```
gateway-service:  -javaagent:microservices/otel-agent/opentelemetry-javaagent.jar -Dotel.service.name=gateway-service -Dotel.exporter.otlp.endpoint=http://localhost:4317

order-service:    -javaagent:microservices/otel-agent/opentelemetry-javaagent.jar -Dotel.service.name=order-service -Dotel.exporter.otlp.endpoint=http://localhost:4317

user-service:     -javaagent:microservices/otel-agent/opentelemetry-javaagent.jar -Dotel.service.name=user-service -Dotel.exporter.otlp.endpoint=http://localhost:4317
```

**第 3 步：验证数据进来了**

启动你的服务后，发几个请求：

```bash
curl http://localhost:8080/你的接口
curl http://localhost:16686/api/services
# 返回 {"data": ["你的服务名", ...]}  ← 通了！
```

**不是 Java？** OTel 支持 [Python](https://opentelemetry.io/docs/languages/python/) · [Go](https://opentelemetry.io/docs/languages/go/) · [Node.js](https://opentelemetry.io/docs/languages/js/) · [.NET](https://opentelemetry.io/docs/languages/net/)

---

## 四、它会输出什么

```
置信度：0.85  ← 0.70+ 可作为修复依据

根因：
  user-service 的 /fault/cpu 端点被调用，CPU 从 0.12% 飙升至 6.31%

证据：
  ✅ 指标：CPU 飙升 50 倍，同时 /fault/cpu 端点有请求记录
  ✅ 日志：无 ERROR/WARN，排除代码异常
  ✅ 调用链：gateway → order → user，user 环节耗时异常

修复建议：
  1. 停止对 /fault/cpu 的调用，确认来源
  2. 给故障注入端点加鉴权
  3. 监控 CPU 在 5 分钟内回落
```

**置信度评分**：

| 分数 | 条件 |
|------|------|
| 0.90+ | 指标 + 日志 + 调用链三源一致 |
| 0.70-0.89 | 两个数据源一致 |
| 0.50-0.69 | 一个数据源有信号 |
| < 0.50 | 证据不足 |

---

## 五、工作原理

```
告警 → Supervisor（LLM 路由）
         ↓        ↓        ↓        ↓
      指标专家  日志专家  调用链专家  代码专家
      (PromQL)  (LogQL)  (Jaeger)  (Git log)
         ↓        ↓        ↓        ↓
         └────────┴────────┴────────┘
                     ↓
             根因推理 + 置信度评分
             如果 < 0.7 → 自动反思补充
                     ↓
               诊断报告 + 存入数据库
```

---

## 六、其他命令

```bash
docker exec aiops-agent python -m app diagnose --alert "CPU飙高"
docker exec aiops-agent python -m app history
docker exec aiops-agent python -m app stats --days 30
```

---

## 七、常见问题

**Q: 没有微服务，想先看效果？**
项目自带 Demo 微服务（`microservices/`），IDEA 打开运行即可，VM Options 已配好。启动后用 `scripts/fault_injector.py` 注入故障测试。

**Q: Docker 部署在服务器上，localhost 不行？**
把 `localhost` 换成服务器 IP。需要改两处：
- `.env` 中的 `PROMETHEUS_URL` / `LOKI_URL` / `JAEGER_URL`
- 微服务 JVM 参数中的 `-Dotel.exporter.otlp.endpoint`

**Q: 对接现有微服务要改代码吗？**
不需要。只加一行 JVM 启动参数（见第三章）。

**Q: 诊断一次花多少钱？**
简单故障约 5K-15K tokens，复杂故障约 20K-50K tokens。DeepSeek 价格，一次几分钱。

---

## 八、项目结构

```
aiops-agent/
├── agent/              # LangGraph 多智能体核心
│   ├── agents/         # Supervisor + 4 Expert + 反思
│   ├── tools/          # MCP 工具（Prometheus/Loki/Jaeger/Git）
│   ├── config.py       # 统一配置 + LLM 重试
│   ├── db.py           # SQLite 诊断记录
│   └── server.py       # HTTP API
├── docker/             # Docker Compose（7 个容器）
│   └── config/         # 监控栈配置文件
├── microservices/      # Demo 微服务（含 OTel Agent JAR）
├── scripts/
│   ├── start.bat       # 一键启动
│   └── fault_injector.py
├── ui/                 # Streamlit Web UI
├── Dockerfile
└── .env.example
```

---

## License

MIT
