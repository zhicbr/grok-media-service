@echo off
chcp 65001 > nul
title Grok Media Service
cd /d "%~dp0"

echo ========================================================
echo         Grok Media Service - 本地图像与视频服务
echo ========================================================
echo.
echo 正在检查 Python 环境...
python --version > nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未找到 Python，请确保已安装 Python 并添加到 PATH 环境变量。
    pause
    exit /b 1
)

echo 正在启动服务 (默认端口 8088)...
echo Web 演示界面: http://127.0.0.1:8088/
echo 接口地址:     http://127.0.0.1:8088/v1/images/generations
echo.
python server.py

pause
