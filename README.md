# RUSSIA VPN APK 逆向 · 节点池分析（研究 demo）

> `RUSSIA VPN v4.2.8.6 b4286 (Premium by @ElaMods)` 的完整逆向、节点收割、测活与本地 demo。
> **定位：安全研究 / 知识资产。不含生产接入方案，不承诺稳定性。**

## 状态表

| 阶段 | 状态 | 证据 |
|------|------|------|
| 架构判定 | ✅ 完成 | Flutter 3.12.2 + ics-openvpn；白标 SaaS（mobilejumps 平台，`site45.newmobilejumpstech.com`） |
| API 打通 | ✅ 完成 | `GET /api/allservers` 200（1167 条）；`GET /api/detail/{slug}` 200（完整 ovpn） |
| 全量收割 | ✅ 完成 | 1167/1167 条配置，`output/ovpn/`，0 失败 |
| 握手测活 | ✅ 完成 | 全量 1167 条真实 OpenVPN TLS 握手，存活 725（62.1%），`output/probe_results.json` |
| 隧道出口验证 | ✅ 完成 | 27 节点抽样（15 连通性 + 12 带宽），27/27 出口可用，出口国家 27/27 匹配标称 |
| 本地 demo | ✅ 运行中 | `http://127.0.0.1:8912`（stats/nodes/detail/probe/export） |
| 生产池接入 | ⛔ 不做 | 研究 demo 定位；节点为平台方租用的数据中心，凭证获取≠授权使用 |

## 一句话协议

```
目录: GET https://site45.newmobilejumpstech.com/api/allservers         -> 1167 条（id/name/country/slug）
配置: GET https://site45.newmobilejumpstech.com/api/detail/{slug}      -> data.config = 完整 .ovpn
```

无鉴权、无签名、无设备绑定。协议细节见 **[docs/PROTOCOL.md](docs/PROTOCOL.md)**（最有价值的一份）。

## 想接进 new-api？

这些节点是 OpenVPN（`udp/1194` + 证书 + `tls-crypt`），不是 SOCKS5，**不能直接喂给代理池**。
完整对接路径（转换层 → `TRYINGOPEN_PROXY_FILE` → 验证三层 → 8 个必避坑）见
**[docs/NEWAPI_INTEGRATION.md](docs/NEWAPI_INTEGRATION.md)**。

一句话：`tools/socks_layer.py`（每节点一个 netns：openvpn + microsocks + fail-closed）
→ 产出 `output/newapi_proxies.txt`（`socks5://127.0.0.1:4400x`）→ new-api 一行环境变量接入，
**不需要改 new-api 业务代码**。

## 关键实测数据
| 指标 | 值 |
|------|----|
| 配置总数 | **1167**（0 失败）；唯一 IP **455**（一 IP 多证书入口） |
| 国家/地区 | 186（存活覆盖 **148**） |
| 协议 | 100% `udp/1194` OpenVPN（`hastcp=true` 不可信，TCP 端口探测全 close） |
| 加密 | 控制通道 TLSv1.3（100%）/ 数据通道 AES-128-GCM（100%） |
| 认证 | 客户端证书 + `<tls-crypt>`（无账号密码，`username`/`password` 恒 null） |
| 证书有效期 | CA 10 年，**无短期轮换** |
| **握手存活率** | **725 / 1167 = 62.1%**（全量真实握手，140 秒跑完） |
| 存活唯一 IP | 321 / 455 = 70.5% |
| 握手耗时 | P50 **2.50s** · P90 3.34s · P95 5.15s · min 1.05s · max 18.69s |
| 隧道出口可用率 | **27/27 = 100%**（两批抽样：15 节点连通性 + 12 节点带宽） |
| 出口地理真实性 | 27/27 一致（隧道核对）；95.6% 一致（ASN 核对，90 样本） |
| 隧道带宽 | 2.0 — 4.3 Mbps（1MB 单流，中位 2.83 Mbps） |
| 隧道 RTT | 529 — 844 ms（中位 607 ms，本机大陆出口） |
| 基础设施 | M247 40 / G-Core 11 / Contabo / Dedicated.com…，`hosting=true` 占 75.6% |

存活 TOP 国家：`Russia,Moscow1 33` / `Russia,Moscow3 26` / `United States 25` /
`Russia,Moscow4 24` / `Russia,Moscow2 24` / `France 14` / `Russia,Moscow5 13` /
`Russia,Moscow6 13` / `Canada 10` / `Japan 10` / `Israel - Tel Aviv 10`。

> 有意思的分布：配置更新年份与存活率非单调 —— 2024 年更新的 81% 存活，2025 年更新的仅 46%，
> 2026 年更新 70%。说明上游对老节点做了维护，2025 那批有大量已废弃未清理的残留。
> 完整数据见 `output/summary.json`。


## 快速开始

> ⚠️ **本仓库不含凭证类文件**：`output/ovpn/`（含 client 私钥 + tls-crypt）和
> `output/details.json`（slug ↔ 节点映射）已 gitignore。clone 下来只能直接读**结果数据**
> （`alive_nodes.txt` / `probe_results.json` / `summary.json` / `node_index.md`）
> 与跑分析；**要复现全链路必须先执行第 1 步收割**。
> 缺文件时脚本会直接提示你要先跑哪一步（退出码 2）。

```bash
# 0. 依赖：Python 3.10+；若要做隧道验证再备 OpenVPN 2.6（bin/ 已 gitignore，见下方说明）
python -c "print('ok')"

# 1. 全量收割（需境外出口，脚本内已写 socks5h://127.0.0.1:10808）
python tools/harvest.py 2 3            # rate=2 req/s, workers=3

# 2. 全量测活（48 并发真实握手，dev null 零侵入，约 2.5 分钟）
python tools/probe.py --workers 48

# 3. 隧道出口抽样验证（真实建隧道，只放行测试 IP 的 /32 路由）
python tools/tunnel_test.py --limit 12 --speed

# 4. 起 demo
python demo_server.py 8912             # http://127.0.0.1:8912
```

`bin/openvpn_x/`（OpenVPN 2.6 便携版 + wintun.dll）因体积已 gitignore，需要时自行提取：

```powershell
curl.exe -L -o openvpn.msi https://swupdate.openvpn.org/community/releases/OpenVPN-2.6.12-I001-amd64.msi
msiexec /a openvpn.msi /qn TARGETDIR=D:\tmp\openvpn_x
# 再把 D:\tmp\openvpn_x\OpenVPN 移到本项目 bin\ 下；wintun.dll 从 https://www.wintun.net/builds/wintun-0.14.1.zip 取 amd64 版放进 bin\openvpn_x\OpenVPN\bin\
```

首次跑隧道验证前需建专属 wintun 适配器（避免与同机其它 VPN 客户端抢）：

```powershell
.\bin\openvpn_x\OpenVPN\bin\tapctl.exe create --hwid wintun --name ovpn-ru-test
```

用完可删掉保持本机干净（本项目交付时已删除，本机无残留路由与适配器）：

```powershell
.\bin\openvpn_x\OpenVPN\bin\tapctl.exe delete ovpn-ru-test
```

## Demo 端点

| 端点 | 说明 |
|------|------|
| `GET /` | demo 页面（统计卡 / 国家分布 / 节点表 / 详情抽屉 / 一键测活） |
| `GET /api/stats` | 汇总：存活率、延迟分位、国家 TOP20、协议分布 |
| `GET /api/nodes?country=&alive=&q=&page=` | 分页节点列表 |
| `GET /api/node/<id>` | 单节点详情 + 配置预览（凭证块折叠） |
| `GET /api/node/<id>/config` | 下载该节点 `.ovpn` |
| `POST /api/probe` | 现场测活 `{"id":1024}` / `{"country":"russia"}`（≤20 个） |
| `GET /api/alive?format=lines` | 存活节点清单（tab 分隔，给下游消费） |
| `GET /healthz` | 健康检查 |

## 目录结构

```
RussiaVPN_4.2.8.6/
├── LICENSE                           MIT
├── demo_server.py / demo.html        本地 demo（只读 + 现场测活）
├── tools/
│   ├── extract_strings.py            三路字符串提取 + 域名/路径筛选
│   ├── enum_sites.py                 同平台 siteNN 实例枚举
│   ├── harvest.py                    全量收割（并发 + 429 退避 + 断点续传）
│   ├── probe.py                      48 并发真实握手测活
│   ├── tunnel_test.py                真实隧道出口验证 + 带宽（/32 路由注入 + 残留自检）
│   ├── socks_layer.py                ★ OpenVPN → SOCKS5 转换层（netns 隔离 + fail-closed）
│   ├── retry_failed.py               失败节点复测（验证路由残留修复）
│   ├── summary.py                    全量统计汇总
│   ├── node_index.py                 生成可直读节点索引
│   └── verify_demo.py                Playwright 真实浏览器验证 demo 页
├── docs/
│   ├── PROTOCOL.md                   ★ 上游协议手册（端点/字段/9 个坑/30 行最小实现）
│   ├── NEWAPI_INTEGRATION.md         ★ new-api 对接指南（转换层 → TRYINGOPEN_PROXY_FILE）
│   ├── REVERSE_REPORT.md             完整逆向报告
│   └── STABILITY_ASSESSMENT.md       稳定性评估（能不能"长久稳定"用）
├── recon/                            侦察产物（allservers.json / ASN 抽样 / 字符串表）
├── output/
│   ├── ovpn/                         全量 .ovpn（含私钥与 tls-crypt，凭证类，勿公开）
│   ├── details.json                  收割结果
│   ├── probe_results.json            握手测活结果（1167 条）
│   ├── tunnel_results.json           隧道出口 + 带宽验证结果
│   ├── summary.json                  全量统计
│   ├── alive_nodes.txt               ★ 存活节点清单（tab 分隔，725 行，可直接喂调度器）
│   ├── node_index.md                 按国家分组的可读索引（148 组）
│   └── shots/                        demo 页面截图（Playwright 实拍）
└── bin/openvpn_x/                    OpenVPN 2.6.12 便携版（MSI 提取，含 wintun.dll）
```

> **关于订阅链接**：这些节点是 OpenVPN（`udp/1194` + 客户端证书 + `tls-crypt`），
> Clash / sing-box / URI 订阅格式**不支持**直接承载 `.ovpn`，所以本项目不产出订阅链接，
> 交付的是 `alive_nodes.txt`（机器可读）+ `node_index.md`（人读）+ `output/ovpn/*.ovpn`（配置本体）。
> 要用必须自己起隧道，再在隧道内做 SOCKS5 出口。

## 验证边界（如实披露）

| 验证项 | 状态 | 说明 |
|--------|------|------|
| 握手测活 | ✅ 已验证 | 全量 1167 条，真实 OpenVPN TLS 握手 |
| 隧道出口/带宽 | ✅ 已验证 | 27 节点抽样，本机 wintun 真实隧道，`/32` 路由隔离 + 每次清理自检 |
| demo 页 | ✅ 已验证 | Playwright + Chromium 真实浏览器，5 条路径 + 0 console error |
| **模拟器内实测** | ⛔ 未做 | 该 APK 仅含 `arm64-v8a`，模拟器为 x86_64（SDK 30）；Flutter AOT 在 ndk_translation 下 SIGILL 自毁（同类 Flutter VPN 已实证此死路）。Android 侧真机验证需 arm64 设备 |
| 上游长期稳定性 | ⏳ 待观察 | 单轮快照已测（62.1% 存活）；"是否长久稳定"需 6-12 小时一轮的多轮观测才能定论 |

## 必读的三个坑

1. **Cloudflare WAF 按 query string 拦**：`/api/allservers` 200，`/api/allservers?lang=ru` **403**。
   探路时极易误判成"路径不存在"。（区分法：CF 页 4557 B，Laravel 404 页 6579 B）
2. **`HTTP_PROXY` 环境变量污染测活**：本机系统代理开着时，"直连"测出的出口其实是代理出口。
   本项目所有测活工具强制 `ProxyHandler({})` / 裸 socket。
3. **Windows `openvpn --route` 挂错接口**：必须 `New-NetRoute -InterfaceIndex <wintun_ifIndex>`，
   否则目标直接 `WinError 10065`。`tunnel_test.py` 每次跑完会自检路由残留并清理。

## 与 INDONESIA VPN 的关系

同平台不同白标站（`site40.mobilejumps.org` vs `site45.newmobilejumpstech.com`），
`slug` 重叠 **413/1104**，`id=1024 Montreal - 689` 在两站是同一节点同一凭证。
**App 只是壳，节点池是平台的。**

## 合规声明

- 本项目仅用于**安全研究**与协议分析。所有节点为 mobilejumps 白标平台租用的**商业数据中心**
  （M247 / G-Core / Contabo / Dedicated.com 等），既非运营商自有基础设施，也非付费终端用户设备。
- **凭证获取 ≠ 授权使用**。将这些节点挂入生产代理池属于使用未授权资源，风险与责任由使用者承担。
- 仓库不提供生产接入方案，不对节点可用性/稳定性作任何承诺；`output/ovpn/` 含私钥与 `tls-crypt`
  密钥，**不得公开分发**。
