"""RUSSIA VPN 节点真实握手测活（Windows 侧 openvpn.exe，dev null 模式）。

安全约束（硬性）:
  - --dev null + --ifconfig-noexec + --route-noexec：不建隧道、不改路由、不改 DNS
  - 只开启 TLS 控制信道 + PUSH_REQUEST，验证证书认证与节点可用性
  - 绝不与宿主 v2rayN/xray(10808) 交互，本工具不发任何 HTTP

存活判据（严格）:
  ALIVE    = 收到 PUSH_REPLY（TLS 握手 + 证书双向认证 + 服务端下发 ifconfig 全通）
  AUTHFAIL = TLS 层报证书/密码错误（节点活着但凭证无效）
  DEAD     = 超时无任何 TLS 响应

用法:
  python tools/probe.py --workers 32 [--limit N] [--only-alive-slugs]
输出:
  output/probe_results.json
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

WORK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(WORK, "output")
OVPN = os.path.join(OUT, "ovpn")
TMP = os.path.join(WORK, "tmp_probe")
OPENVPN = os.path.join(WORK, "bin", "openvpn_x", "OpenVPN", "bin", "openvpn.exe")

# 测活用附加参数：dev null 不建隧道，noexec 不改系统状态
EXTRA = [
    "--dev", "null",
    "--ifconfig-noexec",
    "--route-noexec",
    "--connect-timeout", "8",
    "--connect-retry-max", "1",
    "--server-poll-timeout", "8",
    "--ping-exit", "20",
    "--resolv-retry", "0",
    "--auth-nocache",
    "--verb", "3",
    "--disable-dco",
]

LOCK = threading.Lock()
COUNTER = {"done": 0, "alive": 0, "total": 0}
T0 = time.time()


def sanitize_config(src, dst):
    """去掉 block-outside-dns（Windows 上会触发 WFP block-dns fatal），其余原样保留。"""
    lines = []
    with open(src, encoding="utf-8", errors="ignore") as f:
        for ln in f:
            low = ln.lower()
            if "block-outside-dns" in low:
                continue
            lines.append(ln)
    with open(dst, "w", encoding="utf-8", newline="\n") as f:
        f.writelines(lines)
    return dst


def classify(text, rc):
    if "PUSH_REPLY" in text:
        return "ALIVE", None
    if "AUTH_FAILED" in text:
        return "AUTHFAIL", "AUTH_FAILED"
    if "TLS key negotiation failed" in text or "TLS Error" in text:
        return "DEAD", "tls_error"
    if "All connections have been connect-retry-max unsuccessful" in text:
        return "DEAD", "no_response"
    if "Connection reset" in text or "Connection refused" in text:
        return "DEAD", "conn_refused"
    if rc == 0:
        return "DEAD", "exited_clean_without_push"
    return "DEAD", "timeout_or_unknown"


def parse_alive(text):
    info = {}
    m = re.search(r"PUSH: Received control message: '([^']+)'", text)
    if m:
        push = m.group(1)
        info["push_reply"] = push[:400]
        ip = re.search(r"ifconfig\s+(\d+\.\d+\.\d+\.\d+)", push)
        if ip:
            info["tunnel_ip"] = ip.group(1)
        dns = re.findall(r"dhcp-option DNS ([\d.]+)", push)
        if dns:
            info["dns"] = dns[:3]
        c = re.search(r"cipher\s+(\S+)", push)
        if c:
            info["data_cipher"] = c.group(1)
    m = re.search(r"Control Channel: (TLSv[\d.]+), cipher (\S+(?: \S+)?)", text)
    if m:
        info["tls_version"] = m.group(1)
        info["tls_cipher"] = m.group(2).rstrip(",")
    m = re.search(r"VERIFY X509NAME OK: CN=(\S+)", text)
    if m:
        info["server_cn"] = m.group(1)
    m = re.search(r"Peer Connection Initiated with \[AF_INET6?\]([0-9a-fA-F:.]+):(\d+)", text)
    if m:
        info["peer"] = f"{m.group(1)}:{m.group(2)}"
    return info


def probe_one(cfg_path):
    name = os.path.basename(cfg_path)
    safe = re.sub(r"[^\w.-]", "_", name)
    tmp_cfg = os.path.join(TMP, safe)
    try:
        sanitize_config(cfg_path, tmp_cfg)
    except Exception as e:
        return {"file": name, "verdict": "ERROR", "error": f"sanitize: {e}"}
    t0 = time.time()
    cmd = [OPENVPN, "--config", tmp_cfg] + EXTRA
    lines = []
    done = threading.Event()
    # 任一终局信号出现即中断，避免等满超时（ALIVE 节点 ~2-4s 即可判定）
    stop_markers = ("PUSH_REPLY", "AUTH_FAILED", "Exiting due to fatal error",
                    "All connections have been connect-retry-max unsuccessful",
                    "TLS Error", "TLS key negotiation failed")
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, bufsize=1, errors="ignore",
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def reader():
        try:
            for line in p.stdout:
                lines.append(line)
                if any(mk in line for mk in stop_markers):
                    break
        except Exception:
            pass
        finally:
            done.set()

    th = threading.Thread(target=reader, daemon=True)
    th.start()
    done.wait(timeout=25)
    rc = p.poll()
    if rc is None:
        p.terminate()
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait(timeout=5)
        rc = p.returncode if p.returncode is not None else -1
    text = "".join(lines)
    elapsed = round(time.time() - t0, 2)
    verdict, err = classify(text, rc)
    row = {"file": name, "verdict": verdict, "error": err, "elapsed": elapsed}
    if verdict == "ALIVE":
        row.update(parse_alive(text))
    else:
        # 保留最后几行诊断
        tail = [l for l in text.strip().splitlines() if l.strip()][-4:]
        row["tail"] = [l[:160] for l in tail]
    try:
        os.remove(tmp_cfg)
    except OSError:
        pass
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=32)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default=os.path.join(OUT, "probe_results.json"))
    args = ap.parse_args()

    os.makedirs(TMP, exist_ok=True)
    cfgs = sorted(
        os.path.join(OVPN, f) for f in os.listdir(OVPN) if f.endswith(".ovpn")
    )
    if args.limit:
        cfgs = cfgs[: args.limit]
    print(f"probe {len(cfgs)} configs, workers={args.workers}", flush=True)
    COUNTER["total"] = len(cfgs)

    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for row in ex.map(probe_one, cfgs):
            with LOCK:
                results.append(row)
                COUNTER["done"] += 1
                if row["verdict"] == "ALIVE":
                    COUNTER["alive"] += 1
                d = COUNTER["done"]
                if d % 20 == 0 or d == COUNTER["total"]:
                    el = time.time() - T0
                    eta = el / d * (COUNTER["total"] - d)
                    print(f"  {d}/{COUNTER['total']} alive={COUNTER['alive']} "
                          f"{el:.0f}s eta={eta:.0f}s", flush=True)

    json.dump(results, open(args.out, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    alive = [r for r in results if r["verdict"] == "ALIVE"]
    print(f"\n=== done {len(results)} alive={len(alive)} "
          f"({len(alive)/max(1,len(results))*100:.1f}%) -> {args.out}", flush=True)
    auth = [r for r in results if r["verdict"] == "AUTHFAIL"]
    if auth:
        print(f"authfail={len(auth)} (节点活着但凭证无效)")
    shutil.rmtree(TMP, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
