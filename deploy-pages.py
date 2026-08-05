"""
deploy-pages.py
把 deploy/ 推上 Cloudflare Pages（production）。

Usage:
    python deploy-pages.py

單獨執行的時機：
    - build-static.py 用了 --no-deploy，事後補部署
    - build-static.py 因為有頁面抓取失敗而跳過部署，確認過 _failed_urls.txt 後補部署
    - quick-publish.py 建完後補部署

Requirements:
    npx wrangler 已安裝（npm install -g wrangler）並已 wrangler login
"""

import sys
import os
import shutil
import subprocess
from pathlib import Path

# 訊息含 emoji（❌）與中文，cp950 主控台直接 print 會噴 UnicodeEncodeError
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR   = Path(__file__).parent
DEPLOY_DIR = BASE_DIR / "deploy"
CF_PROJECT = "tesstaiwan"
CF_BRANCH  = "production"


def main():
    if not DEPLOY_DIR.is_dir():
        print(f"❌ 找不到 {DEPLOY_DIR}，請先執行 build（new-post.py / build-static.py）")
        sys.exit(1)

    # Windows 的 CreateProcess 搜尋 PATH 時只補 .exe、不套用 PATHEXT，所以 npx
    # （實際是 npx.CMD）直接丟給 subprocess 會噴 FileNotFoundError WinError 2。
    # 在 PowerShell 手打同一行沒事，是因為 shell 才會套 PATHEXT。
    exe = shutil.which("npx")
    if exe is None:
        print("❌ 找不到 npx，請確認已安裝 Node.js 並在 PATH 中")
        sys.exit(1)

    # wrangler 會誤用 CLOUDFLARE_API_TOKEN（analytics token），清掉讓它用自己的登入憑證
    env = os.environ.copy()
    env.pop("CLOUDFLARE_API_TOKEN", None)

    cmd = [
        exe, "wrangler", "pages", "deploy", "deploy",
        "--project-name", CF_PROJECT,
        "--branch", CF_BRANCH,
    ]

    result = subprocess.run(cmd, cwd=BASE_DIR, env=env)
    if result.returncode != 0:
        print(f"\n❌ 部署失敗（exit code {result.returncode}）")
        sys.exit(1)


if __name__ == "__main__":
    main()
