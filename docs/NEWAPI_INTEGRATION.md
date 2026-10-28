# new-api 对接指南（RUSSIA VPN 节点 → new-api 代理池）

> 面向：负责对接的会话/工程师。目标是把本项目收割的 OpenVPN 节点，变成 new-api
> 代理池里一条**独立的节点源**（不动现有住宅代理与免费源）。
>
> **证据等级标注**：✅ 已验证（本项目实跑） / 📖 静态确认（读 new-api 源码） / ⚠️ 待验证（未实跑）

---

## 0. 三句话结论

1. 这批节点是 **OpenVPN（udp/1194 + 客户端证书 + tls-crypt）**，**不是** SOCKS5/HTTP 代理 ——
   new-api 不能直接吃，必须先起隧道再在隧道内做 SOCKS5 出口。
2. new-api 侧**不需要改一行业务代码**：`TRYINGOPEN_PROXY_FILE` 原生支持 socks5:// 且支持
   **逗号分隔多文件**，正好留给"本地协议转换层"一份独立清单。
3. 端到端最短路径：`output/ovpn/*.ovpn` → `tools/socks_layer.py`（netns 隧道池）→
   `output/newapi_proxies.txt`（`socks5://127.0.0.1:4400x`）→ new-api 的 `TRYINGOPEN_PROXY_FILE`。

---

## 1. 链路图

```
┌─────────────────────── 本仓库已交付（✅ 已验证） ───────────────────────┐
│  site45.newmobilejumpstech.com/api/allservers  (1167 条目录)            │
│              ↓ harvest.py                                              │
│  output/ovpn/*.ovpn  (含 client cert + key + tls-crypt)                │
│              ↓ probe.py（真实握手，48 并发）                            │
│  output/probe_results.json  (725 ALIVE / 62.1%)                        │
└────────────────────────────────┬───────────────────────────────────────┘
                                 ↓
┌──────────── 转换层（⚠️ 代码就绪，本机未实跑，见 §5） ───────────────────┐
│  tools/socks_layer.py                                                  │
│    每节点一个 netns: openvpn --dev tun0 + microsocks:34000+i           │
│    宿主 127.0.0.1:44000+i  --TCP forward-->  netns 内 socks              │
│    内建 fail-closed：隧道断即删默认路由，绝不回落成宿主 IP              │
│              ↓                                                         │
│  output/newapi_proxies.txt                                             │
│    # Russia,Moscow1 | exit=135.136.44.43 | AS...                       │
│    socks5://127.0.0.1:44000                                            │
│    ...                                                                 │
└────────────────────────────────┬───────────────────────────────────────┘
                                 ↓
┌──────────────── new-api（📖 静态确认，无需改代码） ────────────────────┐
│  TRYINGOPEN_PROXY_FILE=/app/proxies/residential.txt,/app/proxies/russia.txt │
│     → LoadProxies() 把每行加载为 source="residential", healthScore=1.0  │
│     → AcquireForChannel() 加权随机 + 冷却 + 每日限额                    │
│     → 出站 HTTP/SSE 走该 SOCKS5                                         │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 2. 前置：节点清单（已完成）

```bash
python tools/harvest.py 2 3        # 拉全量 1167 条（已跑完，产物在 output/）
python tools/probe.py --workers 48 # 全量测活（已跑完，725 ALIVE）
python tools/node_index.py         # 生成 output/alive_nodes.txt / node_index.md
```

当前产物（✅ 已验证）：

| 文件 | 内容 |
|------|------|
| `output/ovpn/*.ovpn` | 1167 份完整配置（**含私钥，勿外发**） |
| `output/probe_results.json` | 每节点 `verdict`（ALIVE/DEAD）、`elapsed`、`tunnel_ip` |
| `output/alive_nodes.txt` | 725 行 `IP:port\tproto\tcountry\t握手\tovpn 文件名` |

---

## 3. 第一步：起 SOCKS5 转换层（在 Linux / WSL 里跑）

```bash
# WSL/Ubuntu 前置
sudo apt-get install -y openvpn microsocks iproute2 iptables curl

# 起 20 条隧道（server 需要 root：ip netns / iptables）
sudo python3 tools/socks_layer.py --limit 20
#   --countries "Russia,Moscow"   优先挑指定国家
#   --limit 50                    规模上限（受本机 netns/tun/文件描述符限制）
```

产出：

- `output/socks_pool.json` — 每条隧道的 `exit_ip` / `exit_country` / `exit_isp` / `socks` / 状态
- `output/newapi_proxies.txt` — **new-api 直接消费的清单**（每行一个 `socks5://`）

**为什么用 netns**：每个节点私有路由表与 tun，多个节点可以同时拿到相同的 `10.8.0.x` 不冲突，
宿主的 `v2rayN/xray(10808)` 和默认路由完全不受影响。单机 20-50 条是合理量级。

**为什么必须 fail-closed**：OpenVPN 断线时若默认路由还在，流量会从物理网卡出去 →
你的真实 IP 泄漏、且 new-api 以为还在用代理。脚本里 `fail_closed()` 会在隧道 up 后
删掉 veth 默认路由（只保留 VPN 服务器 /32 与 tun0 默认路由），断线即全断。

---

## 4. 第二步：接入 new-api（📖 静态确认，改动=0 行业务代码）

### 4.1 环境变量

```yaml
# docker-compose.yml 的 new-api 服务
services:
  new-api:
    environment:
      # 关键：逗号分隔多文件，恰好一份住宅 + 一份协议转换层
      - TRYINGOPEN_PROXY_FILE=/app/proxies/residential.txt,/app/proxies/russia.txt
      # 可选兜底（不推荐用于大批量）：逗号分隔 URL
      # - TRYINGOPEN_PROXIES=socks5://127.0.0.1:44000,socks5://127.0.0.1:44001
      # 每代理每日使用上限（0=不限）。协议转换层端口有限，建议设上限避免打爆
      - TRYINGOPEN_PROXY_MAX_USE_PER_DAY=2000
    volumes:
      - ./proxies:/app/proxies:ro
    # Linux 宿主要让容器访问 127.0.0.1 的 SOCKS5，二选一：
    extra_hosts:
      - "host.docker.internal:host-gateway"   # 然后清单里写 socks5://host.docker.internal:44000
    # 或者整机 network_mode: host（最简单，但会失去端口隔离）
```

### 4.2 清单文件格式（`/app/proxies/russia.txt`）

`LoadProxies()` 的解析规则（源码 `relay/channel/tryingopen/proxy_pool.go:376-433`）：

- 每行一个代理 URL，**空行与 `#` 开头的行自动跳过**
- 支持 `socks5://host:port`、`http://user:pass@host:port`
- 同一文件内重复 URL 去重；与其它文件重复也会去重
- 加载后 `source="residential"`、`healthScore=1.0`（**池里权重最高的一档**）

```text
# Russia VPN socks5 pool, generated 2026-10-29T03:30:00, 20 tunnels
# Russia,Moscow1 | exit=135.136.44.43 | M247
socks5://127.0.0.1:44000
# Canada | exit=167.160.94.156 | Dedicated.com
socks5://127.0.0.1:44001
```

`tools/socks_layer.py` 生成的 `output/newapi_proxies.txt` 就是这个格式，直接复制成
`proxies/russia.txt` 即可。

### 4.3 生效方式

```bash
# 方式 A：重启容器（最可靠）
docker compose restart new-api

# 方式 B：调管理端点热重载（📖 静态确认存在，⚠️ 未在本项目实测）
curl -X POST "$BASE/api/twoapi/proxy_pool/reload" -H "Authorization: Bearer $ADMIN_TOKEN"
# 关联端点：
#   GET  /api/twoapi/proxy_pool          池快照（分页/按源）
#   POST /api/twoapi/proxy_pool/config   改抓取周期等
```

### 4.4 容器内地址问题（最容易踩）

`TRYINGOPEN_PROXY_FILE` 里的 `127.0.0.1` 指的是**容器自己的 loopback**，不是宿主的。
转换层跑在宿主/WSL 上时，必须三选一：

| 方案 | 做法 | 代价 |
|------|------|------|
| A. `extra_hosts` | 清单写 `socks5://host.docker.internal:44000` | Linux 需加 `host.docker.internal:host-gateway`；端口要绑 `0.0.0.0` 而非 `127.0.0.1` |
| B. `network_mode: host` | 清单保持 `127.0.0.1` | 失去容器网络隔离 |
| C. 转换层也进同一 compose 网络 | 清单写 `socks5://sockslayer:44000` | 需要把 `socks_layer.py` 容器化（`--privileged` + `/dev/net/tun`） |

`socks_layer.py` 目前把 relay 绑在 `127.0.0.1`（安全默认）。走方案 A 时把
`make_relay()` 里的 `"127.0.0.1"` 改成 `"0.0.0.0"`，并只在内网/本机防火墙后使用。

---

## 5. 第三步：验证（三层，缺一层都不算通）

### 5.1 隧道层

```bash
# 每条隧道的真实出口（脚本已内建，也可手工查）
ip netns exec vpn0 curl -s "http://208.95.112.1/json/?fields=query,country,isp"
# 期望：出口国家 == 该节点的标称 country（本项目 27/27 抽样实测一致 ✅）

# 宿主侧 SOCKS5 是否可连
curl -s --socks5-hostname 127.0.0.1:44000 https://api.ipify.org
```

### 5.2 new-api 池层

```bash
# 池快照：确认 source=residential 里出现你的 russia 条目
curl -s "$BASE/api/twoapi/proxy_pool?page_size=50" -H "Authorization: Bearer $ADMIN_TOKEN" | jq '.'
```

预期：条目 `source` 为 `residential`、`host` 为 `127.0.0.1:44000`（或 host.docker.internal）。

### 5.3 业务层（唯一能证明"真的在用"的一层）

发一个走该渠道的真实请求，然后在**上游侧**核对来源 IP：

```bash
curl -s "$BASE/v1/chat/completions" -H "Authorization: Bearer $USER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model":"tryingopen/qwen/qwen3.8-flash","messages":[{"role":"user","content":"hi"}]}'
```

再看 new-api 日志里该请求使用的代理，或抓包确认目的端口是 4400x。
**只看池快照有条目 ≠ 流量真的走了它**（这是最容易假验证的地方）。

---

## 6. 必避的坑（全部来自读 new-api 源码，📖 静态确认）

1. **住宅源不做真实出口拨测**。`TraceProbes()` 里 `if e.source != "free" { continue }` ——
   文件加载的代理（我们的清单）**不会被拨测**。所以出口 IP 的正确性必须由转换层
   （`check_exit_ip()`）自己保证，不能指望 new-api 帮你验。
2. **`127.0.0.1` 在容器里是容器自己**。见 §4.4，这是第一次对接最常见的失败点。
3. **域名要单独确认可达**：代理只负责出口，容器内的 DNS 若解析不了上游域名，照样失败。
4. **429 会推到下一个代理并冷却**：`MarkRateLimitedForChannel` 按 24h 窗口内使用次数递增
   冷却（0→30s→90s→300s→900s，`TRYINGOPEN_USE_COOLDOWN_MAP` 可配）。协议层节点少时，
   20 个端口被打到 429 会迅速整池冷却 —— 要么控并发，要么加节点。
5. **全局熔断阈值 5**：同一代理连续失败 5 次会进入 10 分钟全局冷却（`globalFailThreshold`）。
   隧道抖动时集中失败会触发，表现为"整池突然没可用"。
6. **每日限额**：`TRYINGOPEN_PROXY_MAX_USE_PER_DAY`（0=不限）。端口数固定时建议设上限，
   否则单端口被打爆会连带拖慢所有业务。
7. **免费代理缓存是另一个文件**：`free_proxies_cache.txt`（格式 `origin\traw`），
   不要手工往那里写 —— 它由后台任务回收（超 3h 且 30min 未使用即剔除），你写的会被 reap。
8. **别把 ovpn 私钥放容器里**：转换层只需要 ovpn 文件在**宿主**；容器只拿 socks5 清单。

---

## 7. 运维建议

| 事项 | 频率 | 做法 |
|------|------|------|
| 节点重测活 | 6-12 小时 | `python tools/probe.py --workers 48`（全量 2.5 分钟） |
| 转换层重建 | 随测活 | 重跑 `socks_layer.py --limit N`，新清单覆盖 |
| new-api 重载 | 随清单变化 | 重启容器，或调 `/api/twoapi/proxy_pool/reload` |
| 双阈值摘除 | 每轮 | 连续 **2 轮** 握手失败才剔除（单轮抖动常见，一次失败的下一轮常恢复） |

规模建议：单机 netns 隧道 **20-50 条**（每条 ≈ 1 tun + 1 veth + 1 microsocks + 1 openvpn 进程）。
要更多就多机，或改成"按需起停"。

---

## 8. 合规提醒（必须原样转达给决策者）

- 这些节点是 **mobilejumps 白标平台租用的商业数据中心**（M247 / G-Core / Dedicated.com / Contabo，
  90 样本中 68 个 `hosting=true`）。**既不是运营商自有基础设施，也不是付费终端用户设备。**
- **凭证获取 ≠ 授权使用**。把它们挂进生产代理池 = 使用未授权资源，风险与责任在运营方。
- 本项目已完成的部分定位为**研究 demo**（用户 2026-10-29 拍板）。若决定上生产，请自行评估：
  上游随时可能封端口/换证书、无任何 SLA、且节点静默死亡率高（实测 37.9% 已死）。
- `output/ovpn/` 含客户端私钥与 tls-crypt 密钥，**不得提交、不得外发**（已在 `.gitignore`）。
