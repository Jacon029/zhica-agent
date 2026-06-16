@echo off
chcp 65001 >nul
title 智采-Agent 企业采购智能助手

:: ─── 启动入口 ───
:: 双击此文件即可启动智采-Agent，浏览器将自动打开

cd /d "%~dp0"

:: 检查 API Key 配置（优先读 .env 文件，其次读环境变量）
if not defined DEEPSEEK_API_KEY (
    if exist ".env" (
        for /f "tokens=2 delims==" %%a in ('findstr "DEEPSEEK_API_KEY" .env') do set DEEPSEEK_API_KEY=%%a
    )
)
if not defined DEEPSEEK_API_KEY (
    echo.
    echo ╔════════════════════════════════════════════╗
    echo ║  zhicai-Agent v1.0                         ║
    echo ║  Enterprise Procurement AI Assistant        ║
    echo ╚════════════════════════════════════════════╝
    echo.
    echo [INFO] .env  file loaded.
    echo         If API key is set in .env, you're good to go.
    echo.
    timeout /t 2 >nul
)

:: 启动 Streamlit
:: streamlit 会自动在默认浏览器中打开
streamlit run app.py --server.headless true

pause
