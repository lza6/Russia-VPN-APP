"""生成可直读的节点索引（output/node_index.md + output/alive_nodes.txt）。

OpenVPN 协议无法直接转成 Clash/sing-box 订阅（它们不吃 .ovpn），
因此这里给的是"可直读清单 + 文件映射"，而不是订阅链接。
"""
import json
import os
import sys

WORK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(WORK, "output")


def main():
    det = json.load(open(os.path.join(OUT, "details.json"), encoding="utf-8"))
    pr = json.load(open(os.path.join(OUT, "probe_results.json"), encoding="utf-8"))
    pmap = {p["file"]: p for p in pr}

    rows = []
    for d in det:
        if not d.get("config_len"):
            continue
        fname = os.path.basename(d.get("file") or "")
        p = pmap.get(fname) or {}
        rows.append({**d, "verdict": p.get("verdict"), "elapsed": p.get("elapsed"),
                     "tunnel_ip": p.get("tunnel_ip"), "fname": fname})

    alive = [r for r in rows if r["verdict"] == "ALIVE"]
    alive.sort(key=lambda r: (r["country"] or "", r["elapsed"] or 999))

    # 1) 纯文本清单：直接可导入任何自建调度器
    lines = [f"{r['remote']}:{r['port']}\t{r['proto']}\t{r['country']}\t{r['elapsed']}s\t{r['fname']}"
             for r in alive]
    open(os.path.join(OUT, "alive_nodes.txt"), "w", encoding="utf-8").write("\n".join(lines) + "\n")

    # 2) 人读索引：按国家分组
    by_country = {}
    for r in alive:
        by_country.setdefault(r["country"], []).append(r)

    md = ["# 存活节点索引（%d / %d 条配置）" % (len(alive), len(rows)), "",
          "> 按国家分组，每组按握手耗时升序。`ovpn 文件` 列相对于 `output/ovpn/`。",
          "> 机器可读清单：`output/alive_nodes.txt`（tab 分隔）· 完整 JSON：`output/probe_results.json`", ""]
    for country in sorted(by_country, key=lambda c: (-len(by_country[c]), c)):
        items = by_country[country]
        md.append(f"## {country}（{len(items)}）")
        md.append("")
        md.append("| ID | 名称 | 远端 | 握手 | ovpn 文件 |")
        md.append("|----|------|------|------|-----------|")
        for r in items[:12]:
            md.append(f"| {r['id']} | {r['name']} | `{r['remote']}:{r['port']}` | "
                      f"{r['elapsed']}s | `{r['fname']}` |")
        if len(items) > 12:
            md.append(f"| … | 其余 {len(items)-12} 个见 alive_nodes.txt | | | |")
        md.append("")
    open(os.path.join(OUT, "node_index.md"), "w", encoding="utf-8").write("\n".join(md))

    print(f"存活 {len(alive)} -> output/alive_nodes.txt ({len(lines)} 行), "
          f"output/node_index.md ({len(by_country)} 个国家分组)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
