"""
backup-plugins-list.py
掃描 app/public/wp-content/plugins，把「裝了哪些外掛、哪個版本」寫成
app/public/wp-content/plugins-manifest.json（這支腳本放公開工具 repo，輸出的
清單是站台專屬資料，進 private repo——見 app/public/wp-content/docs/RESTORE.md）。

外掛原始碼本身不備份（都是第三方套件，重灌環境時手動照清單重裝就好）：
免費外掛可直接用 download_url 從 wordpress.org 抓對應版本的 zip 回來；
若某個外掛在 wordpress.org 上找不到同名 slug（代表可能是純付費/私有外掛），
download_url 會是 null，需要自己想辦法重新取得。

Usage:
    python backup-plugins-list.py
"""

import sys
import re
import json
import datetime
from pathlib import Path

# 訊息含中文，cp950 主控台直接 print 會噴 UnicodeEncodeError 中斷腳本
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

WP_ROOT       = Path(__file__).parent / "app" / "public"
PLUGINS_DIR   = WP_ROOT / "wp-content" / "plugins"
MU_PLUGINS_DIR = WP_ROOT / "wp-content" / "mu-plugins"
MANIFEST_FILE = WP_ROOT / "wp-content" / "plugins-manifest.json"

# wp-content/mu-plugins 底下這幾個是搬家前舊主機（Bluehost/Endurance）留下的殘留檔案，
# 本機開發用不到，不需要備份也不用列進清單提醒重裝。
# sso.php 檔頭寫著 Plugin Name: SSO / Author: Garth Mortensen, Mike Hansen——
# 不是我們自己寫的客製化程式碼，是同一批 Bluehost/EIG 主機留下的 SSO 登入外掛，歸在這一類。
IGNORED_MU_PLUGINS = {
    "endurance-browser-cache.php",
    "endurance-page-cache.php",
    "sso.php",
}

# 這裡放「真的是自己寫的」客製化 mu-plugin，原始碼直接 git 保存，不走「清單 + 重新下載」流程
CUSTOM_MU_PLUGINS = set()

HEADER_RE = re.compile(
    r"^[ \t/*#@]*Plugin Name:\s*(.+)$",
    re.IGNORECASE | re.MULTILINE,
)
VERSION_RE = re.compile(
    r"^[ \t/*#@]*Version:\s*(.+)$",
    re.IGNORECASE | re.MULTILINE,
)


def find_main_file(plugin_dir):
    """在外掛資料夾『最上層』找含 Plugin Name header 的主檔案（不遞迴進子資料夾）。"""
    preferred = plugin_dir / f"{plugin_dir.name}.php"
    candidates = [preferred] if preferred.exists() else []
    candidates += sorted(p for p in plugin_dir.glob("*.php") if p != preferred)

    for php_file in candidates:
        try:
            head = php_file.read_text(encoding="utf-8", errors="ignore")[:8192]
        except OSError:
            continue
        if HEADER_RE.search(head):
            return php_file, head
    return None, None


def scan_plugins():
    plugins = []
    if not PLUGINS_DIR.exists():
        print(f"  找不到外掛資料夾：{PLUGINS_DIR}")
        return plugins

    for plugin_dir in sorted(p for p in PLUGINS_DIR.iterdir() if p.is_dir()):
        slug = plugin_dir.name
        main_file, head = find_main_file(plugin_dir)

        if not main_file:
            print(f"  [略過] {slug}：找不到含 Plugin Name 的主檔案")
            continue

        name_match = HEADER_RE.search(head)
        version_match = VERSION_RE.search(head)
        name = name_match.group(1).strip() if name_match else slug
        version = version_match.group(1).strip() if version_match else None

        if version:
            download_url = f"https://downloads.wordpress.org/plugin/{slug}.{version}.zip"
        else:
            download_url = None

        plugins.append({
            "slug": slug,
            "name": name,
            "version": version,
            "download_url": download_url,
            "info_url": f"https://wordpress.org/plugins/{slug}/",
            "note": None if version else "找不到版本號，需自行確認來源（可能是付費/私有外掛）",
        })
        print(f"  {slug}: {name} {version or '(版本未知)'}")

    return plugins


def scan_mu_plugins():
    custom_in_git = []
    ignored_host_bundled = []
    unclassified = []

    if not MU_PLUGINS_DIR.exists():
        return {
            "custom_in_git": custom_in_git,
            "ignored_host_bundled": ignored_host_bundled,
            "unclassified": unclassified,
        }

    for php_file in sorted(MU_PLUGINS_DIR.glob("*.php")):
        if php_file.name in CUSTOM_MU_PLUGINS:
            custom_in_git.append(php_file.name)
        elif php_file.name in IGNORED_MU_PLUGINS:
            ignored_host_bundled.append(php_file.name)
        else:
            unclassified.append(php_file.name)

    if unclassified:
        print(f"  [注意] mu-plugins 裡有未分類的檔案，請手動確認：{', '.join(unclassified)}")

    return {
        "custom_in_git": custom_in_git,
        "ignored_host_bundled": ignored_host_bundled,
        "unclassified": unclassified,
    }


def main():
    print("  掃描 wp-content/plugins...")
    plugins = scan_plugins()

    print("  掃描 wp-content/mu-plugins...")
    mu_plugins = scan_mu_plugins()

    manifest = {
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "plugins": plugins,
        "mu_plugins": mu_plugins,
    }

    MANIFEST_FILE.write_text(
        json.dumps(manifest, indent=4, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"  完成：{len(plugins)} 個外掛 -> {MANIFEST_FILE.name}")


if __name__ == "__main__":
    main()
