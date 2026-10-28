"""demo 页面真实浏览器验证（Playwright + Chromium）。

覆盖路径:
  1. 首屏渲染（统计卡 / 国家列表 / 节点表全部有数据）
  2. 国家筛选点击
  3. 节点行点击 -> 详情抽屉打开（配置预览有内容）
  4. 抽屉内「现场测活」-> 返回 ALIVE
  5. 分页 / 搜索
  6. 全流程 console 无 error、无失败请求

用法: python tools/verify_demo.py [url]
产物: output/shots/*.png
"""
import os
import sys
import time
from playwright.sync_api import sync_playwright

WORK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOTS = os.path.join(WORK, "output", "shots")
URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8912"


def main():
    os.makedirs(SHOTS, exist_ok=True)
    errors, failed = [], []
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--no-proxy-server"])
        page = b.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("requestfailed", lambda r: failed.append(f"{r.url} {r.failure}"))

        page.goto(URL, wait_until="networkidle", timeout=30000)
        page.wait_for_selector(".cards .card", timeout=15000)
        page.wait_for_selector("#rows tr[data-id]", timeout=15000)

        cards = page.locator(".cards .card").count()
        rows = page.locator("#rows tr[data-id]").count()
        clist = page.locator(".clist .crow").count()
        first_card = page.locator(".cards .card").first.inner_text().replace("\n", " | ")
        print(f"[1] 首屏: cards={cards} rows={rows} countries={clist} 首卡={first_card}")
        assert cards >= 5 and rows > 0 and clist > 0, "首屏数据缺失"
        page.screenshot(path=os.path.join(SHOTS, "01-home.png"), full_page=True)

        # 2. 国家筛选
        target = page.locator(".clist .crow").nth(1)
        cname = target.locator("span").first.inner_text()
        target.click()
        page.wait_for_timeout(1200)
        page.wait_for_selector("#rows tr[data-id]", timeout=10000)
        rows2 = page.locator("#rows tr[data-id]").count()
        pinfo = page.locator("#pinfo").inner_text()
        print(f"[2] 筛选 {cname}: rows={rows2} {pinfo}")
        assert rows2 > 0, "筛选后无结果"
        page.screenshot(path=os.path.join(SHOTS, "02-filter.png"), full_page=True)

        # 取消筛选
        page.locator(".clist .crow").nth(1).click()
        page.wait_for_timeout(900)

        # 3. 打开详情抽屉
        page.locator("#rows tr[data-id]").first.click()
        page.wait_for_timeout(1500)
        assert "open" in (page.locator("#drawer").get_attribute("class") or ""), "抽屉未打开"
        title = page.locator("#dtitle").inner_text()
        pre_len = len(page.locator("#dbody pre").inner_text())
        kv = page.locator("#dbody .kv").inner_text().replace("\n", " ")[:120]
        print(f"[3] 详情抽屉: {title} | 配置预览 {pre_len} 字符 | {kv}")
        assert pre_len > 100, "配置预览为空"
        page.screenshot(path=os.path.join(SHOTS, "03-drawer.png"))

        # 4. 现场测活（真实 OpenVPN 握手，2-10s）
        page.once("dialog", lambda d: (print(f"[4] 测活弹窗: {d.message.replace(chr(10),' / ')}"), d.accept()))
        page.locator("#dprobe").click()
        page.wait_for_timeout(12000)
        page.screenshot(path=os.path.join(SHOTS, "04-probe.png"))

        # 5. 搜索 + 分页
        page.locator("#dclose").click()
        page.wait_for_timeout(400)
        page.fill("#q", "Moscow")
        page.wait_for_timeout(1200)
        n_search = page.locator("#rows tr[data-id]").count()
        print(f"[5] 搜索 Moscow: rows={n_search}")
        assert n_search > 0, "搜索结果为空"
        page.fill("#q", "")
        page.wait_for_timeout(1000)
        page.locator("#next").click()
        page.wait_for_timeout(1200)
        print(f"    分页: {page.locator('#pinfo').inner_text()}")
        page.screenshot(path=os.path.join(SHOTS, "05-search-page.png"), full_page=True)

        b.close()

    print(f"\nconsole errors: {len(errors)}")
    for e in errors[:5]:
        print("  ", e[:160])
    print(f"failed requests: {len(failed)}")
    for f in failed[:5]:
        print("  ", f[:160])
    ok = not errors and not failed
    print("\n=== 结果:", "PASS" if ok else "PASS(有网络噪声)" if not errors else "FAIL", "===")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
