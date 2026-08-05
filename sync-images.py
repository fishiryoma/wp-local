"""
sync-images.py
同步 wp-content/uploads 到 Cloudflare R2，Account ID 自動從 .env 讀取。

Usage:
    python sync-images.py             # 一般同步（只新增/更新，不刪除 R2 上的檔案）
    python sync-images.py --dry-run   # 只列出差異，不實際上傳
    python sync-images.py --delete    # 完全同步（本地已刪除的檔案也會從 R2 移除）

Requirements:
    pip install python-dotenv boto3
    AWS/R2 憑證：.env 的 R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY，
    或已設定好的 aws profile（aws configure）

── 為什麼不用 `aws s3 sync` ────────────────────────────────────────────
`aws s3 sync` 是對「兩個已排序的清單」做 merge-join，並假設伺服器回傳的
物件清單是字典序。R2 的 ListObjectsV2 不符合這個假設，例如它會把
    generic-1-150x150.png.webp
排在
    generic-1-150x150.png
之前（字典序上 `X.png` 是 `X.png.webp` 的前綴，必須排在前面）。

本 bucket 因為每張圖都有 `X.jpg` / `X.jpg.webp` 這種配對，全站有一萬多處
這樣的順序反轉。merge-join 一對不齊就會把非 .webp 的原圖全部誤判成
「遠端不存在」，導致每次同步都重傳約一半的檔案（約 18,500 個）。

所以這裡改成自己把兩邊清單各自讀成 dict 再比對，完全不依賴回傳順序。
─────────────────────────────────────────────────────────────────────
"""

import sys
import os
import mimetypes
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from dotenv import load_dotenv

# 檔名含日文/中文，cp950 主控台直接 print 會噴 UnicodeEncodeError
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    import boto3
    from botocore.config import Config
except ImportError:
    print("ERROR: boto3 not installed. Run: python -m pip install boto3")
    sys.exit(1)

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

UPLOADS_DIR = BASE_DIR / "app" / "public" / "wp-content" / "uploads"
R2_BUCKET   = "tesstaiwan-uploads"
ACCOUNT_ID  = os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
MAX_WORKERS = 16

# mimetypes 少數副檔名認不得，明確補上
EXTRA_TYPES = {
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".svg":  "image/svg+xml",
}

_thread_local = threading.local()


def r2_client():
    """每個執行緒各自持有一個 client。"""
    client = getattr(_thread_local, "client", None)
    if client is None:
        # .env 有 R2 金鑰就優先使用，否則交給 boto3 自己找 aws profile
        key    = os.getenv("R2_ACCESS_KEY_ID") or None
        secret = os.getenv("R2_SECRET_ACCESS_KEY") or None
        client = boto3.client(
            "s3",
            endpoint_url=f"https://{ACCOUNT_ID}.r2.cloudflarestorage.com",
            aws_access_key_id=key,
            aws_secret_access_key=secret,
            region_name="auto",
            config=Config(max_pool_connections=MAX_WORKERS + 4),
        )
        _thread_local.client = client
    return client


def content_type(key: str) -> str:
    ext = os.path.splitext(key)[1].lower()
    if ext in EXTRA_TYPES:
        return EXTRA_TYPES[ext]
    guessed, _ = mimetypes.guess_type(key)
    return guessed or "application/octet-stream"


def list_remote() -> dict:
    """列出 bucket 全部物件 → {key: size}"""
    remote = {}
    paginator = r2_client().get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=R2_BUCKET):
        for obj in page.get("Contents", []):
            remote[obj["Key"]] = obj["Size"]
    return remote


def list_local() -> dict:
    """列出本地 uploads → {r2_key: size}"""
    local = {}
    for path in UPLOADS_DIR.rglob("*"):
        if path.is_file():
            key = str(path.relative_to(UPLOADS_DIR)).replace(os.sep, "/")
            local[key] = path.stat().st_size
    return local


def upload_one(key: str):
    r2_client().upload_file(
        str(UPLOADS_DIR / key),
        R2_BUCKET,
        key,
        ExtraArgs={"ContentType": content_type(key)},
    )
    return key


def delete_one(key: str):
    r2_client().delete_object(Bucket=R2_BUCKET, Key=key)
    return key


def run_parallel(label: str, keys: list, fn) -> int:
    """平行執行，回傳失敗數。"""
    done = failed = 0
    total = len(keys)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(fn, k): k for k in keys}
        for future in as_completed(futures):
            key = futures[future]
            try:
                future.result()
                done += 1
                print(f"  [{done + failed}/{total}] {label} {key}")
            except Exception as e:
                failed += 1
                print(f"  [{done + failed}/{total}] FAIL   {key}  ({e})")
    return failed


def main():
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        return

    if not ACCOUNT_ID:
        print("ERROR: .env 裡沒有設定 CLOUDFLARE_ACCOUNT_ID")
        sys.exit(1)

    dry_run   = "--dry-run" in sys.argv or "--dryrun" in sys.argv
    do_delete = "--delete" in sys.argv

    print("=" * 50)
    print("  R2 Image Sync")
    print(f"  Mode: {'dry-run' if dry_run else 'live'}"
          f"{' + delete' if do_delete else ''}")
    print("=" * 50)

    print("\n讀取 R2 物件清單...")
    remote = list_remote()
    print(f"讀取本地檔案清單...")
    local = list_local()

    added   = [k for k in local if k not in remote]
    changed = [k for k in local if k in remote and local[k] != remote[k]]
    orphan  = [k for k in remote if k not in local]
    todo    = sorted(added + changed)

    print()
    print(f"  本地檔案 : {len(local):,}")
    print(f"  R2 物件  : {len(remote):,}")
    print(f"  待上傳   : {len(todo):,}  (新增 {len(added):,}、size 不同 {len(changed):,})")
    print(f"  R2 孤兒  : {len(orphan):,}"
          f"{'  → 將刪除' if do_delete else '  (未加 --delete，不處理)'}")

    if dry_run:
        print()
        for k in todo[:50]:
            print(f"  (dry-run) upload {k}")
        if len(todo) > 50:
            print(f"  ... 其餘 {len(todo) - 50:,} 個")
        if do_delete:
            for k in sorted(orphan)[:50]:
                print(f"  (dry-run) delete {k}")
            if len(orphan) > 50:
                print(f"  ... 其餘 {len(orphan) - 50:,} 個")
        print("\n(dry-run，未實際變更 R2)")
        return

    failed = 0
    if todo:
        print(f"\n上傳 {len(todo):,} 個檔案...")
        failed += run_parallel("upload", todo, upload_one)

    if do_delete and orphan:
        print(f"\n刪除 {len(orphan):,} 個孤兒物件...")
        failed += run_parallel("delete", sorted(orphan), delete_one)

    print("\n" + "=" * 50)
    if failed:
        print(f"  ⚠ 完成，但有 {failed} 個失敗")
        print("=" * 50)
        sys.exit(1)
    print(f"  ✅ 同步完成（上傳 {len(todo):,}"
          f"{f'、刪除 {len(orphan):,}' if do_delete else ''}）")
    print("=" * 50)


if __name__ == "__main__":
    main()
