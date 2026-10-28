"""RUSSIA VPN 节点池 Demo Server（只读 + 现场测活）。

启动:
  python demo_server.py [port]        默认 8912

端点:
  GET /                      demo 页面
  GET /api/stats             节点池汇总（存活率/国家分布/协议/延迟分位）
  GET /api/nodes             节点列表（country/alive/q/page/page_size 过滤）
  GET /api/node/<id>         单节点详情（含 ovpn 原文、凭证明细脱敏展示）
  GET /api/node/<id>/config  下载该节点的 .ovpn
  POST /api/probe            现场测活 {"id": 1024} 或 {"country":"Russia"} （限 20 个）
  GET /api/alive?format=json 存活节点清单（供代理池/对接方消费）
  GET /healthz
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

WORK = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(WORK, "output")
DETAILS = os.path.join(OUT, "details.json")
PROBE = os.path.join(OUT, "probe_results.json")
OVPN = os.path.join(OUT, "ovpn")
HTML = os.path.join(WORK, "demo.html")
OPENVPN = os.path.join(WORK, "bin", "openvpn_x", "OpenVPN", "bin", "openvpn.exe")

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8912
PROBE_LOCK = threading.Semaphore(4)  # 现场测活并发上限，避免打满本机

_cache = {"nodes": None, "mtime": 0, "probe": None, "probe_mtime": 0}


def load_nodes():
    """details.json + probe_results.json 合并成一个节点表（带缓存）。"""
    st = os.path.getmtime(DETAILS) if os.path.exists(DETAILS) else 0
    if _cache["nodes"] is not None and st == _cache["mtime"]:
        nodes = _cache["nodes"]
    else:
        rows = json.load(open(DETAILS, encoding="utf-8")) if st else []
        nodes = []
        for r in rows:
            if not r.get("config_len"):
                continue
            nodes.append({
                "id": r.get("id"),
                "name": r.get("name"),
                "country": r.get("country") or "Unknown",
                "slug": r.get("slug"),
                "remote": r.get("remote"),
                "port": r.get("port"),
                "proto": r.get("proto"),
                "file": r.get("file"),
                "updated_at": r.get("updated_at"),
                "has_cert": r.get("has_cert"),
                "has_tls_crypt": r.get("has_tls_crypt"),
            })
        _cache["nodes"], _cache["mtime"] = nodes, st

    pst = os.path.getmtime(PROBE) if os.path.exists(PROBE) else 0
    if _cache["probe"] is None or pst != _cache["probe_mtime"]:
        rows = json.load(open(PROBE, encoding="utf-8")) if pst else []
        _cache["probe"] = {r["file"]: r for r in rows}
        _cache["probe_mtime"] = pst

    probe = _cache["probe"]
    for n in nodes:
        fname = os.path.basename(n["file"]) if n.get("file") else ""
        p = probe.get(fname)
        n["alive"] = (p or {}).get("verdict")
        n["elapsed"] = (p or {}).get("elapsed")
        n["tunnel_ip"] = (p or {}).get("tunnel_ip")
        n["tls_cipher"] = (p or {}).get("tls_cipher")
    return nodes


def stats(nodes):
    probed = [n for n in nodes if n["alive"]]
    alive = [n for n in probed if n["alive"] == "ALIVE"]
    lat = sorted(n["elapsed"] for n in alive if n["elapsed"])
    def pct(p):
        if not lat:
            return None
        k = min(len(lat) - 1, int(len(lat) * p))
        return lat[k]
    by_country = Counter(n["country"] for n in nodes)
    alive_by_country = Counter(n["country"] for n in alive)
    return {
        "total_nodes": len(nodes),
        "probed": len(probed),
        "alive": len(alive),
        "alive_rate": round(len(alive) / len(probed) * 100, 1) if probed else None,
        "authfail": sum(1 for n in probed if n["alive"] == "AUTHFAIL"),
        "dead": sum(1 for n in probed if n["alive"] == "DEAD"),
        "countries": len(by_country),
        "countries_alive": len(alive_by_country),
        "top_countries": by_country.most_common(20),
        "top_countries_alive": alive_by_country.most_common(20),
        "latency_s": {"p50": pct(0.5), "p90": pct(0.9), "p95": pct(0.95),
                      "min": lat[0] if lat else None, "max": lat[-1] if lat else None},
        "protos": dict(Counter(f"{n['proto']}/{n['port']}" for n in nodes)),
        "upstream": "https://site45.newmobilejumpstech.com/api/",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


def probe_node(node):
    """对单个节点跑一次真实 OpenVPN 握手（dev null，不改本机路由/DNS）。"""
    cfg = os.path.join(WORK, node["file"]) if node.get("file") else None
    if not cfg or not os.path.exists(cfg):
        return {"verdict": "ERROR", "error": "config missing"}
    tmp = os.path.join(WORK, "tmp_demo_probe")
    os.makedirs(tmp, exist_ok=True)
    dst = os.path.join(tmp, os.path.basename(cfg))
    with open(cfg, encoding="utf-8", errors="ignore") as f:
        body = [ln for ln in f if "block-outside-dns" not in ln.lower()]
    open(dst, "w", encoding="utf-8", newline="\n").writelines(body)
    cmd = [OPENVPN, "--config", dst, "--dev", "null", "--ifconfig-noexec",
           "--route-noexec", "--connect-timeout", "8", "--connect-retry-max", "1",
           "--server-poll-timeout", "8", "--ping-exit", "15", "--resolv-retry", "0",
           "--auth-nocache", "--verb", "3", "--disable-dco"]
    t0 = time.time()
    with PROBE_LOCK:
        lines = []
        done = threading.Event()
        markers = ("PUSH_REPLY", "AUTH_FAILED", "Exiting due to fatal error",
                   "All connections have been connect-retry-max unsuccessful",
                   "TLS Error", "TLS key negotiation failed")
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, bufsize=1, errors="ignore",
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

        def reader():
            try:
                for line in p.stdout:
                    lines.append(line)
                    if any(mk in line for mk in markers):
                        break
            except Exception:
                pass
            finally:
                done.set()

        threading.Thread(target=reader, daemon=True).start()
        done.wait(timeout=25)
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(timeout=5)
    text = "".join(lines)
    elapsed = round(time.time() - t0, 2)
    if "PUSH_REPLY" in text:
        m = re.search(r"ifconfig\s+(\d+\.\d+\.\d+\.\d+)", text)
        tls = re.search(r"Control Channel: (TLSv[\d.]+), cipher (\S+(?: \S+)?)", text)
        return {"verdict": "ALIVE", "elapsed": elapsed,
                "tunnel_ip": m.group(1) if m else None,
                "tls_version": tls.group(1) if tls else None,
                "tls_cipher": tls.group(2).rstrip(",") if tls else None,
                "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    why = ("AUTHFAIL" if "AUTH_FAILED" in text else "DEAD")
    return {"verdict": why, "elapsed": elapsed, "error": "no handshake",
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S")}


def mask_config(cfg_text):
    """demo 展示用：私钥/证书内容折叠，避免整份凭证在页面上滚屏。"""
    out, skip = [], False
    for ln in cfg_text.splitlines():
        if re.match(r"^<(ca|cert|key|tls-crypt|tls-auth)>", ln):
            out.append(ln)
            out.append("    [%d 行块已折叠]" % 0)
            skip = True
            continue
        if re.match(r"^</(ca|cert|key|tls-crypt|tls-auth)>", ln):
            out.append(ln)
            skip = False
            continue
        if not skip:
            out.append(ln)
    return "\n".join(out)


class Handler(BaseHTTPRequestHandler):
    server_version = "RussiaVPNPoolDemo/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False, indent=1).encode()
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        p = u.path

        if p == "/healthz":
            return self._send(200, {"ok": True})
        if p in ("/", "/index.html"):
            if not os.path.exists(HTML):
                return self._send(404, "demo.html missing", "text/plain")
            return self._send(200, open(HTML, encoding="utf-8").read(),
                              "text/html; charset=utf-8")
        if p == "/api/stats":
            return self._send(200, stats(load_nodes()))
        if p == "/api/nodes":
            nodes = load_nodes()
            country = q.get("country", "").strip().lower()
            alive = q.get("alive", "").strip().upper()
            kw = q.get("q", "").strip().lower()
            if country:
                nodes = [n for n in nodes if country in n["country"].lower()]
            if alive:
                nodes = [n for n in nodes if n["alive"] == alive]
            if kw:
                nodes = [n for n in nodes
                         if kw in (n["name"] or "").lower()
                         or kw in (n["remote"] or "").lower()
                         or kw in (n["country"] or "").lower()]
            try:
                page = max(1, int(q.get("page", 1)))
                size = min(500, max(1, int(q.get("page_size", 50))))
            except ValueError:
                page, size = 1, 50
            total = len(nodes)
            items = nodes[(page - 1) * size: page * size]
            return self._send(200, {"total": total, "page": page, "page_size": size,
                                    "items": items})
        if p == "/api/alive":
            nodes = [n for n in load_nodes() if n["alive"] == "ALIVE"]
            if q.get("format") == "lines":
                lines = [f"{n['remote']}:{n['port']}\t{n['proto']}\t{n['country']}\t{n['name']}"
                         for n in nodes]
                return self._send(200, "\n".join(lines), "text/plain; charset=utf-8")
            return self._send(200, {"count": len(nodes), "nodes": nodes})
        m = re.match(r"^/api/node/(\d+)$", p)
        if m:
            nid = int(m.group(1))
            for n in load_nodes():
                if n["id"] == nid:
                    cfg_path = os.path.join(WORK, n["file"]) if n.get("file") else None
                    text = ""
                    if cfg_path and os.path.exists(cfg_path):
                        text = mask_config(open(cfg_path, encoding="utf-8",
                                                errors="ignore").read())
                    return self._send(200, {"node": n, "config_preview": text})
            return self._send(404, {"error": "node not found"})
        m = re.match(r"^/api/node/(\d+)/config$", p)
        if m:
            nid = int(m.group(1))
            for n in load_nodes():
                if n["id"] == nid and n.get("file"):
                    cfg_path = os.path.join(WORK, n["file"])
                    if os.path.exists(cfg_path):
                        body = open(cfg_path, "rb").read()
                        return self._send(200, body, "application/x-openvpn-profile",
                                          {"Content-Disposition":
                                           f'attachment; filename="node{nid}.ovpn"'})
            return self._send(404, {"error": "config not found"})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        u = urlparse(self.path)
        if u.path != "/api/probe":
            return self._send(404, {"error": "not found"})
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            return self._send(400, {"error": "bad json"})
        nodes = load_nodes()
        if payload.get("id"):
            target = [n for n in nodes if n["id"] == int(payload["id"])][:20]
        elif payload.get("country"):
            c = str(payload["country"]).lower()
            target = [n for n in nodes if c in n["country"].lower()][:20]
        elif payload.get("files"):
            want = set(payload["files"])
            target = [n for n in nodes if os.path.basename(n.get("file") or "") in want][:20]
        else:
            return self._send(400, {"error": "need id / country / files"})
        if not target:
            return self._send(404, {"error": "no matching node"})
        results = [{"id": n["id"], "name": n["name"], "country": n["country"],
                    **probe_node(n)} for n in target]
        return self._send(200, {"count": len(results), "results": results})


def main():
    if not os.path.exists(DETAILS):
        print(f"[FATAL] {DETAILS} 不存在，先跑 tools/harvest.py")
        return 1
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    nodes = load_nodes()
    print(f"[russia-vpn-demo] {len(nodes)} nodes loaded, listening on http://127.0.0.1:{PORT}")
    print(f"[russia-vpn-demo] openvpn engine: {OPENVPN} (exists={os.path.exists(OPENVPN)})")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
