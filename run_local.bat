@echo off
rem ============================================================
rem 综测计算器 · 本机启动脚本（Windows）
rem   ZC_HEADLESS=0：允许“改用弹窗扫码”，二维码被网络拦截时可兜底
rem   ZC_IDLE_CLOSE=1800：空闲 30 分钟自动关浏览器（本机可设 0 关闭）
rem ============================================================
setlocal
cd /d "%~dp0"
set ZC_HEADLESS=0
set ZC_IDLE_CLOSE=1800
set PYTHONIOENCODING=utf-8
echo [1/2] 检查依赖...
python -c "import flask, playwright" 2>nul || (
  echo 依赖缺失，正在安装...
  python -m pip install -r requirements.txt
  python -m playwright install chromium
)
echo [2/2] 启动服务：http://127.0.0.1:8765
echo.
python app.py
endlocal