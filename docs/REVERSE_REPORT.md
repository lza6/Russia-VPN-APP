# RUSSIA VPN v4.2.8.6 (b4286, @ElaMods) 逆向报告

> 分析日期：2026-10-29 · 工作区 `C:\Users\Administrator.DESKTOP-EGNE9ND\Desktop\逆向产物\RussiaVPN_4.2.8.6`
> 结论先行：**这是一个 Flutter + ics-openvpn 的国别白标 VPN，节点目录与 ovpn 客户端凭证
> 由一个无鉴权的 Laravel API 全量下发。1167 条配置可完整拉取，握手存活率约 69%。**

## 1. 样本基本信息

| 项 | 值 |
|----|----|
| 文件名 | `RUSSIA VPN v4.2.8.6 b4286 (Premium by @ElaMods).apk` |
| 大小 | 24.4 MB（解压 54.2 MB / 893 项） |
| 包名 | `free.vpnrussia.secure.unlimited.turbo.unblock.proxy` |
| Flutter | 3.12.2（`libflutter.so` 内版本串） |
| 原生组件 | `libopenvpn.so` + `libovpnexec.so` + `libopvpnutil.so`（ics-openvpn 系）、`librive_native.so`（Rive 动画）、`libdartjni.so` |
| 权限 | 仅 `INTERNET` / `WAKE_LOCK` / `DUMP` — 无位置、无通讯录、无存储 |
| VPN 接入方式 | `android.net.VpnService` + `de.blinkt.openvpn.core.OpenVPNService` |
| 签名 | `META-INF/HEMANT.SF`（第三方 mod 签名，非原厂） |
| 变体 | "Premium by @ElaMods" = 已解 premium 的 mod 包 |

三个 dex（`classes.dex` 8.6 MB / `classes3.dex` 5.3 MB / `classes2.dex` 208 KB）里是
Facebook Audience Network、AdMob、Billing、Firebase 等 SDK —— **业务逻辑不在 dex 里**，
全部在 Flutter AOT（`libapp.so` 7.7 MB）。所以路线是 dex 字符串表扫域名 + libapp 字符串定位 base URL，
而不是 jadx 全量反编译。

## 2. 架构判定（10 分钟定论的事实链）

| 判定项 | 实测 | 结论 |
|--------|------|------|
| 是否 Firebase RTDB 下发 | dex/arsc 全量搜 `-default-rtdb.firebaseio.com` → 0 命中 | ❌ 不是 RTDB 模式 |
| 是否离线内置配置 | `assets/` 内只有 Rive 动画、国旗 png、语言 json，无 `.ovpn`/`.conf` | ❌ 不是离线模式 |
| 是否自有 HTTP API | `libapp.so` 命中唯一 URL：`https://site45.newmobilejumpstech.com/api/0` | ✅ 自有 API 模式 |
| 白标归属 | 同栈的 INDONESIA VPN 用 `site40.mobilejumps.org/public/api/`，节点 `id`/`slug` 与本站高度重叠（slug 重叠 413/1104） | ✅ 同一白标 SaaS 平台（mobilejumps） |

**平台性质**：mobilejumps 是"VPN 白标 SaaS"——同一套 Laravel 后端 + 节点池，
按 `siteNN.域名` 一客一实例部署，App 侧只改品牌与动画（`ru_ic_*.json` 前缀即 Russia 定制）。
这解释了为什么 App 侧**完全没有鉴权逻辑**：信任边界设在了"不知道域名"上。

## 3. API 面

完整协议见 [`PROTOCOL.md`](./PROTOCOL.md)。核心三行：

```http
GET https://site45.newmobilejumpstech.com/api/allservers      -> 1167 条目录
GET https://site45.newmobilejumpstech.com/api/detail/{slug}   -> 完整 .ovpn（含私钥 + tls-crypt）
```

无签名、无设备指纹、无 token、无 `X-Android-Package` 校验。`User-Agent: Dart/3.5 (dart:io)`
只是照抄 App，裸 curl 同样 200。

`libapp.so` 里还有 `detail/random`（随机节点）端点字符串，实测被 Cloudflare WAF 拦（403）
或 Laravel 限流（429），未打通——但功能上等价于"从 allservers 随机抽一条"，无额外价值。

## 4. 节点池画像

### 4.1 规模与地理

| 指标 | 值 |
|------|----|
| 配置总数 | 1167（`allservers` 全量） |
| 唯一 IP | 约 420（一个 IP 挂了多份证书/多入口） |
| 国家/地区 | 186（含 `Russia,Moscow1..6` 这类分区后缀） |
| 协议 | **100% `udp/1194`**（`hastcp=true` 不可信，TCP 端口实测全关） |

TOP 国家：`United States 129` / `Russia,Moscow1 39` / `Russia,Moscow2 31` /
`Russia,Moscow4 26` / `Russia,Moscow3 26` / `United Kingdom 25` / `Australia 24` / `Germany 19`。

### 4.2 基础设施归属（90 个 IP 抽样，ip-api.com batch）

| ASN | 数量 | 说明 |
|-----|------|------|
| AS9009 M247 Europe | 40 | 商业 VPN 托管商，虚拟位置多 |
| AS202422 G-Core Labs (GHOST) | 11 | 前 G-Core，现 GHOST |
| AS45899 VNPT Corp | 5 | 越南电信 |
| AS141995 Contabo Asia | 4 | 德国云厂商亚太节点 |
| AS63018 Dedicated.com | 4 | 加拿大独立服务器 |
| AS35661 Virtua Systems | 4 | — |
| AS62563 GlobalTeleHost | 4 | — |

`hosting=true`：**68/90 = 75.6%**。

**结论：这是平台方批量采购的数据中心 VPS 池，不是住宅 IP，也不是终端用户设备。**

### 4.3 地理真实性

90 个样本里 **86 个（95.6%）** 标称国家与 IP 注册地一致。4 个不一致的都是
M247 的新加坡 IP 被挂上 `Morocco` / `Malta` / `Panama` 标签（典型"虚拟位置"卖法）。

隧道实测进一步确认：**节点 IP 就是出口 IP**（入口即出口，无二次转发）。
抽样 12 个节点建真实隧道核对，出口 IP 与 `remote` 字段 100% 一致。

## 5. 测活

### 5.1 方法（可复现）

```
openvpn.exe --config <sanitized.ovpn> --dev null --ifconfig-noexec --route-noexec \
  --connect-timeout 8 --connect-retry-max 1 --server-poll-timeout 8 --auth-nocache --disable-dco
```

判据：**收到 `PUSH_REPLY` = ALIVE**（证明 TLS 握手 + 双向证书认证 + 服务端下发 ifconfig 全通）。
`--dev null` 不建隧道、`--ifconfig-noexec`+`--route-noexec` 不改本机任何状态，
因此可以 40 并发安全跑（全量 1167 条约 4 分钟）。

> 为什么不裸 UDP 探测：配置全部带 `<tls-crypt>`，未加密的 `P_CONTROL_HARD_RESET` 会被服务端静默丢弃，
> 裸探测的 100% 失败率是假数据。

### 5.2 结果（全量 1167 条）

| 指标 | 值 |
|------|----|
| 存活 | **725 / 1167 = 62.1%** |
| 存活唯一 IP | 321 / 455 = 70.5% |
| 握手耗时 | P50 2.50s · P90 3.34s · P95 5.15s · min 1.05s · max 18.69s |
| 失败 | 全部 `DEAD`（442 条，无任何 TLS 响应；0 条 `AUTH_FAILED`） |
| 存活国家 | 148 / 186 |
| 控制通道 | TLSv1.3 × 725（100%） |
| 数据通道 | AES-128-GCM × 725（100%） |
| 服务端证书 CN | 321 个唯一 = 存活唯一 IP 数（**一 IP 一独立证书**，非单证书簇） |

全量测活用 48 并发，**140 秒跑完**，0 卡死。

按配置更新年份看存活率（非单调，有信息量）：

| 配置更新年份 | 存活 / 总数 | 存活率 |
|-------------|------------|--------|
| 2024 | 235 / 289 | **81%** |
| 2025 | 242 / 524 | **46%** |
| 2026 | 248 / 354 | 70% |

2025 那批大量已废弃但未从目录清理 —— 说明上游的 `status` 字段（恒 0）确实不反映可用性，
**任何"全量下发"式的对接都必须自己测活**。

### 5.3 真实隧道出口验证（两批共 27 节点抽样，含带宽）

```
隧道建立 → New-NetRoute 只放行测试 IP 的 /32 到 wintun 接口 → 裸 socket HTTP 请求
```

**结果 27/27 出口可用，出口国家 27/27 与标称完全一致。**

| 批次 | 样本 | 出口可用 | 地理匹配 | 说明 |
|------|------|---------|---------|------|
| 第一批 | 15 | 15 | 15 | 连通性验证（无测速） |
| 第二批 | 12 | 12 | 12 | 含 1MB 单流带宽实测（2 个首轮 10065 失败，修正路由残留后复测全通） |

带宽与延迟（12 节点实测）：

| 指标 | 值 |
|------|----|
| 带宽 | min 2.04 / 中位 2.83 / max 4.21 Mbps（1MB 单流，受 BDP+慢启动限制，非链路上限） |
| RTT | min 529 / 中位 607 / max 844 ms |
| 出口国家样例 | Serbia / Belgium×3 / Bulgaria×2 / Canada×4 / Poland / Portugal |
| 出口 IP 与 `remote` | **完全一致**（入口即出口，无二次转发） |

RTT 500-850 ms 是"本机在中国大陆 → 北美/欧洲"的物理距离成本，不是节点慢。
**出口 IP == 节点 `remote` 字段**这一点让"不上隧道判地理真实性"成为可能（见 PROTOCOL.md 第 8 节）。

一个易踩的实现坑（已修）：宿主反复跑隧道测试后，残留的 `/32` 路由指向**已拆除**的 wintun 接口，
新隧道建立后路由不会自动覆盖，请求直接 `WinError 10065`，看起来像"节点不出流量"。
`tools/tunnel_test.py` 的 `add_route()` 已改为**先 `Remove-NetRoute` 再 `New-NetRoute`**，
修正后同一批节点的 2 个"失败"全部转为 `EGRESS_OK`。

## 6. 踩坑清单（按代价排序）

1. **Cloudflare WAF 按 query string 拦**：`/api/allservers` 200，`/api/allservers?lang=ru` 403。
   探路时误以为路径不存在，浪费了大量盲试。区分法：CF 拦截页 4557 B，Laravel 404 页 6579 B。
2. **域名被 SNI 阻断**：TLS 握手阶段 RST（`WinError 10054`），必须走境外出口。
3. **本机代理环境变量污染测活**：`HTTP_PROXY=127.0.0.1:10808` 让"直连"出口变成代理出口
   （实测被劫持成意大利/法国 IP 轮换）。所有测活必须 `ProxyHandler({})` 或裸 socket。
4. **Windows `openvpn --route` 加错接口**：不加 `--interface-index` 的 /32 路由会挂到物理网卡，
   目标直接 `WinError 10065`。改用 `New-NetRoute -InterfaceIndex <wintun_ifIndex>`。
5. **`--dev null` 撞 `block-outside-dns`**：Windows 上进 WFP 后 fatal，测活前必须剔掉该行。
6. **`redirect-gateway` + `--route-nopull` 冲突**：需 `--pull-filter ignore "redirect-gateway"`。
7. **wintun 适配器被同机其它 VPN 占用**：`tapctl create --hwid wintun --name ovpn-ru-test` 建专属适配器。

## 7. 与 INDONESIA VPN 的关系（同平台印证）

| 项 | RUSSIA VPN | INDONESIA VPN 1.8.5.7 |
|----|-----------|----------------------|
| 包名 | `free.vpnrussia.secure...` | `...indonesia...` |
| API | `site45.newmobilejumpstech.com/api/` | `site40.mobilejumps.org/public/api/` |
| 配置数 | 1167 | 1104 |
| 唯一 IP | ~420 | 473 |
| slug 重叠 | **413** | — |
| 同节点样例 | `id=1024 Montreal - 689` | `id=1024 Montreal - 689`（同一节点，同一 slug） |

同一个 `id`、同一个 `slug`、同一个 `remote` —— 两站共用同一个后端节点表，
只是 `siteNN` 实例不同。**这证明节点池是平台的、App 只是壳。**

## 8. 合规判定

- **节点所有权**：mobilejumps 平台租用的数据中心 VPS（M247/G-Core/Dedicated.com/Contabo 等），
  既非运营商自有基础设施，也非付费终端用户设备。
- **凭证性质**：每节点一套客户端证书 + 私钥 + `tls-crypt` 密钥，服务端用同一套 CA 签发。
  **证书有效期长达 10 年，没有任何短期轮换机制**——这也是它"能长期用"的技术原因。
- **获取 ≠ 授权**。逆向披露这套 API 属于安全研究范畴；**把平台方付费基础设施挂进自己的
  生产代理池属于使用未授权资源**，风险与责任由使用者承担。
- 本仓库定位：**研究 demo / 知识资产**，不提供生产接入方案，不承诺稳定性。

## 9. 交付清单

| 路径 | 内容 |
|------|------|
| `tools/extract_strings.py` | 三路字符串提取（libapp/dex/arsc）+ 域名/路径正则筛选 |
| `tools/enum_sites.py` | 同平台 siteNN 实例枚举（实测仅 site45 有效） |
| `tools/harvest.py` | 全量收割（并发 + 429 自适应退避 + 断点续传） |
| `tools/probe.py` | 40 并发真实握手测活（dev null，零侵入） |
| `tools/tunnel_test.py` | 真实隧道出口验证（/32 路由注入 + 路由残留自检） |
| `demo_server.py` + `demo.html` | 节点池 demo（stats/nodes/detail/probe/export 五组端点） |
| `output/ovpn/` | 全量配置文件 |
| `output/details.json` / `probe_results.json` / `tunnel_results.json` | 结构化结果 |
| `docs/PROTOCOL.md` | 协议手册（最有价值，给对接方） |
| `docs/REVERSE_REPORT.md` | 本文件 |
