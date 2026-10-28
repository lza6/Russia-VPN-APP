"""对指定 ovpn 文件复测隧道出口（用于验证路由修正是否消除 WinError 10065）。"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import tunnel_test as tt  # noqa: E402

WORK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    targets = [l.strip() for l in open(os.path.join(WORK, "tmp_retry.txt"), encoding="utf-8") if l.strip()]
    print(f"retry {len(targets)} nodes")
    for name in targets:
        cfg = os.path.join(tt.OVPN, name)
        if not os.path.exists(cfg):
            print(f"  {name}: config missing")
            continue
        row = tt.test_node(cfg, with_speed=True)
        print(f"  {name[:44]:46s} {row['verdict']:18s} exit={row.get('exit_ip')} "
              f"country={row.get('exit_country')} rtt={row.get('rtt_ms')}ms "
              f"kbps={row.get('down_kbps')} err={row.get('error')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
