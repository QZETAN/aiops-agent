@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo.
echo   ============================================================
echo     AIOps Agent — 一键启动
echo   ============================================================
echo.

:: ========================================================================
:: 1. 检查 Docker
:: ========================================================================
docker info >nul 2>&1
if %errorlevel% neq 0 (
    echo   [ERROR] Docker 未运行
    echo.
    echo   Windows: 启动 Docker Desktop（右下角图标右键 - Start）
    echo   Linux:   sudo systemctl start docker
    echo.
    pause
    exit /b 1
)
echo   [OK] Docker 已就绪

:: ========================================================================
:: 2. 检查 .env 中的 API Key
:: ========================================================================
if not exist ".env" copy .env.example .env >nul

findstr /C:"sk-your-api-key-here" .env >nul 2>&1
if %errorlevel% equ 0 (
    echo.
    echo   ============================================================
    echo     [重要] 请先配置 API Key！
    echo.
    echo     .env 文件已创建，现在打开记事本。
    echo     把 DEEPSEEK_API_KEY 改成你的真实 Key，保存关闭。
    echo     申请地址: https://platform.deepseek.com
    echo   ============================================================
    echo.
    start notepad .env
    echo   改好后按任意键继续...
    pause >nul
)

echo   [OK] .env 配置已检查

:: ========================================================================
:: 3. 启动 Docker Compose
:: ========================================================================
echo.
echo   ============================================================
echo     启动服务（首次需要拉取镜像和构建，约 3-5 分钟）
echo   ============================================================
echo.

cd /d "%~dp0\..\docker"
docker compose --env-file ../.env up -d --build
cd /d "%~dp0\.."

echo.
echo   等待所有服务就绪...

:: 等待 + 逐个检查
timeout /t 10 /nobreak >nul
curl -s http://localhost:8000/health >nul 2>&1 && echo   [OK] Agent API   :8000   || echo   [..] Agent API 启动中...
timeout /t 5 /nobreak >nul
curl -s http://localhost:9090/api/v1/query?query=up >nul 2>&1 && echo   [OK] Prometheus  :9090   || echo   [..] Prometheus 启动中...
curl -s http://localhost:16686/api/services >nul 2>&1 && echo   [OK] Jaeger      :16686  || echo   [..] Jaeger 启动中...
timeout /t 5 /nobreak >nul

:: ========================================================================
:: 4. 打开浏览器
:: ========================================================================
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
echo     浏览器即将打开 Web UI，输入告警即可诊断。
echo   ============================================================
echo.

start http://localhost:8501

pause
