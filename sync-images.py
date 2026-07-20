"""
sync-images.py
同步 wp-content/uploads 到 Cloudflare R2，Account ID 自動從 .env 讀取。

Usage:
    python sync-images.py           # 一般同步（只新增/更新，不刪除 R2 上的檔案）
    python sync-images.py --delete  # 完全同步（本地已刪除的檔案也會從 R2 移除）

Requirements:
    pip install python-dotenv
    aws CLI 已設定（aws configure）
"""

import sys
import subprocess
from pathlib import Path
from dotenv import load_dotenv
import os

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

UPLOADS_DIR = BASE_DIR / "app" / "public" / "wp-content" / "uploads"
R2_BUCKET   = "tesstaiwan-uploads"
ACCOUNT_ID  = os.getenv("CLOUDFLARE_ACCOUNT_ID", "")


def main():
    if not ACCOUNT_ID:
        print("ERROR: .env 裡沒有設定 CLOUDFLARE_ACCOUNT_ID")
        sys.exit(1)

    cmd = [
        "aws", "s3", "sync",
        str(UPLOADS_DIR),
        f"s3://{R2_BUCKET}",
        "--endpoint-url", f"https://{ACCOUNT_ID}.r2.cloudflarestorage.com",
        "--size-only",  # R2 的 ETag 與 S3 不同，用大小比對避免重複上傳
    ]
    if "--delete" in sys.argv:
        cmd.append("--delete")

    result = subprocess.run(cmd)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
