@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo.
echo   ============================================================
echo     AIOps Agent - 一键启动
echo   ============================================================
echo.
echo   此脚本将自动完成:
echo     1. 检查 Docker ^& Python 环境
echo     2. 启动监控基础设施（Prometheus + Loki + Jaeger + Grafana）
echo     3. 启动诊断引擎
echo     4. 启动 Web UI 并打开浏览器
echo.

:: ========================================================================
:: 1. 检查 Docker
:: ========================================================================
echo   [1/5] 检查 Docker ...
docker info >nul 2>&1
if %errorlevel% neq 0 (
    echo   [ERROR] Docker 未运行
    echo   请先启动 Docker Desktop（右下角图标右键 ^> Start）
    echo   下载: https://www.docker.com/products/docker-desktop
    pause
    exit /b 1
)
echo         Docker 已就绪

:: ========================================================================
:: 2. 创建/检查 .env 配置
:: ========================================================================
echo   [2/5] 检查配置文件 ...

if not exist ".env" (
    copy .env.example .env >nul
)

findstr /C:"sk-your-api-key-here" .env >nul 2>&1
if %errorlevel% equ 0 (
    echo.
    echo   ============================================================
    echo     [重要] DEEPSEEK_API_KEY 未配置！
    echo.
    echo     .env 文件已自动创建，现在用记事本打开。
    echo     请填入你的 DeepSeek API Key，保存后关闭记事本。
    echo     申请地址: https://platform.deepseek.com
    echo   ============================================================
    echo.
    start notepad .env
    echo   按任意键继续...
    pause >nul
)

echo         .env 已就绪

:: ========================================================================
:: 3. 启动 Docker Compose
:: ========================================================================
echo   [3/5] 启动服务（首次构建镜像需要几分钟）...
cd /d "%~dp0\..\docker"

:: 使用项目根目录的 .env
docker compose --env-file ../.env up -d --build 2>&1 | findstr /V "warning"
cd /d "%~dp0\.."

echo         等待服务就绪（约 20 秒）...
timeout /t 8 /nobreak >nul

:: 快速检查关键服务
curl -s http://localhost:9090/api/v1/query?query=up >nul 2>&1 && echo         Prometheus  :9090  已就绪 || echo         [等待中] Prometheus ...
curl -s http://localhost:16686/api/services >nul 2>&1 && echo         Jaeger      :16686 已就绪 || echo         [等待中] Jaeger ...
curl -s http://localhost:8000/health >nul 2>&1 && echo         Agent API   :8000  已就绪 || echo         [等待中] Agent API ...

:: 再等一下让所有服务完全启动
timeout /t 8 /nobreak >nul

:: ========================================================================
:: 4. 检查 Python（Streamlit 需要在宿主机运行）
:: ========================================================================
echo   [4/5] 检查 Python 环境 ...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo   [WARN]  未找到 Python，Web UI 将无法启动
    echo          但 Agent API 已在 Docker 中运行（http://localhost:8000）
    echo          安装 Python 后可运行: streamlit run ui/streamlit_app.py
    goto :skip_ui
)

if not exist "venv\Scripts\python.exe" (
    echo         正在创建虚拟环境并安装依赖...
    python -m venv venv
    venv\Scripts\pip install -e . --quiet 2>nul
)

:: ========================================================================
:: 5. 启动 Web UI
:: ========================================================================
echo   [5/5] 启动 Web UI ...

echo.
echo   ============================================================
echo     ✅ 全部就绪！
echo.
echo     Web UI:     http://localhost:8501
echo     Agent API:  http://localhost:8000
echo     Prometheus: http://localhost:9090
echo     Jaeger:     http://localhost:16686
echo     Grafana:    http://localhost:3000
echo.
echo     在 Web UI 输入告警即可开始诊断。
echo     按 Ctrl+C 可停止 Web UI（Docker 服务继续运行）。
echo   ============================================================
echo.

start http://localhost:8501
venv\Scripts\streamlit run ui\streamlit_app.py --server.headless true 2>nul
goto :end

:skip_ui
echo.
echo   ============================================================
echo     ✅ Docker 服务已就绪！
echo.
echo     Agent API:  http://localhost:8000
echo     Prometheus: http://localhost:9090
echo     Jaeger:     http://localhost:16686
echo     Grafana:    http://localhost:3000
echo.
echo     Web UI 需要 Python，安装后运行:
echo       pip install -e .
echo       streamlit run ui/streamlit_app.py
echo   ============================================================
echo.
pause

:end
