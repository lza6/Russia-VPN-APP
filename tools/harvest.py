"""RUSSIA VPN (site45.newmobilejumpstech.com) 全量 OpenVPN 配置收割。

上游: GET https://site45.newmobilejumpstech.com/api/allservers       -> 节点目录
      GET https://site45.newmobilejumpstech.com/api/detail/{slug}    -> 完整 ovpn 配置
无鉴权，带 429 限流；本机需经 socks5h://127.0.0.1:10808（域名被 SNI 阻断，直连 RST）。

用法:
  python tools/harvest.py [rate_req_per_sec] [workers]
输出:
  recon/allservers.json   节点目录
  output/details.json     每节点解析结果（断点续传）
  output/ovpn/*.ovpn      全量配置文件
"""
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request

WORK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECON = os.path.join(WORK, "recon")
OUT = os.path.join(WORK, "output")
CFG_DIR = os.path.join(OUT, "ovpn")
BASE = "https://site45.newmobilejumpstech.com/api/"
H = {"User-Agent": "Dart/3.5 (dart:io)", "Accept": "*/*"}
_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler(
        {"http": "socks5h://127.0.0.1:10808", "https": "socks5h://127.0.0.1:10808"}
    )
)


class Pacer:
    """全局节流器：按 interval 串行放行，遇 429 自动降速、成功后缓慢提速。"""

    def __init__(self, rate):
        self.lock = threading.Lock()
        self.interval = 1.0 / rate
        self.next_at = 0.0

    def wait(self):
        with self.lock:
            now = time.time()
            at = max(now, self.next_at)
            self.next_at = at + self.interval
        if at > now:
            time.sleep(at - now)

    def slow_down(self, factor=2.0, max_interval=4.0):
        with self.lock:
            self.interval = min(self.interval * factor, max_interval)

    def speed_up(self, factor=1.05, min_interval=0.4):
        with self.lock:
            self.interval = max(self.interval / factor, min_interval)


PACER = Pacer(float(sys.argv[1]) if len(sys.argv) > 1 else 2.0)


def get(path, timeout=30, tries=6):
    delay = 5.0
    for i in range(tries):
        PACER.wait()
        try:
            req = urllib.request.Request(BASE + path, headers=H)
            with _OPENER.open(req, timeout=timeout) as r:
                raw = r.read().decode("utf-8")
            PACER.speed_up()
            return json.loads(raw)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                PACER.slow_down()
                print(f"  429 -> backoff {delay:.0f}s (interval={PACER.interval:.2f})", flush=True)
                time.sleep(delay)
                delay = min(delay * 1.6, 45)
                continue
            if e.code in (500, 502, 503, 504):
                time.sleep(delay)
                delay = min(delay * 1.6, 45)
                continue
            return {"_error": f"HTTP {e.code}"}
        except Exception as e:
            if i == tries - 1:
                return {"_error": f"{type(e).__name__}: {e}"}
            time.sleep(delay)
            delay = min(delay * 1.6, 45)
    return {"_error": "retry_exhausted"}


def safe_name(d):
    n = re.sub(r"[^A-Za-z0-9._-]+", "_", d.get("name") or str(d.get("id")))
    c = re.sub(r"[^A-Za-z0-9]+", "_", d.get("country") or "xx")
    return f"{d['id']}_{c}_{n}"[:120]


def parse(item, data):
    cfg = data.get("config") or ""
    path = ""
    if cfg.strip():
        path = os.path.join(CFG_DIR, safe_name(data) + ".ovpn")
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(cfg)
    rmatch = re.search(r"^remote\s+(\S+)\s+(\d+)", cfg, re.M)
    pmatch = re.search(r"^proto\s+(\S+)", cfg, re.M)
    return {
        "id": data.get("id"),
        "name": data.get("name"),
        "country": data.get("country"),
        "slug": item["slug"],
        "remote": rmatch.group(1) if rmatch else None,
        "port": int(rmatch.group(2)) if rmatch else None,
        "proto": pmatch.group(1) if pmatch else None,
        "hastcp": data.get("hastcp"),
        "hasudp": data.get("hasudp"),
        "status": data.get("status"),
        "username": data.get("username"),
        "has_password": bool(data.get("password")),
        "has_cert": "-----BEGIN CERTIFICATE-----" in cfg,
        "has_key": "-----BEGIN PRIVATE KEY-----" in cfg or "-----BEGIN RSA PRIVATE KEY-----" in cfg,
        "has_tls_crypt": "tls-crypt" in cfg or "tls-auth" in cfg,
        "config_len": len(cfg),
        "file": os.path.relpath(path, WORK) if path else None,
        "updated_at": data.get("updated_at"),
    }


def main():
    os.makedirs(CFG_DIR, exist_ok=True)
    os.makedirs(RECON, exist_ok=True)
    allservers_path = os.path.join(RECON, "allservers.json")
    if os.path.exists(allservers_path):
        servers = json.load(open(allservers_path, encoding="utf-8"))["data"]
        print(f"reuse allservers.json ({len(servers)} nodes)", flush=True)
    else:
        d = get("allservers", timeout=60)
        if "_error" in d:
            print("allservers failed:", d["_error"])
            return 1
        json.dump(d, open(allservers_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        servers = d["data"]
        print(f"allservers: {len(servers)} nodes", flush=True)

    done_path = os.path.join(OUT, "details.json")
    rows = json.load(open(done_path, encoding="utf-8")) if os.path.exists(done_path) else []
    have = {r["slug"] for r in rows if r.get("config_len")}
    todo = [s for s in servers if s["slug"] not in have]
    print(f"total={len(servers)} have={len(have)} todo={len(todo)}", flush=True)

    from concurrent.futures import ThreadPoolExecutor, as_completed
    workers = int(sys.argv[2]) if len(sys.argv) > 2 else 6
    t0 = time.time()

    def work(item):
        d = get("detail/" + item["slug"])
        if "_error" in d:
            return {"id": item["id"], "name": item["name"], "country": item["country"],
                    "slug": item["slug"], "error": d["_error"]}
        return parse(item, d.get("data") or {})

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(work, it) for it in todo]
        for fu in as_completed(futs):
            rows.append(fu.result())
            done += 1
            if done % 25 == 0 or done == len(todo):
                json.dump(rows, open(done_path, "w", encoding="utf-8"), indent=1,
                          ensure_ascii=False)
                ok = sum(1 for r in rows if r.get("config_len"))
                el = time.time() - t0
                rate = done / el if el else 0
                print(f"  {done}/{len(todo)} ok={ok} {el:.0f}s {rate:.2f}/s "
                      f"interval={PACER.interval:.2f}", flush=True)
    json.dump(rows, open(done_path, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    ok = sum(1 for r in rows if r.get("config_len"))
    print(f"done ok={ok} err={len(rows)-ok} elapsed={time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
