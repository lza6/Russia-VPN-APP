# RUSSIA VPN v4.2.8.6 — 上游协议手册

> 给二次开发者/对接方的实现手册。所有内容均在 2026-10-29 实测验证。
> 目标读者：需要自己复现节点拉取、或把这套上游当成"节点目录 API"用的人。

## 1. 一句话结论

这是一个 **Flutter + OpenVPN(ics-openvpn)** 的国别白标 VPN，节点目录与配置全部来自
一个**无鉴权的 Laravel JSON API**（mobilejumps 白标 SaaS 平台）：
拿到 slug 就能拿到完整 `.ovpn`（含 client 证书 + 私钥 + `tls-crypt` 密钥）。
**App 本身不参与签发**——也就是说，这套 API 只要知道域名/路径，任何 HTTP 客户端都能全量拉取。

## 2. 端点表

| 方法 | 路径 | 返回 | 说明 |
|------|------|------|------|
| GET | `https://site45.newmobilejumpstech.com/api/allservers` | 目录 JSON（实测 1167 条 / 378 KB） | 全量节点索引，无鉴权 |
| GET | `https://site45.newmobilejumpstech.com/api/detail/{slug}` | 单节点 JSON（含完整 `.ovpn` 文本） | `slug` 为 72 字符随机串，来自 `allservers` |
| GET | `https://site45.newmobilejumpstech.com/public/api/allservers` | 同上 | 双前缀并存（同栈的 INDONESIA VPN 站只用 `/public/api/`） |
| GET | `https://site45.newmobilejumpstech.com/` | `endpoint` 三字纯文本 | 存活探针，8 字节 |
| GET | `/api/detail/random` | 403 / 429 | APK 内存在此字符串，实测被 CF WAF 拦截或用 429 限流，**未打通** |

**APK 里的 base URL**：`https://site45.newmobilejumpstech.com/api/0`
（`lib/arm64-v8a/libapp.so` 内唯一命中，Dart AOT 常量）。
`/api/0` 中的 `0` 实测可省略——`/api/allservers` 直接可用，路径路由对前缀宽容。

## 3. 请求头

```http
User-Agent: Dart/3.5 (dart:io)
Accept: */*
```

服务端**不校验** App 签名、设备指纹、`X-Android-Package`、token——上面的头只是照抄 App，
实测裸 `curl` 也能 200。唯一要求：**必须经非受限网络出口访问**（见第 6 节 WAF/网络）。

## 4. 响应结构

### 4.1 `allservers`

```json
{
  "success": true,
  "message": "All Servers loaded",
  "data": [
    {
      "id": 1024,
      "name": "Montreal - 689",
      "flag": "https://mobilejumptech.com/flag/ca.png",
      "country": "Canada",
      "slug": "LbusKsoumt2YhwF2c1FmEt4uQfIdEVZvwXlPvkgGJeMID8HIYBqx64pyQArBLZwi",
      "priority": 4,
      "status": 0,
      "created_at": "2021-05-09T12:44:44.000000Z",
      "updated_at": "2025-12-07T12:27:39.000000Z",
      "hastcp": true,
      "hasudp": true
    }
  ]
}
```

| 字段 | 说明 | 实测取值 |
|------|------|---------|
| `id` | 节点主键，站内唯一 | 1…1万+ |
| `slug` | **唯一有用的凭证**：72 字符随机串，取 detail 的钥匙 | `[A-Za-z0-9]{72}` |
| `country` | 标称国家，可能带分区后缀 | `Russia,Moscow1` / `US - Seattle` |
| `priority` | 客户端排序权重 | 0-5 |
| `status` | 实测**全站恒为 0**，不表示可用性（死节点也 0） | 0 |
| `hastcp`/`hasudp` | 声称支持 TCP/UDP | 实测 100% 节点配置均为 `udp/1194`，`hastcp` 不可信 |
| `updated_at` | 配置更新时间 | 2021—2026 混杂，与存活无关 |

### 4.2 `detail/{slug}`

```json
{
  "success": true,
  "message": "Success load VPN Detail",
  "data": {
    "id": 1024, "name": "Montreal - 689", "country": "Canada",
    "username": null, "password": null,
    "config": "client\nproto udp\nremote 167.160.94.156 1194\n…<ca>…</ca><cert>…</cert><key>…</key><tls-crypt>…</tls-crypt>"
  }
}
```

`config` 是**可直接落盘的完整 OpenVPN 客户端配置**，内联四块：

| 块 | 内容 | 说明 |
|----|------|------|
| `<ca>` | EC P-256 CA 证书 | 每节点独立 CA（`CN=cn_<随机串>`） |
| `<cert>` | 客户端证书 | 每节点独立证书 |
| `<key>` | 客户端私钥 | 明文内联，**这就是"凭证"本身** |
| `<tls-crypt>` | tls-crypt 密钥 | 控制通道加密，缺它无法握手 |

`username`/`password` 恒为 `null`（实测全部节点走**证书认证**，非账号密码）。
证书有效期：CA 10 年（如 2025-12 → 2035-12），**无短期轮换机制**。

关键配置行示例：

```
client
proto udp
remote 167.160.94.156 1194
dev tun
remote-cert-tls server
verify-x509-name server_0bV32ja5FZtqZxmn name
auth SHA256
cipher AES-128-GCM
tls-client
tls-version-min 1.2
tls-cipher TLS-ECDHE-ECDSA-WITH-AES-128-GCM-SHA256
```

## 5. 服务端下发的 PUSH_REPLY（隧道参数）

握手成功后服务端推：

```
PUSH_REPLY,dhcp-option DNS 1.0.0.1,dhcp-option DNS 1.1.1.1,
redirect-gateway def1 bypass-dhcp,route-gateway 10.8.0.1,
topology subnet,ping 10,ping-restart 120,
ifconfig 10.8.0.181 255.255.255.0,peer-id 159,cipher AES-128-GCM
```

- 隧道网段 `10.8.0.0/24`，网关 `10.8.0.1`，客户端分到 `10.8.0.x`
- DNS 硬编码为 Cloudflare（`1.0.0.1` / `1.1.1.1`）
- 数据通道 `AES-128-GCM`，控制通道 `TLS 1.3`（实测 100% 存活节点均为 TLSv1.3）
- 服务端证书 CN 逐节点不同（抽样 178 个存活节点 → 160 个不同 CN）

## 6. 必避的坑（按踩坑代价排序）

### 6.1 Cloudflare WAF：**任何带 query string 的请求一律 403**

```
GET /api/allservers          -> 200 (378 KB)
GET /api/allservers?lang=ru  -> 403 (4557 B Cloudflare 挑战页)
```

这条最坑：`/api/settings`、`/api/vip`、`/api/premium` 等探路请求全部 403，
**看不出是"路径不存在"还是"参数违规"**。判断法：Laravel 的原生 404 是 6579 B 带
`<title>Not Found</title>`，Cloudflare 的是 4557 B 带 `class="no-js ie6 oldie"`。

### 6.2 域名被 SNI 阻断（中国大陆出口）

`site45.newmobilejumpstech.com` 解析到 Cloudflare（`104.21.12.91` / `172.67.194.4`），
TCP 443 能建连但 TLS 握手阶段被 RST（`WinError 10054`）。
**必须走境外出口**（本机 `socks5h://127.0.0.1:10808` 或境外 VPS）。
注意 `allservers` 与 `detail` 走同一个域名，不存在"直连无 SNI 绕过"的路。

### 6.3 429 限流器是全局的

连续拉 detail 会触发 Laravel `throttle`（实测约 2 req/s 安全，>4 req/s 开始偶发 429）。
必须实现：遇 429 → 退避 5s（×1.6 递增）→ 成功后 `interval × 0.95` 缓慢提速。
1167 条在 `interval≈0.5s` 下约 15-20 分钟拉完，0 错误。

### 6.4 本机代理环境变量会毁掉所有测活数据

Windows 侧 `HTTP_PROXY=http://127.0.0.1:10808`（系统级 WinINET 也开了）。
`urllib`/`curl` 默认继承 → 测出来的"节点出口"其实是代理出口（本机实测被劫持成
意大利 `80.174.118.92` / 法国 `198.16.113.218` 轮换）。
**铁律**：所有测活/出口验证用 `ProxyHandler({})` 或裸 socket，绝不读环境变量。

### 6.5 `--dev null` 测活要屏蔽 `block-outside-dns`

配置里带 `setenv opt block-outside-dns`，Windows 上会进 WFP 加过滤层，
接口索引解析失败即 `fatal`。测活前用脚本剔掉该行即可（不影响真实客户端）。

### 6.6 Windows 上 `openvpn --route` 会把 /32 路由挂到物理网卡

真实隧道出口验证时，`--route <ip> 255.255.255.255 vpn_gateway` 经 `route.exe ADD`
加的路由**不带接口索引**，Windows 选默认接口 → 目标直接 `WinError 10065`（网络不可达）。
正确做法：`New-NetRoute -DestinationPrefix <ip>/32 -InterfaceIndex <wintun_ifIndex> -NextHop 0.0.0.0`，
接口索引从 `(Get-NetAdapter -Name 'ovpn-ru-test').ifIndex` 取。

### 6.7 `redirect-gateway` 被 push 会中断握手

服务端推 `redirect-gateway def1`，若客户端用 `--route-nopull` 则会报
`Options error: option 'redirect-gateway' cannot be used in this context ([PUSH-OPTIONS])`。
必须同时加 `--pull-filter ignore "redirect-gateway"`（和 `--pull-filter ignore "dhcp-option"` 一起）。

### 6.8 同机 wintun 适配器被占用

若系统里已有别的 VPN 客户端装着 wintun 适配器且处于禁用/占用态，
openvpn 直接 `--dev tun` 会报 `All wintun adapters on this system are currently in use or disabled`。
解法：`tapctl create --hwid wintun --name ovpn-ru-test` 建一个专属适配器，用 `--dev-node` 指定。

### 6.9 僵尸 /32 路由导致假失败（最容易误判成"节点不出流量"）

反复起停隧道后，宿主会残留指向**已拆除** wintun 接口的 `/32` 路由。新隧道建立后
`New-NetRoute` 若因"路由已存在"而跳过，请求就会走向已消失的接口 → `WinError 10065`。
表现是"同一个节点上一轮通、这一轮不通"，极易误判成节点质量问题。

**正确写法：先删后加。**

```powershell
Remove-NetRoute -DestinationPrefix "208.95.112.1/32" -Confirm:$false -ErrorAction SilentlyContinue
New-NetRoute    -DestinationPrefix "208.95.112.1/32" -InterfaceIndex <wintun_ifIndex> -NextHop "0.0.0.0"
```

本项目实测：修正前 12 个抽样里 2 个"失败"，修正后全部 `EGRESS_OK`（累计 27/27 全通）。

## 7. 最小实现骨架（30 行，可直接抄）

```python
import json, urllib.request

H = {"User-Agent": "Dart/3.5 (dart:io)", "Accept": "*/*"}
PROXY = {"http": "socks5h://127.0.0.1:10808",
         "https": "socks5h://127.0.0.1:10808"}          # 大陆出口必填
OP = urllib.request.build_opener(urllib.request.ProxyHandler(PROXY))
BASE = "https://site45.newmobilejumpstech.com/api/"

def api(path):
    with OP.open(urllib.request.Request(BASE + path, headers=H), timeout=30) as r:
        return json.loads(r.read().decode())

servers = api("allservers")["data"]                        # 1167 条
for s in servers:
    cfg = api("detail/" + s["slug"])["data"]["config"]      # 完整 .ovpn
    open(f'{s["id"]}.ovpn', "w", newline="\n").write(cfg)
```

## 8. 节点真实性判定（不上隧道也能判）

实测结论：**这批节点的隧道出口 IP == 节点 IP 本身**（入口即出口，不做二次转发）。
抽样 12 个节点建真隧道核对：出口 IP 与 `remote` 字段 100% 一致。

因此可以用 `remote` 的 ASN 归属直接判定：

```bash
curl -s -X POST http://ip-api.com/batch -H "Content-Type: application/json" \
  -d '[{"query":"167.160.94.156","fields":"country,isp,as,hosting"}]'
```

- 地理一致性实测：86/90（95.6%）标称国家与 IP 归属一致
- 不一致的典型：M247 的新加坡 IP 被标成 `Morocco`/`Malta`/`Panama`（虚拟位置）
- `hosting=true` 占比：68/90（75.6%）——全是数据中心，不是住宅 IP
