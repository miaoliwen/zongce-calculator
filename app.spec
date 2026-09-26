# -*- mode: python ; coding: utf-8 -*-
# ============================================================
# 综测计算器 · PyInstaller 打包配置
#   打包：  python -m PyInstaller app.spec --noconfirm
#   产物：  dist/综测计算器.exe （单文件，双击即用）
#
# 说明：
#   - static/index.html 是前端页面，必须随包带上（datas）；
#   - collect_all('playwright') 会把 playwright 自带的 Node 驱动
#     （node.exe + 驱动脚本）整包打入，浏览器引擎（弹窗扫码兜底）
#     才能工作；浏览器本体用系统 Edge/Chrome（见 crawler.py 候选路径）；
#   - 默认配置（无头 + auto 引擎）走纯 HTTP 引擎，不需要任何浏览器；
#   - console 保持 True：运行日志靠它输出，隐藏后无法排查问题。
# ============================================================
from PyInstaller.utils.hooks import collect_all

_pw_datas, _pw_binaries, _pw_hidden = collect_all("playwright")

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=_pw_binaries,
    datas=[("static", "static")] + _pw_datas,
    hiddenimports=_pw_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="综测计算器",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    icon=None,
)
