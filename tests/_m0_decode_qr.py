# -*- coding: utf-8 -*-
"""
M0 技术验证：解码超星扫码登录二维码，确定安卓同机登录交互。
运行：python -m tests._m0_decode_qr   （仓库根目录执行）

产出（打印 + debug/m0/ 下存图）：
  1. uuid / enc / pcrefer 等页面参数
  2. 二维码图片解码出的原始内容（URL / 文本）
  3. 解码内容与 uuid/enc 的对应关系 → 决定安卓端能否用 deep link 直达确认页
"""
import base64
import re
import sys
from pathlib import Path
from urllib.parse import quote, urljoin

import cv2
import numpy as np
import requests

BASE = "https://ntsf.jw.chaoxing.com"
PASSPORT = "https://passport2.chaoxing.com"
SCAN_URL = (
    f"{PASSPORT}/cloudscanlogin?pcrefer="
    + quote(f"{BASE}/admin/scanLogin?", safe="")
    + "&customurl=&mobiletip="
    + quote("教务管理系统")
)
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _attr(attrs, name):
    m = re.search(r"\b" + name + r'\s*=\s*(["\'])(.*?)\1', attrs, re.I | re.S)
    return m.group(2) if m else ""


def _input_value(html, field_id):
    m = re.search(
        r'<input\b[^>]*\bid\s*=\s*["\']' + re.escape(field_id) + r'["\'][^>]*>',
        html, re.I | re.S,
    )
    return _attr(m.group(0), "value") if m else ""


def main():
    out_dir = Path("debug/m0")
    out_dir.mkdir(parents=True, exist_ok=True)
    http = requests.Session()
    http.headers.update({"User-Agent": UA})

    print(f"[1] GET {SCAN_URL}")
    r = http.get(SCAN_URL, timeout=20)
    print(f"    status={r.status_code} final_url={r.url} bytes={len(r.text)}")
    if r.status_code >= 400:
        print("!! 扫码页请求失败"); return 1
    html = r.text
    (out_dir / "scan_page.html").write_text(html, encoding="utf-8")

    uuid = _input_value(html, "uuid")
    enc = _input_value(html, "enc")
    pcrefer = _input_value(html, "pcrefer")
    print(f"[2] uuid={uuid}")
    print(f"    enc={enc}")
    print(f"    pcrefer={pcrefer}")

    m = re.search(r'<img\b[^>]*\bid=["\']ewm["\'][^>]*>', html, re.I | re.S)
    if not m:
        print("!! 未找到 <img id=ewm>"); return 1
    qr_src = _attr(m.group(0), "src")
    print(f"[3] qr img src = {qr_src[:120]}")

    png_bytes = None
    if qr_src.startswith("data:image"):
        b64 = qr_src.split(",", 1)[1]
        png_bytes = base64.b64decode(b64)
        print("    （二维码为 data URI 内嵌）")
    else:
        qr_url = urljoin(SCAN_URL, qr_src)
        print(f"    二维码图片 URL = {qr_url}")
        img = http.get(qr_url, headers={"Referer": SCAN_URL}, timeout=20)
        print(f"    status={img.status_code} type={img.headers.get('content-type')} "
              f"bytes={len(img.content)}")
        if img.status_code == 200 and "image" in img.headers.get("content-type", ""):
            png_bytes = img.content
    if not png_bytes:
        print("!! 二维码图片拉取失败"); return 1
    qr_path = out_dir / "qr.png"
    qr_path.write_bytes(png_bytes)
    print(f"    已保存 {qr_path}（{len(png_bytes)} bytes）")

    arr = np.frombuffer(png_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        print("!! 图片无法解码为图像"); return 1
    det = cv2.QRCodeDetector()
    content, _, _ = det.detectAndDecode(img)
    if not content:
        content, _, _ = det.detectAndDecodeMulti(img)[0:1] if False else ("", None, None)
    print(f"[4] 二维码解码内容：\n    {content!r}")

    print("[5] 对应关系分析")
    for label, needle in (("uuid", uuid), ("enc", enc), ("pcrefer", pcrefer)):
        if needle and needle in content:
            print(f"    内容中包含 {label} ✓")
        elif needle:
            print(f"    内容中不含 {label}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
