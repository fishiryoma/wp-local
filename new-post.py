"""
new-post.py
發布新文章一鍵腳本

依序執行：
  1. 壓縮新圖片（compress-images.py）
  2. 同步圖片到 Cloudflare R2（aws s3 sync）
  3. Build 受影響的 HTML 頁面（quick-publish.py，含 analytics 同步）
  4. 部署到 Cloudflare Pages（wrangler pages deploy）

圖片先於 HTML 上傳，避免訪客看到新頁面時圖片還沒就位。

Usage:
    python new-post.py http://tesstaiwan-local.local/你的文章網址/

Requirements:
    - Local WP 必須正在執行
    - pip install Pillow requests pymysql python-dotenv
    - npx wrangler 已安裝（npm install -g wrangler）
    - AWS CLI 已設定（aws configure）
"""

import sys
import time
import subprocess
from pathlib import Path
from dotenv import load_dotenv
import os

# ── 設定 ──────────────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

CF_ACCOUNT_ID = os.getenv("CF_ACCOUNT_ID", "")   # 選填，若已在 wrangler 設定則不需要
UPLOADS_DIR = BASE_DIR / "app" / "public" / "wp-content" / "uploads"
R2_BUCKET   = "tesstaiwan-uploads"
R2_ENDPOINT = f"https://{os.getenv('CF_ACCOUNT_ID', 'YOUR_ACCOUNT_ID')}.r2.cloudflarestorage.com"
DEPLOY_DIR  = BASE_DIR / "deploy"
CF_PROJECT  = "tesstaiwan"
# ─────────────────────────────────────────────────────────────────────


def run(label: str, cmd: list[str], cwd=None, clean_cf_token=False) -> bool:
    """執行子指令，回傳是否成功"""
    print(f"\n{'='*50}")
    print(f"  {label}")
    print(f"{'='*50}")
    env = None
    if clean_cf_token:
        # wrangler 會誤用 CF_API_TOKEN（analytics token），清掉讓它用自己的登入憑證
        env = os.environ.copy()
        env.pop("CLOUDFLARE_API_TOKEN", None)  # 以防萬一
    result = subprocess.run(cmd, cwd=cwd or BASE_DIR, env=env)
    if result.returncode != 0:
        print(f"\n❌ {label} 失敗（exit code {result.returncode}）")
        return False
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

    # ── Step 1: 壓縮圖片 ─────────────────────────────────────────────
    ok = run(
        "Step 1/4  壓縮新圖片",
        [sys.executable, str(BASE_DIR / "compress-images.py")],
    )
    if not ok:
        print("壓縮失敗，中止。")
        sys.exit(1)

    # ── Step 2: 同步圖片到 R2 ─────────────────────────────────────────
    r2_cmd = [
        "aws", "s3", "sync",
        str(UPLOADS_DIR),
        f"s3://{R2_BUCKET}",
        "--endpoint-url", R2_ENDPOINT,
        "--size-only",   # R2 的 ETag 與 S3 不同，用大小比對避免重複上傳
    ]
    ok = run("Step 2/4  同步圖片到 Cloudflare R2", r2_cmd)
    if not ok:
        print("R2 同步失敗，中止。")
        sys.exit(1)

    # ── Step 3: Build HTML（含 analytics 同步）───────────────────────
    ok = run(
        "Step 3/4  Build HTML 頁面",
        [sys.executable, str(BASE_DIR / "quick-publish.py"), post_url],
    )
    if not ok:
        print("Build 失敗，中止。")
        sys.exit(1)

    # ── Step 4: Deploy 到 Cloudflare Pages ───────────────────────────
    ok = run(
        "Step 4/4  部署到 Cloudflare Pages",
        ["npx", "wrangler", "pages", "deploy", "deploy",
         "--project-name", CF_PROJECT, "--branch", "production"],
        cwd=BASE_DIR,
        clean_cf_token=True,  # 避免 analytics token 干擾 wrangler 認證
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
