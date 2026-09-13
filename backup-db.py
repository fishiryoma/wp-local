"""
backup-db.py
MySQL -> gzip SQL dump -> Cloudflare R2（bucket 名稱見 .env 的 R2_BACKUP_BUCKET）

Usage:
    python backup-db.py           # 若距上次備份 >= 30 天才執行
    python backup-db.py --force   # 強制備份，略過日期檢查

Requirements:
    pip install python-dotenv mysql-connector-python --break-system-packages
    aws CLI 已設定 R2 存取金鑰（aws configure）

備份前請先在 Cloudflare Dashboard 手動建立 R2 Bucket，並在 .env 設定 R2_BACKUP_BUCKET
"""

import sys
import json
import gzip
import datetime
import os
import subprocess
from pathlib import Path

# 訊息含中文，cp950 主控台直接 print 會噴 UnicodeEncodeError 中斷備份
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── Load .env ─────────────────────────────────────────────────────
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

CLOUDFLARE_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
R2_BACKUP_BUCKET      = os.getenv("R2_BACKUP_BUCKET", "")

DB_HOST         = os.getenv("DB_HOST", "localhost")
DB_PORT         = int(os.getenv("DB_PORT", "3306"))
DB_NAME         = os.getenv("DB_NAME", "local")
DB_USER         = os.getenv("DB_USER", "root")
DB_PASSWORD     = os.getenv("DB_PASSWORD", "root")

STATE_FILE      = Path(__file__).parent / ".backup-state.json"
BACKUP_INTERVAL = 30  # 天

R2_ENDPOINT = f"https://{CLOUDFLARE_ACCOUNT_ID}.r2.cloudflarestorage.com"


# ── State ─────────────────────────────────────────────────────────

def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"last_backup": None, "last_file": None}


def save_state(state):
    STATE_FILE.write_text(
        json.dumps(state, indent=4, ensure_ascii=False),
        encoding="utf-8"
    )


def should_backup(force=False):
    if force:
        return True, "强制備份 (--force)"
    state = load_state()
    last = state.get("last_backup")
    if not last:
        return True, "尚未備份過"
    days = (datetime.date.today() - datetime.date.fromisoformat(last)).days
    if days >= BACKUP_INTERVAL:
        return True, f"距上次備份 {days} 天 (>= {BACKUP_INTERVAL} 天)"
    return False, f"距上次備份 {days} 天 (< {BACKUP_INTERVAL} 天)，略過"


# ── MySQL Dump ────────────────────────────────────────────────────

def escape_value(val):
    """將 Python 值轉成 MySQL INSERT 語法中的字串。"""
    if val is None:
        return "NULL"
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, (bytes, bytearray)):
        return "0x" + val.hex()
    if isinstance(val, (datetime.date, datetime.datetime, datetime.time)):
        return f"'{val}'"
    s = str(val)
    s = s.replace("\\", "\\\\")
    s = s.replace("'",  "\\'")
    s = s.replace("\n", "\\n")
    s = s.replace("\r", "\\r")
    s = s.replace("\0", "\\0")
    return f"'{s}'"


def dump_database():
    """連接 MySQL，傾印所有資料表為 SQL 字串。"""
    import mysql.connector

    conn = mysql.connector.connect(
        host=DB_HOST, port=DB_PORT,
        database=DB_NAME, user=DB_USER, password=DB_PASSWORD,
        charset="utf8mb4"
    )
    cursor = conn.cursor()

    lines = []
    lines.append(f"-- backup-db.py MySQL dump")
    lines.append(f"-- Database : {DB_NAME}")
    lines.append(f"-- Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("--")
    lines.append("SET NAMES utf8mb4;")
    lines.append("SET FOREIGN_KEY_CHECKS=0;")
    lines.append("")

    cursor.execute("SHOW TABLES")
    tables = [row[0] for row in cursor.fetchall()]
    print(f"  DB: {len(tables)} 個資料表")

    for table in tables:
        lines.append(f"-- Table: {table}")
        lines.append(f"DROP TABLE IF EXISTS `{table}`;")

        cursor.execute(f"SHOW CREATE TABLE `{table}`")
        create_stmt = cursor.fetchone()[1]
        lines.append(create_stmt + ";")
        lines.append("")

        cursor.execute(f"SELECT * FROM `{table}`")
        rows = cursor.fetchall()
        if rows:
            for row in rows:
                values = ", ".join(escape_value(v) for v in row)
                lines.append(f"INSERT INTO `{table}` VALUES ({values});")
        lines.append("")

    lines.append("SET FOREIGN_KEY_CHECKS=1;")

    cursor.close()
    conn.close()

    return "\n".join(lines)


# ── R2 Upload ─────────────────────────────────────────────────────

def upload_to_r2(local_path, filename):
    """用 aws s3 cp 上傳到 R2 bucket。"""
    s3_uri = f"s3://{R2_BACKUP_BUCKET}/{filename}"
    cmd = [
        "aws", "s3", "cp",
        str(local_path),
        s3_uri,
        "--endpoint-url", R2_ENDPOINT,
    ]

    env = os.environ.copy()
    # 若 .env 有設定 R2 金鑰，優先使用
    r2_key = os.getenv("R2_ACCESS_KEY_ID", "")
    r2_secret = os.getenv("R2_SECRET_ACCESS_KEY", "")
    if r2_key:
        env["AWS_ACCESS_KEY_ID"]     = r2_key
        env["AWS_SECRET_ACCESS_KEY"] = r2_secret

    result = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if result.returncode != 0:
        raise RuntimeError(f"aws s3 cp failed:\n{result.stderr.strip()}")
    return s3_uri


# ── Main ──────────────────────────────────────────────────────────

def run_backup(force=False):
    ok, reason = should_backup(force)
    if not ok:
        print(f"  DB Backup: {reason}，略過")
        return

    print(f"  DB Backup: {reason}，開始備份...")

    today = datetime.date.today().isoformat()
    filename = f"{DB_NAME}_{today}.sql.gz"
    tmp_path = Path(__file__).parent / filename

    # 1. Dump
    print("  DB Backup: 傾印資料庫...")
    try:
        sql = dump_database()
    except Exception as e:
        print(f"  DB Backup: [失敗] 無法連線 MySQL：{e}")
        sys.exit(1)

    # 2. gzip
    sql_bytes = sql.encode("utf-8")
    with gzip.open(tmp_path, "wb") as f:
        f.write(sql_bytes)
    size_kb = tmp_path.stat().st_size // 1024
    print(f"  DB Backup: 壓縮完成 ({size_kb} KB) -> {filename}")

    # 3. Upload to R2
    print(f"  DB Backup: 上傳到 R2 ({R2_BACKUP_BUCKET})...")
    try:
        s3_uri = upload_to_r2(tmp_path, filename)
    except Exception as e:
        tmp_path.unlink(missing_ok=True)
        print(f"  DB Backup: [失敗] 上傳失敗：{e}")
        print("  DB Backup: 請確認 aws CLI 已設定 R2 存取金鑰")
        sys.exit(1)
    finally:
        tmp_path.unlink(missing_ok=True)  # 刪除本地暫存檔

    # 4. Update state
    save_state({"last_backup": today, "last_file": filename})
    print(f"  DB Backup: [完成] {s3_uri}")


def main():
    force = "--force" in sys.argv
    run_backup(force=force)


if __name__ == "__main__":
    main()
