"""真实隧道出口验证（Windows 侧，单节点串行，零侵入路由）。

安全设计:
  - --route-nopull              : 服务端推送的路由/DNS 全部丢弃，默认路由绝不被劫持
  - --route <ip>/32 vpn_gateway : 只把 2 个测试目标 IP 的流量导进隧道
  - --pull-filter ignore dhcp-option : 不改本机 DNS
  - 退出时 openvpn 自动删除自己添加的 /32 路由；脚本再校验一次路由表残留
  - 串行执行：多个节点隧道 IP 可能同为 10.8.0.x，并发会互相污染

测量:
  - exit_ip      : 隧道内出口公网 IP（http://208.95.112.1 = ip-api.com 直连 IP）
  - exit_country : 出口国家，与标称 country 对比，验证节点地理真实性
  - rtt_ms       : 隧道内 HTTP 往返（含 TLS-less 纯 HTTP 请求）
  - down_kbps    : 抽样带宽（speed.cloudflare.com __down 1MB，可选 --speed）

用法:
  python tools/tunnel_test.py --limit 20 [--speed] [--country Russia]
输出:
  output/tunnel_results.json
"""
import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request

WORK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(WORK, "output")
OVPN = os.path.join(OUT, "ovpn")
OPENVPN = os.path.join(WORK, "bin", "openvpn_x", "OpenVPN", "bin", "openvpn.exe")
TMP = os.path.join(WORK, "tmp_tunnel")

IPAPI_IP = "208.95.112.1"        # ip-api.com 直连 IP（免 DNS）
# 测速目标：直连 IP + 手写 Host（免 DNS 污染）。多个候选，逐个试。
SPEED_TARGETS = [
    ("ipv4.download.thinkbroadband.com", "80.249.99.148", "/1MB.zip"),
]
PROBE_TARGETS = [IPAPI_IP]


def sanitize(src, dst):
    with open(src, encoding="utf-8", errors="ignore") as f:
        body = [ln for ln in f if "block-outside-dns" not in ln.lower()]
    with open(dst, "w", encoding="utf-8", newline="\n") as f:
        f.writelines(body)


def http_via(host_ip, host_header, path="/json/", timeout=12):
    """直连 IP + 手写 Host 头，避开本机 DNS 与任何系统代理。"""
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(f"http://{host_ip}{path}", headers={
        "Host": host_header, "User-Agent": "curl/8", "Accept": "*/*"})
    t0 = time.time()
    with op.open(req, timeout=timeout) as r:
        data = r.read()
    return data, round((time.time() - t0) * 1000)


def measure_speed(timeout=15):
    """下 1MB 估算带宽。逐个试候选目标，任一成功即返回。"""
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    last = "no target"
    for host, ip, path in SPEED_TARGETS:
        req = urllib.request.Request(f"http://{ip}{path}",
                                     headers={"Host": host, "User-Agent": "curl/8"})
        t0 = time.time()
        try:
            with op.open(req, timeout=timeout) as r:
                total = 0
                while True:
                    chunk = r.read(65536)
                    if not chunk:
                        break
                    total += len(chunk)
            if total < 50_000:
                last = f"{host}: short body {total}B"
                continue
            dt = max(0.001, time.time() - t0)
            return round(total * 8 / dt / 1000, 1), None
        except Exception as e:
            last = f"{host}: {type(e).__name__} {str(e)[:50]}"
            continue
    return None, last


def wintun_ifindex(name="ovpn-ru-test"):
    try:
        p = subprocess.run(["powershell", "-NoProfile", "-Command",
                            f"(Get-NetAdapter -Name '{name}' -ErrorAction SilentlyContinue).ifIndex"],
                           capture_output=True, text=True, timeout=20)
        v = p.stdout.strip()
        return int(v) if v.isdigit() else None
    except Exception:
        return None


def add_route(ip, ifindex):
    """先删后加：宿主多次运行后可能残留指向已拆除接口的僵尸 /32 路由（会致 WinError 10065）。"""
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    f"Remove-NetRoute -DestinationPrefix '{ip}/32' -Confirm:$false "
                    f"-ErrorAction SilentlyContinue | Out-Null; "
                    f"New-NetRoute -DestinationPrefix '{ip}/32' -InterfaceIndex {ifindex} "
                    f"-NextHop '0.0.0.0' -ErrorAction SilentlyContinue | Out-Null"],
                   capture_output=True, timeout=25)


def tun_routes(ifindex=None):
    """列出指向 wintun 接口的测试 /32 路由，用于残留校验。

    注意：我们加的 /32 路由 NextHop 就是 0.0.0.0（点对点），
    所以**不能**用 NextHop 过滤，只能按 InterfaceIndex 匹配。
    """
    if ifindex is None:
        ifindex = wintun_ifindex()
    if not ifindex:
        return []
    wanted = {f"{t[1]}/32" for t in SPEED_TARGETS} | {f"{IPAPI_IP}/32"}
    try:
        p = subprocess.run(["powershell", "-NoProfile", "-Command",
                            "Get-NetRoute -ErrorAction SilentlyContinue | "
                            "Where-Object {$_.DestinationPrefix -like '*/32'} | "
                            "ForEach-Object { \"$($_.DestinationPrefix)|$($_.InterfaceIndex)\" }"],
                           capture_output=True, text=True, timeout=20)
        out = []
        for line in p.stdout.splitlines():
            if "|" not in line:
                continue
            prefix, idx = line.split("|", 1)
            if prefix.strip() in wanted and idx.strip() == str(ifindex):
                out.append(prefix.strip())
        return out
    except Exception:
        return ["<route-query-failed>"]


def test_node(cfg_path, with_speed=False):
    name = os.path.basename(cfg_path)
    os.makedirs(TMP, exist_ok=True)
    dst = os.path.join(TMP, name)
    sanitize(cfg_path, dst)

    cmd = [OPENVPN, "--config", dst, "--dev", "tun", "--windows-driver", "wintun",
           "--route-nopull",
           "--pull-filter", "ignore", "dhcp-option",
           "--pull-filter", "ignore", "redirect-gateway",
           "--dev-node", "ovpn-ru-test",
           "--route-delay", "2", "--connect-timeout", "10",
           "--connect-retry-max", "1", "--server-poll-timeout", "10",
           "--ping", "10", "--ping-exit", "60", "--auth-nocache",
           "--verb", "3", "--log", os.path.join(TMP, "tun.log")]

    log_path = os.path.join(TMP, "tun.log")
    try:
        os.remove(log_path)
    except OSError:
        pass

    t0 = time.time()
    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    state = {"verdict": "INIT", "error": None}
    tun_ip = None
    tun_ifindex = wintun_ifindex()
    deadline = time.time() + 35
    try:
        while time.time() < deadline:
            if p.poll() is not None:
                state["verdict"] = "FATAL"
                break
            try:
                txt = open(log_path, encoding="utf-8", errors="ignore").read()
            except OSError:
                txt = ""
            m = re.search(r"Ifconfig\s+\S+?\s+(\d+\.\d+\.\d+\.\d+)", txt)
            if not m:
                m = re.search(r"ifconfig\s+(\d+\.\d+\.\d+\.\d+)", txt)
            if m:
                tun_ip = m.group(1)
            if "Initialization Sequence Completed" in txt:
                state["verdict"] = "TUNNEL_UP"
                break
            if "Exiting due to fatal error" in txt or "Cannot open TUN" in txt:
                state["verdict"] = "FATAL"
                state["error"] = "tun_open_failed"
                break
            time.sleep(0.4)

        row = {"file": name, "verdict": state["verdict"], "tun_ip": tun_ip,
               "setup_s": round(time.time() - t0, 2)}
        if state["verdict"] == "TUNNEL_UP":
            # 路由必须按 wintun 接口索引注入；openvpn 的 --route 在 Windows 上会挂到物理网卡
            idx = wintun_ifindex()
            row["wintun_ifindex"] = idx
            if not idx:
                row["verdict"] = "NO_IFINDEX"
                row["error"] = "wintun adapter not found"
                return row
            add_route(IPAPI_IP, idx)
            time.sleep(1.0)
            try:
                data, rtt = http_via(IPAPI_IP, "ip-api.com")
                info = json.loads(data.decode("utf-8", "ignore"))
                row["exit_ip"] = info.get("query")
                row["exit_country"] = info.get("country")
                row["exit_isp"] = info.get("isp")
                row["rtt_ms"] = rtt
                row["verdict"] = "EGRESS_OK"
            except Exception as e:
                row["verdict"] = "TUNNEL_NO_EGRESS"
                row["error"] = f"{type(e).__name__}: {str(e)[:90]}"
            if with_speed and row["verdict"] == "EGRESS_OK":
                for _, sip, _ in SPEED_TARGETS:
                    add_route(sip, idx)
                time.sleep(0.8)
                kbps, err = measure_speed()
                row["down_kbps"] = kbps
                if err:
                    row["speed_error"] = err
        elif state["error"]:
            row["error"] = state["error"]
            try:
                tail = [l for l in open(log_path, encoding="utf-8", errors="ignore")
                        .read().strip().splitlines() if l.strip()][-3:]
                row["tail"] = [l[:140] for l in tail]
            except OSError:
                pass
        return row
    finally:
        p.terminate()
        try:
            p.wait(timeout=8)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait(timeout=5)
        time.sleep(1.5)
        row_routes = tun_routes(tun_ifindex)
        if row_routes:
            print(f"  [WARN] 路由残留 {row_routes}，清理中", flush=True)
            for d in row_routes:
                subprocess.run(["powershell", "-NoProfile", "-Command",
                                f"Remove-NetRoute -DestinationPrefix '{d}' -Confirm:$false "
                                f"-ErrorAction SilentlyContinue"], capture_output=True)
        try:
            os.remove(dst)
        except OSError:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--speed", action="store_true")
    ap.add_argument("--country", default="")
    ap.add_argument("--probe-file", default=os.path.join(OUT, "probe_results.json"))
    args = ap.parse_args()

    alive = []
    if os.path.exists(args.probe_file):
        for r in json.load(open(args.probe_file, encoding="utf-8")):
            if r.get("verdict") == "ALIVE":
                alive.append(os.path.join(OVPN, r["file"]))
    else:
        alive = [os.path.join(OVPN, f) for f in sorted(os.listdir(OVPN)) if f.endswith(".ovpn")]

    details = {}
    dp = os.path.join(OUT, "details.json")
    if os.path.exists(dp):
        for d in json.load(open(dp, encoding="utf-8")):
            if d.get("file"):
                details[os.path.basename(d["file"])] = d
    if args.country:
        c = args.country.lower()
        alive = [p for p in alive
                 if c in (details.get(os.path.basename(p), {}).get("country") or "").lower()]
    alive = alive[: args.limit]
    print(f"tunnel-test {len(alive)} alive nodes, speed={args.speed}", flush=True)

    results = []
    for i, cfg in enumerate(alive, 1):
        row = test_node(cfg, args.speed)
        d = details.get(os.path.basename(cfg), {})
        row["country"] = d.get("country")
        row["remote"] = d.get("remote")
        if row.get("exit_country") and d.get("country"):
            claimed = d["country"].lower().split(",")[0]
            real = (row["exit_country"] or "").lower()
            row["geo_match"] = bool(claimed and real and (claimed in real or real in claimed))
        results.append(row)
        print(f"  {i}/{len(alive)} {row['verdict']:18s} {row.get('exit_country') or '-':14s}"
              f" ip={row.get('exit_ip') or '-':16s} rtt={row.get('rtt_ms') or '-'}ms "
              f"{('kbps=' + str(row.get('down_kbps'))) if row.get('down_kbps') else ''}",
              flush=True)
        json.dump(results, open(os.path.join(OUT, "tunnel_results.json"), "w",
                                encoding="utf-8"), indent=1, ensure_ascii=False)

    ok = sum(1 for r in results if r["verdict"] == "EGRESS_OK")
    geo = sum(1 for r in results if r.get("geo_match"))
    print(f"\n=== {ok}/{len(results)} 出口可用, 地理匹配 {geo}/{ok}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
