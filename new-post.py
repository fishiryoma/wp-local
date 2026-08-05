"""
new-post.py
發布新文章一鍵腳本

依序執行：
  1. DB 備份檢查（backup-db.py，距上次 >= 30 天才真的備份）
  2. 壓縮新圖片（compress-images.py）
  3. 同步圖片到 Cloudflare R2（sync-images.py）
  4. Build 受影響的 HTML 頁面（quick-publish.py，含 analytics 同步）
  5. 部署到 Cloudflare Pages（deploy-pages.py）

圖片先於 HTML 上傳，避免訪客看到新頁面時圖片還沒就位。
備份放最前面：發文本身不改 DB，但先備份可保證後續任一步失敗時已有備份。

Usage:
    python new-post.py http://tesstaiwan-local.local/你的文章網址/

Requirements:
    - Local WP 必須正在執行
    - pip install Pillow requests pymysql python-dotenv boto3 mysql-connector-python
    - npx wrangler 已安裝（npm install -g wrangler）並已 wrangler login
    - R2 憑證：.env 的 R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY 或 aws configure
"""

import sys
import time
import shutil
import subprocess
from pathlib import Path

# 訊息含 emoji（❌ ⚠️ ✅）與中文，cp950 主控台直接 print 會噴 UnicodeEncodeError
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── 設定 ──────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
# ─────────────────────────────────────────────────────────────────────


def run(label: str, cmd: list[str], cwd=None, fatal: bool = True) -> bool:
    """執行子指令，回傳是否成功。

    fatal=False 用於「失敗了也該讓流程繼續」的步驟（例如 DB 備份）：
    只印警告並回傳 True，不讓呼叫端中止。
    """
    print(f"\n{'='*50}")
    print(f"  {label}")
    print(f"{'='*50}")

    # Windows 的 CreateProcess 搜尋 PATH 時只補 .exe、不套用 PATHEXT，所以像 npx
    # （實際是 npx.CMD）這種指令直接丟給 subprocess 會噴 FileNotFoundError WinError 2。
    # 在 PowerShell 手打同一行沒事，是因為 shell 才會套 PATHEXT。
    exe = shutil.which(cmd[0])
    if exe is None:
        msg = f"找不到執行檔 `{cmd[0]}`，請確認已安裝並在 PATH 中"
        if fatal:
            print(f"\n❌ {label} 失敗：{msg}")
            return False
        print(f"\n⚠️  {label} 略過：{msg}")
        return True
    cmd = [exe, *cmd[1:]]

    result = subprocess.run(cmd, cwd=cwd or BASE_DIR)
    if result.returncode != 0:
        if fatal:
            print(f"\n❌ {label} 失敗（exit code {result.returncode}）")
            return False
        print(f"\n⚠️  {label} 失敗（exit code {result.returncode}），繼續執行後續步驟")
    return True


def main():
    if len(sys.argv) < 2:
        print("Usage: python new-post.py <post-url>")
        print("Example: python new-post.py http://tesstaiwan-local.local/my-post/")
        sys.exit(1)

    post_url   = sys.argv[1]
    start_time = time.time()

    print("=" * 50)
    print("  New Post Publisher")
    print(f"  {post_url}")
    print("=" * 50)

    # ── Step 1: DB 備份檢查 ──────────────────────────────────────────
    # 備份失敗不該擋住發文，所以 fatal=False（與 build-static.py 的處理一致）
    run(
        "Step 1/5  DB 備份檢查",
        [sys.executable, str(BASE_DIR / "backup-db.py")],
        fatal=False,
    )

    # ── Step 2: 壓縮圖片 ─────────────────────────────────────────────
    ok = run(
        "Step 2/5  壓縮新圖片",
        [sys.executable, str(BASE_DIR / "compress-images.py")],
    )
    if not ok:
        print("壓縮失敗，中止。")
        sys.exit(1)

    # ── Step 3: 同步圖片到 R2 ─────────────────────────────────────────
    ok = run(
        "Step 3/5  同步圖片到 Cloudflare R2",
        [sys.executable, str(BASE_DIR / "sync-images.py")],
    )
    if not ok:
        print("R2 同步失敗，中止。")
        sys.exit(1)

    # ── Step 4: Build HTML（含 analytics 同步）───────────────────────
    ok = run(
        "Step 4/5  Build HTML 頁面",
        [sys.executable, str(BASE_DIR / "quick-publish.py"), post_url],
    )
    if not ok:
        print("Build 失敗，中止。")
        sys.exit(1)

    # ── Step 5: Deploy 到 Cloudflare Pages ───────────────────────────
    ok = run(
        "Step 5/5  部署到 Cloudflare Pages",
        [sys.executable, str(BASE_DIR / "deploy-pages.py")],
    )
    if not ok:
        print("Deploy 失敗。")
        sys.exit(1)

    # ── 完成 ─────────────────────────────────────────────────────────
    elapsed = int(time.time() - start_time)
    mins, secs = divmod(elapsed, 60)
    print("\n" + "=" * 50)
    print("  ✅ 發布完成！")
    print(f"  Time: {mins}m {secs}s")
    print("=" * 50)


if __name__ == "__main__":
    main()
