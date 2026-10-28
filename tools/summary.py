"""全量汇总统计 -> output/summary.json（供 README/报告引用）。"""
import json
import os
import re
import sys
from collections import Counter

WORK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(WORK, "output")

DETAILS_HINT = (
    "缺少 output/details.json：该文件含节点 slug 与 ovpn 映射，属凭证类未入库。\n"
    "先在本机执行一次全量收割再跑本脚本：\n"
    "    python tools/harvest.py 2 3\n"
    "（需要境外出口访问 site45.newmobilejumpstech.com，见 docs/PROTOCOL.md §6.2）"
)



def main():
    det_path = os.path.join(OUT, "details.json")
    if not os.path.exists(det_path):
        print(DETAILS_HINT)
        return 2
    det = json.load(open(det_path, encoding="utf-8"))
    pr = json.load(open(os.path.join(OUT, "probe_results.json"), encoding="utf-8"))
    pmap = {p["file"]: p for p in pr}

    rows = []
    for d in det:
        if not d.get("config_len"):
            continue
        fname = os.path.basename(d.get("file") or "")
        p = pmap.get(fname) or {}
        rows.append({**d, "verdict": p.get("verdict"), "elapsed": p.get("elapsed")})

    alive = [r for r in rows if r["verdict"] == "ALIVE"]
    lat = sorted(r["elapsed"] for r in alive if r["elapsed"])

    def q(p):
        return round(lat[min(len(lat) - 1, int(len(lat) * p))], 2)

    w = Counter(r["country"] for r in alive)
    print(f"总配置 {len(rows)} | 存活 {len(alive)} ({len(alive)/len(rows)*100:.1f}%) "
          f"| 唯一IP {len({r['remote'] for r in rows})} "
          f"| 存活唯一IP {len({r['remote'] for r in alive})}")
    print(f"握手耗时 P50 {q(.5)}s P90 {q(.9)}s P95 {q(.95)}s "
          f"min {lat[0]:.2f}s max {lat[-1]:.2f}s")
    print(f"覆盖国家 {len({r['country'] for r in rows})} | 存活国家 {len(w)}")
    print("\n存活 TOP20 国家:")
    for c, n in w.most_common(20):
        print(f"  {n:4d}  {c}")
    print("\n失败原因:", dict(Counter(r["verdict"] for r in rows if r["verdict"] != "ALIVE")))
    print("\nTLS:", dict(Counter(p.get("tls_version") for p in pr if p["verdict"] == "ALIVE")))
    print("数据cipher:", dict(Counter(p.get("data_cipher") for p in pr if p["verdict"] == "ALIVE")))
    print("服务端 CN 唯一:", len({p.get("server_cn") for p in pr if p["verdict"] == "ALIVE"}))

    def yr(d):
        m = re.match(r"(\d{4})", d.get("updated_at") or "")
        return int(m.group(1)) if m else None

    byyr = {}
    for r in rows:
        y = yr(r)
        byyr.setdefault(y, [0, 0])
        byyr[y][0] += 1
        if r["verdict"] == "ALIVE":
            byyr[y][1] += 1
    print("\n按配置更新年份的存活率:")
    for y in sorted(k for k in byyr if k):
        t, a = byyr[y]
        print(f"  {y}: {a}/{t} = {a/t*100:.0f}%")

    # 一个国家有多个 IP 时，取延迟最优的节点作为"推荐代表"
    best = {}
    for r in alive:
        c = r["country"]
        if c not in best or (r["elapsed"] or 999) < (best[c]["elapsed"] or 999):
            best[c] = r
    print(f"\n每国家最快代表节点: {len(best)} 个国家")

    summ = {
        "total_configs": len(rows),
        "alive": len(alive),
        "alive_rate": round(len(alive) / len(rows) * 100, 1),
        "unique_ip": len({r["remote"] for r in rows}),
        "alive_unique_ip": len({r["remote"] for r in alive}),
        "latency_s": {"p50": q(.5), "p90": q(.9), "p95": q(.95),
                      "min": round(lat[0], 2), "max": round(lat[-1], 2)},
        "countries": len({r["country"] for r in rows}),
        "alive_countries": len(w),
        "top_countries_alive": w.most_common(30),
        "fail_reasons": dict(Counter(r["verdict"] for r in rows
                                     if r["verdict"] != "ALIVE")),
        "by_update_year": {str(y): {"total": byyr[y][0], "alive": byyr[y][1]}
                           for y in sorted(k for k in byyr if k)},
        "best_per_country": {c: {"id": r["id"], "name": r["name"], "remote": r["remote"],
                                 "elapsed": r["elapsed"]} for c, r in best.items()},
    }
    json.dump(summ, open(os.path.join(OUT, "summary.json"), "w", encoding="utf-8"),
              indent=1, ensure_ascii=False)
    print("-> output/summary.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
