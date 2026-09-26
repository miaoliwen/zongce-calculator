"""诊断：复刻 App 扫码前的请求序列，观察 getauthstatus 实际返回。

用途：定位「接口返回了非 JSON 页面（HTTP 200）」错误发生在哪个请求。
只做 1 次扫码页请求 + 4 次轮询（3s 间隔），不扫码、不对教务系统施压。
运行：python debug/_diag_getauthstatus.py
"""
import re
import sys
import time

import requests

sys.path.insert(0, ".")
from crawler import SCAN_URL  # noqa: E402
from http_crawler import AUTHSTATUS_URL, UA, _input_value  # noqa: E402

UA_ALONE = {"User-Agent": UA}


def brief(body: str, n: int = 300) -> str:
    body = re.sub(r"\s+", " ", body).strip()
    return body[:n]


def main():
    s = requests.Session()
    s.headers.update(UA_ALONE)

    print("=== 1. GET cloudscanlogin ===")
    r = s.get(SCAN_URL, timeout=20)
    print("status:", r.status_code, "ct:", r.headers.get("content-type"))
    uuid = _input_value(r.text, "uuid")
    enc = _input_value(r.text, "enc")
    print("uuid:", uuid[:60], "enc:", enc[:30])
    if not uuid:
        for m in re.finditer(r"<input[^>]*>", r.text):
            print("input tag:", m.group(0)[:200])
        print("---- 页面 script/uuid 相关片段 ----")
        for m in re.finditer(r".{80}uuid.{120}", r.text):
            print(brief(m.group(0), 260))
        return

    print("\n=== 2. POST getauthstatus ×4（间隔 3s，不扫码）===")
    for i in range(4):
        r2 = s.post(
            AUTHSTATUS_URL,
            data={"enc": enc, "uuid": uuid},
            timeout=20,
        )
        ct = r2.headers.get("content-type", "")
        print(f"[{i+1}] status={r2.status_code} ct={ct!r} body[:200]={brief(r2.text, 200)!r}")
        time.sleep(3)

    print("\n=== 3. 同请求补发 X-Requested-With/Origin/Referer 头再试一次 ===")
    r3 = s.post(
        AUTHSTATUS_URL,
        data={"enc": enc, "uuid": uuid},
        headers={
            "X-Requested-With": "XMLHttpRequest",
            "Referer": SCAN_URL,
            "Origin": "https://passport2.chaoxing.com",
            "Accept": "application/json, text/javascript, */*; q=0.01",
        },
        timeout=20,
    )
    print(f"status={r3.status_code} ct={r3.headers.get('content-type')!r} "
          f"body[:200]={brief(r3.text, 200)!r}")


if __name__ == "__main__":
    main()
