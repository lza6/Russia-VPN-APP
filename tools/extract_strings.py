"""RUSSIA VPN 字符串侦察：libapp.so / dex / resources.arsc 三路提取 + 域名与端点正则过滤。"""
import os
import re
import sys

APK = r"C:\Users\Administrator.DESKTOP-EGNE9ND\Desktop\逆向产物\RUSSIA VPN v4.2.8.6 b4286 (Premium by @ElaMods).apk"
APKDIR = r"C:\Users\Administrator.DESKTOP-EGNE9ND\Desktop\逆向产物\RussiaVPN_4.2.8.6\apk"
OUT = r"C:\Users\Administrator.DESKTOP-EGNE9ND\Desktop\逆向产物\RussiaVPN_4.2.8.6\recon"

PRINTABLE = re.compile(rb"[ -~]{4,}")


def extract(path, dst):
    data = open(path, "rb").read()
    ss = [m.group().decode("ascii", "ignore") for m in PRINTABLE.finditer(data)]
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, dst), "w", encoding="utf-8") as f:
        f.write("\n".join(ss))
    return ss


def main():
    import zipfile

    z = zipfile.ZipFile(APK)
    targets = {
        "libapp_strings.txt": "lib/arm64-v8a/libapp.so",
        "flutter_strings.txt": "lib/arm64-v8a/libflutter.so",
        "openvpn_strings.txt": "lib/arm64-v8a/libopenvpn.so",
        "arsc_strings.txt": "resources.arsc",
        "dex1_strings.txt": "classes.dex",
        "dex2_strings.txt": "classes2.dex",
        "dex3_strings.txt": "classes3.dex",
        "dex4_strings.txt": "classes4.dex",
    }
    os.makedirs(OUT, exist_ok=True)
    all_strings = {}
    for dst, name in targets.items():
        data = z.read(name)
        ss = [m.group().decode("ascii", "ignore") for m in PRINTABLE.finditer(data)]
        open(os.path.join(OUT, dst), "w", encoding="utf-8").write("\n".join(ss))
        all_strings[dst] = ss
        print(f"{dst}: {len(ss)} strings from {name} ({len(data)} bytes)")

    # 域名候选
    host_re = re.compile(r"\b(?:https?://)?([a-z0-9][a-z0-9-]{1,60}(?:\.[a-z0-9-]{1,60})+)\b", re.I)
    skip = ("schemas.android.com", "w3.org", "apache.org", "github.com", "google.com",
            "android.com", "kotlinlang.org", "json-schema.org", "openssl.org", "dart.dev",
            "flutter.dev", "godotengine.org", "sqlite.org", "gnu.org", "mozilla.org",
            "unicode.org", "iana.org", "ietf.org", "xml.org", "example.com")
    hosts = {}
    for dst, ss in all_strings.items():
        for s in ss:
            for m in host_re.finditer(s):
                h = m.group(1).lower().strip(".")
                if any(h.endswith(x) or h == x for x in skip):
                    continue
                if not re.search(r"\.[a-z]{2,}$", h):
                    continue
                hosts.setdefault(h, set()).add(dst)

    with open(os.path.join(OUT, "hosts.txt"), "w", encoding="utf-8") as f:
        for h in sorted(hosts):
            f.write(f"{h}\t{','.join(sorted(hosts[h]))}\n")
    print(f"\n=== {len(hosts)} host candidates -> recon/hosts.txt ===")

    # API / 路径候选
    path_re = re.compile(r"(/api/[A-Za-z0-9_./{}$:-]{2,80})")
    paths = {}
    for dst, ss in all_strings.items():
        for s in ss:
            if s.startswith("/api/") or "mobilejumps" in s or ".public/api" in s:
                paths.setdefault(s, set()).add(dst)
            for m in path_re.finditer(s):
                paths.setdefault(m.group(1), set()).add(dst)
    with open(os.path.join(OUT, "api_paths.txt"), "w", encoding="utf-8") as f:
        for p in sorted(paths):
            f.write(f"{p}\t{','.join(sorted(paths[p]))}\n")
    print(f"=== {len(paths)} path candidates -> recon/api_paths.txt ===")


if __name__ == "__main__":
    main()
