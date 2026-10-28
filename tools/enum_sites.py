"""枚举 mobilejumps 白标平台的 siteNN 实例（同栈不同站）。

背景: RUSSIA VPN 用 site45.newmobilejumpstech.com，INDONESIA VPN 用 site40.mobilejumps.org。
同一套 Laravel 白标后端在不同 siteNN / 不同域名下并行部署，每站节点池不同。
枚举目的是评估"可发现节点总量"，不改变合规判断（节点均为平台方租用的数据中心）。

用法: python tools/enum_sites.py [--domain newmobilejumpstech.com] [--from 1] [--to 60]
输出: recon/sites.json
"""
import argparse
import json
import os
import sys
import time
import urllib.request

WORK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECON = os.path.join(WORK, "recon")
PROXY = {"http": "socks5h://127.0.0.1:10808", "https": "socks5h://127.0.0.1:10808"}
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler(PROXY))
H = {"User-Agent": "Dart/3.5 (dart:io)", "Accept": "*/*"}


def fetch(url, timeout=18):
    try:
        with OPENER.open(urllib.request.Request(url, headers=H), timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception as e:
        return None, str(e).encode()[:120]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", default="newmobilejumpstech.com")
    ap.add_argument("--from", dest="lo", type=int, default=1)
    ap.add_argument("--to", dest="hi", type=int, default=60)
    ap.add_argument("--interval", type=float, default=0.5)
    args = ap.parse_args()

    os.makedirs(RECON, exist_ok=True)
    out = []
    for n in range(args.lo, args.hi + 1):
        host = f"site{n}.{args.domain}"
        for path in ("/api/allservers", "/public/api/allservers"):
            code, body = fetch(f"https://{host}{path}")
            tag = f"{host}{path}"
            if code == 200 and body[:1] == b"{":
                try:
                    d = json.loads(body.decode("utf-8", "ignore"))
                    cnt = len(d.get("data") or [])
                    if cnt:
                        out.append({"host": host, "path": path, "count": cnt,
                                    "sample": (d["data"][0].get("name") if d["data"] else None)})
                        print(f"  {tag} -> 200 {cnt} nodes", flush=True)
                        break
                except Exception:
                    pass
            elif code not in (None, 404):
                print(f"  {tag} -> {code}", flush=True)
        time.sleep(args.interval)

    json.dump(out, open(os.path.join(RECON, "sites.json"), "w", encoding="utf-8"),
              indent=1, ensure_ascii=False)
    total = sum(s["count"] for s in out)
    print(f"\n=== {len(out)} 站可用, 节点合计 {total} (去重前)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
