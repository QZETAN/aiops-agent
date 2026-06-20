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
echo   [1/5] 检查 Docker ...
docker info >nul 2>&1
if %errorlevel% neq 0 (
    echo   [ERROR] Docker 未运行！
    echo.
    echo   请先启动 Docker Desktop，然后重新运行此脚本。
    echo   下载地址：https://www.docker.com/products/docker-desktop
    echo.
    pause
    exit /b 1
)
echo          Docker 已就绪

:: ========================================================================
:: 2. 启动监控基础设施
:: ========================================================================
echo   [2/5] 启动监控基础设施（Prometheus + Loki + Jaeger + Grafana）...
cd /d "%~dp0\..\docker"
docker compose up -d 2>nul
cd /d "%~dp0\.."

:: 等待容器启动
echo         等待服务就绪...
timeout /t 15 /nobreak >nul

:: ========================================================================
:: 3. 检查 Python 环境
:: ========================================================================
echo   [3/5] 检查 Python 环境 ...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo   [ERROR] 未找到 Python！
    echo   请先安装 Python 3.11+ （https://www.python.org/downloads/）
    pause
    exit /b 1
)

:: 创建虚拟环境（首次）
if not exist "venv\Scripts\python.exe" (
    echo         正在创建虚拟环境...
    python -m venv venv
    echo         正在安装依赖（首次运行需要几分钟）...
    venv\Scripts\pip install -e . --quiet 2>nul
    echo         依赖安装完成
)

:: ========================================================================
:: 4. 检查配置文件
:: ========================================================================
echo   [4/5] 检查配置文件 ...

if not exist ".env" (
    copy .env.example .env >nul
    echo.
    echo   ============================================================
    echo     [重要] 请先配置 .env 文件！
    echo     .env 文件已自动创建，请用记事本打开并填入你的 API Key：
    echo.
    echo     DEEPSEEK_API_KEY=sk-你的key
    echo.
    echo     申请地址：https://platform.deepseek.com
    echo   ============================================================
    echo.
    start notepad .env
    pause
)

:: 验证 API Key 是否已填写
findstr /C:"sk-your-api-key-here" .env >nul 2>&1
if %errorlevel% equ 0 (
    echo.
    echo   [WARN]  .env 中的 DEEPSEEK_API_KEY 还是占位符，未填写真实 Key
    echo          诊断功能将无法使用
    echo.
    echo   请编辑 .env 文件，将 DEEPSEEK_API_KEY 改为你的真实 Key
    echo   申请地址：https://platform.deepseek.com
    echo.
    start notepad .env
    pause
)

echo         .env 配置已检查

:: ========================================================================
:: 5. 启动 Web UI
:: ========================================================================
echo   [5/5] 启动 Web UI ...

echo.
echo   ============================================================
echo     ✅ 一切就绪！
echo.
echo     浏览器打开: http://localhost:8501
echo.
echo     提示：
echo       - 监控数据源: Prometheus(:9090) Loki(:3100) Jaeger(:16686)
echo       - 如果没有微服务数据，可以用 Demo 微服务测试（见 README）
echo   ============================================================
echo.

:: 打开浏览器
start http://localhost:8501

:: 启动 Streamlit
venv\Scripts\streamlit run ui\streamlit_app.py --server.headless true 2>nul

pause
