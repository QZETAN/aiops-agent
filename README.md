# AIOps 智能运维 Agent

> 输入一条告警，自动定位微服务故障根因。
> **只需 Docker，一条命令部署。**

---

## 这是什么

你的微服务出故障时，通常需要打开 Prometheus 看指标、翻 Loki 查日志、切到 Jaeger 追调用链，再人脑关联三者找根因。这个过程需要会 PromQL、LogQL，知道服务之间的调用关系，新手根本无从下手。

这个 Agent 替你做了这些事：收到告警后自动发现服务、自动决定查什么、自动关联三个数据源的信息、输出带置信度的根因报告。

[效果演示截图或 GIF 占位]

---

## 快速开始

### 你需要有

- **Docker**（已安装并运行）
- **DeepSeek API Key**（[免费注册](https://platform.deepseek.com)，个人版即可）

### 1. 克隆项目

```bash
git clone https://github.com/QZETAN/aiops-agent.git
cd aiops-agent
```

### 2. 配置 API Key

```bash
cp .env.example .env
```

编辑 `.env`，只改这一行：

```env
DEEPSEEK_API_KEY=sk-你的key
```

### 3. 启动

```bash
docker compose --env-file .env -f docker/docker-compose.yml up -d --build
```

首次运行会拉取镜像和构建，约 3-5 分钟。之后再次启动只需几秒。

### 4. 打开 Web UI

浏览器打开 `http://localhost:8501`，输入告警内容即可开始诊断。

> 如果你的 Docker 跑在远程服务器上，把 `localhost` 换成服务器 IP。

---

## 怎么用

在 Web UI 输入框里描述你看到的故障现象。你可以很具体：

```
order-service 的 5xx 错误率突然飙升到 25%
```

也可以很模糊：

```
系统有点卡，响应很慢
```

Agent 会自己发现环境中有哪些服务、自己决定查什么。输入越具体，定位越精准。

诊断完成后你会看到一份报告，包含根因描述、置信度、证据链、修复建议。所有诊断记录自动保存，可随时回溯。

---

## 对接你的微服务

Agent 本身不存储你的业务数据。它通过查询 Prometheus、Loki、Jaeger 的 API 来获取信息。所以你的微服务需要先把数据上报到这三个后端。

### 上报数据只需要一件事

给你的微服务挂上 **OpenTelemetry Agent**（一个 JAR 包，不改代码）：

下载 JAR 放到项目目录，然后在启动参数里加一行：

```
-javaagent:/path/to/opentelemetry-javaagent.jar
-Dotel.service.name=你的服务名
-Dotel.exporter.otlp.endpoint=http://localhost:4317
```

IDEA 用户：加在 Run Configuration → VM Options 里。
命令行启动：加在 `java` 命令后面。

JAR 下载地址：[OpenTelemetry Java Agent Releases](https://github.com/open-telemetry/opentelemetry-java-instrumentation/releases)

> 非 Java 项目？OTel 支持 [Python](https://opentelemetry.io/docs/languages/python/) · [Go](https://opentelemetry.io/docs/languages/go/) · [Node.js](https://opentelemetry.io/docs/languages/js/) · [.NET](https://opentelemetry.io/docs/languages/net/)，原理一样。

### 确认数据已上报

发几个请求到你的服务，然后访问 Jaeger UI（`http://localhost:16686`），在 Service 下拉框里看到你的服务名就说明通了。

---

## 效果：一份典型的诊断报告

```
置信度：0.85

根因：
  order-service 在 14:03 出现 NullPointerException (OrderService.java:156)，
  导致调用链超时，上游 gateway 返回 500。

证据：
  · 指标：14:03 起 5xx 错误率从 0 升至 25%
  · 日志：14:03:12 NullPointerException at OrderService.java:156
  · 调用链：trace abc123 中 order-service Span 耗时 4800ms（正常 150ms）

修复建议：
  回滚提交 def4567（张三，14:02），该提交将 customerName 字段改为了 nullable
```

**置信度说明**：

| 分数 | 含义 |
|------|------|
| 0.90+ | 指标 + 日志 + 调用链三源交叉验证，确定性极高 |
| 0.70-0.89 | 两个数据源一致，可作为修复依据 |
| 0.50-0.69 | 一个数据源有信号，方向对但建议人工确认 |
| < 0.50 | 证据不足，建议检查监控接入是否完整 |

---

## 架构

```
告警 → Supervisor（决策下一步找谁）
         ↓        ↓        ↓        ↓
      指标专家  日志专家  调用链专家  代码专家
         ↓        ↓        ↓        ↓
         └────────┴────────┴────────┘
                     ↓
             根因推理 + 置信度评分
             置信度 < 0.7 → 自动反思补充查询
                     ↓
               诊断报告 → 存入数据库
```

Agent 内部是 LangGraph 多智能体工作流。Supervisor 作为调度中心，按「指标 → 日志 → 调用链 → 代码」的顺序调度 4 个专业 Agent。低于 0.7 分的报告会自动触发反思循环，补充缺失证据后重新推理。

---

## 想先试试效果？（可选）

项目自带三个 Demo 微服务（`microservices/` 目录），用 IDEA 打开即可运行。VM Options 和 OTel Agent JAR 已经配好，启动后直接产生数据。

```bash
# 产生流量
curl http://localhost:8080/api/order/1

# 注入故障
curl -X POST http://localhost:8082/fault/cpu?seconds=30

# 在 Web UI 输入 "有服务 CPU 飙升" 诊断
```

---

## 常见问题

**Q: 对接现有微服务需要改代码吗？**
不需要。只加一个 JVM 启动参数。

**Q: 没有 Prometheus / Loki / Jaeger？**
`docker compose up -d` 已经包含全部，不需要额外安装。

**Q: 为什么用 DeepSeek？能换吗？**
可以。改 `.env` 中的 `LLM_MODEL` 和 `LLM_BASE_URL` 即可换成 OpenAI 或 Ollama。

**Q: 诊断一次花多少钱？**
简单故障约 5K-15K tokens。DeepSeek 的价格，一次几分钱。

---

## License

MIT
