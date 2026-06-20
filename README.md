# AIOps 智能运维 Agent

> 输入一条告警，自动定位微服务故障根因。**不需要运维经验。**

```
你: "有服务 CPU 突然飙升，响应变慢"
Agent: 自动发现服务 → 查指标 → 查日志 → 查调用链 → 2 分钟后输出根因报告
```

---

## 效果对比

| | 人工排查 | AIOps Agent |
|------|------|------|
| 需要懂 Prometheus？ | ✅ 必须会 PromQL | ❌ 不需要 |
| 需要懂 Loki？ | ✅ 必须会 LogQL | ❌ 不需要 |
| 需要懂 Jaeger？ | ✅ 必须会看调用链 | ❌ 不需要 |
| 需要知道服务名？ | ✅ 必须知道 | ❌ 自动发现 |
| 排查一个 CPU 飙升 | 10-20 分钟 | 1-2 分钟 |
| 凌晨 3 点告警 | 起床开电脑 | Agent 自动诊断 |
| 新人上手 | 2-3 周 | 1 分钟 |

---

## 快速开始

### 你需要准备

1. **Docker Desktop** — [下载安装](https://www.docker.com/products/docker-desktop)，启动后确保右下角图标是绿的
2. **Python 3.11+** — [下载安装](https://www.python.org/downloads/)
3. **DeepSeek API Key** — [免费注册申请](https://platform.deepseek.com)，5 块钱能用很久

### 3 步跑起来

```bash
# 1. 克隆项目
git clone https://github.com/QZETAN/aiops-agent.git
cd aiops-agent

# 2. 双击运行启动脚本
scripts\start.bat          # Windows
# 或
bash scripts/start.sh      # Mac / Linux（待添加）
```

脚本会自动：
- 启动 Docker 监控栈（Prometheus + Loki + Jaeger + Grafana + OTel Collector）
- 创建 Python 虚拟环境、安装依赖
- 帮你创建 `.env` 配置文件
- 打开浏览器到 Web UI

### 3. 填写 API Key

脚本首次运行会自动弹出记事本，让你填写 DeepSeek API Key：

```bash
# .env 文件里改这一行
DEEPSEEK_API_KEY=sk-你的真实key
```

保存后关掉记事本，脚本继续运行。

### 4. 开始诊断

浏览器打开 `http://localhost:8501`，在输入框里描述故障现象：

```
"有服务 CPU 突然飙升，响应变慢"
"某个服务突然返回大量 500 错误"
"系统整体延迟变高，用户反馈页面卡顿"
```

点击「开始诊断」，Agent 会自动发现服务、调度专家、输出根因报告。

---

## 对接你的微服务

**不需要改任何代码。** 给你的服务加一个启动参数即可：

```
-javaagent:opentelemetry-javaagent.jar
-Dotel.service.name=你的服务名
-Dotel.exporter.otlp.endpoint=http://localhost:4317
```

Agent 会自动从 Jaeger 发现你的服务。支持所有语言（Java / Go / Python / Node.js / .NET），只要有 OTel Agent 即可。

> OpenTelemetry Java Agent 下载：[官方发布页](https://github.com/open-telemetry/opentelemetry-java-instrumentation/releases)
> 其他语言：[OTel 官方文档](https://opentelemetry.io/docs/languages/)

---

## 它会输出什么

诊断完成后，你会得到一份带置信度的报告：

```
置信度：0.85  ← 0.70+ 即可作为修复依据，0.90+ 为确定性极高

根因：
  user-service 的 /fault/cpu 端点被调用，JVM CPU 从 0.12% 飙升至 6.31%（50 倍增长）

证据：
  ✅ 指标：CPU 在 14:03 飙升，同时 /fault/cpu 端点有请求记录
  ✅ 日志：所有服务无 ERROR/WARN，排除代码异常
  ✅ 调用链：gateway → order → user，user 环节耗时异常

修复建议：
  1. 立即停止对 /fault/cpu 的调用，确认调用来源
  2. 给故障注入端点加鉴权或 IP 白名单
  3. 监控 CPU 在 5 分钟内回落至 < 0.5%
```

---

## 内置安全阀

| 机制 | 作用 |
|------|------|
| LLM 自动重试 3 次 | API 短暂故障不中断诊断 |
| JSON 自动修复 | LLM 输出格式瑕疵自动纠正 |
| 最大 10 轮调度 | 防止无限循环烧 token |
| 最大 2 轮反思 | 防止反复自我质疑 |
| Expert 查询上限 | 单个专家最多查 3-5 次 |
| 全局异常兜底 | 崩溃时保留已收集证据 |

---

## 工作原理

```
你的告警
    ↓
Supervisor（LLM 调度中心）
    ↓           ↓           ↓           ↓
指标专家    日志专家    调用链专家    代码专家
(查 CPU/   (查 ERROR/  (查慢请求/   (查 Git
QPS/内存)  Exception)  错误 Span)   提交历史)
    ↓           ↓           ↓           ↓
    └───────────┴───────────┴───────────┘
                    ↓
            根因推理 + 置信度评分
                    ↓
              诊断报告（自动存入数据库）
```

**置信度评分**（基于数据源交叉验证）：

| 分数 | 条件 |
|------|------|
| 0.90+ | 指标 + 日志 + 调用链三源一致 |
| 0.70-0.89 | 两个数据源一致 |
| 0.50-0.69 | 只有一个数据源有信号 |
| < 0.50 | 证据不足，建议人工介入 |

---

## 其他功能

```bash
# CLI 模式
aiops diagnose --alert "服务 CPU 飙高"

# 查看历史记录
aiops history

# 查看统计报告（最近 30 天哪个服务故障最多）
aiops stats --days 30

# HTTP 服务模式（接收 AlertManager Webhook）
aiops serve --port 8000
```

浏览器打开 `http://localhost:8501`，还有「📋 历史记录」「📊 统计分析」两个页签。

---

## 常见问题

**Q: 我的服务名和 Demo 不一样怎么办？**
Agent 自动发现，你叫什么它就查什么。

**Q: Prometheus / Loki 有一个挂了影响吗？**
不会中断。Agent 识别到数据源不可用后自动跳过，用其余数据源继续。

**Q: 没有微服务数据怎么办？**
项目自带三个 Demo 微服务（`microservices/` 目录），IDEA 打开直接跑。然后用 `scripts/fault_injector.py` 注入故障测试。

**Q: 诊断一次花多少钱？**
简单故障约 5K-15K tokens，复杂故障约 20K-50K。DeepSeek 的价格，诊断一次几分钱。

---

## 项目结构

```
aiops-agent/
├── agent/              # LangGraph 多智能体核心
│   ├── config.py       # 统一配置 + LLM 重试
│   ├── utils.py        # JSON 修复等工具
│   ├── db.py           # SQLite 诊断记录
│   ├── server.py       # HTTP 服务
│   └── agents/         # Supervisor + 4 Expert + 反思
├── docker/             # Docker Compose 监控栈
├── microservices/      # Demo 微服务（可选）
├── scripts/
│   ├── start.bat       # 一键启动
│   └── fault_injector.py
├── ui/
│   └── streamlit_app.py
├── app.py              # CLI 入口
├── pyproject.toml
└── .env.example
```

---

## License

MIT
