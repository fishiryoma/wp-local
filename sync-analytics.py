"""
sync-analytics.py
Cloudflare KV Analytics -> WordPress MySQL (wp_cocoon_accesses)

Usage:
    python sync-analytics.py              # 預設：從 KV 讀取，寫入 MySQL
    python sync-analytics.py --backfill   # 回填：從 CF GraphQL API 抓最近 8 天，寫入 KV

Requirements:
    pip install requests python-dotenv mysql-connector-python --break-system-packages
"""

import sys
import json
import datetime
import os
import re
import requests
from pathlib import Path

# ── Load .env ─────────────────────────────────────────────────────
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

ANALYTICS_TOKEN = os.getenv("ANALYTICS_TOKEN", "")
CF_ZONE_ID      = os.getenv("CF_ZONE_ID", "")
CF_ACCOUNT_ID   = os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
KV_NAMESPACE_ID = os.getenv("KV_NAMESPACE_ID", "")

DB_HOST         = os.getenv("DB_HOST", "localhost")
DB_PORT         = int(os.getenv("DB_PORT", "3306"))
DB_NAME         = os.getenv("DB_NAME", "local")
DB_USER         = os.getenv("DB_USER", "root")
DB_PASSWORD     = os.getenv("DB_PASSWORD", "root")
DB_TABLE_PREFIX = os.getenv("DB_TABLE_PREFIX", "wp_")

CF_GRAPHQL  = "https://api.cloudflare.com/client/v4/graphql"
KV_BASE_URL = (
    f"https://api.cloudflare.com/client/v4/accounts"
    f"/{CF_ACCOUNT_ID}/storage/kv/namespaces/{KV_NAMESPACE_ID}/values"
)
KV_KEY      = "analytics-cache"
STATE_FILE  = Path(__file__).parent / ".analytics-sync.json"

SKIP_EXTENSIONS = {
    ".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".webp",
    ".svg", ".ico", ".woff", ".woff2", ".ttf", ".otf",
    ".map", ".xml", ".json", ".txt", ".php", ".zip",
}
SKIP_PREFIXES = ["/wp-", "/feed/", "/xmlrpc", "/sitemap", "/?", "/page/", "/wp-json/"]


# ── State ─────────────────────────────────────────────────────────

def load_state():
    if STATE_FILE.exists():
        try:
            s = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            return {"last_applied": s.get("last_applied")}
        except Exception:
            pass
    return {"last_applied": None}

def save_state(state):
    STATE_FILE.write_text(
        json.dumps(state, indent=4, ensure_ascii=False),
        encoding="utf-8"
    )


# ── KV REST API ───────────────────────────────────────────────────

def fetch_from_kv():
    """從 Cloudflare KV 取得 analytics-cache。"""
    url = f"{KV_BASE_URL}/{KV_KEY}"
    headers = {"Authorization": f"Bearer {ANALYTICS_TOKEN}"}
    r = requests.get(url, headers=headers, timeout=30)
    if r.status_code == 404:
        print("  Analytics KV: 尚無資料")
        return None
    r.raise_for_status()
    return r.json()

def write_to_kv(data):
    """把 analytics-cache 寫入 Cloudflare KV。"""
    url = f"{KV_BASE_URL}/{KV_KEY}"
    headers = {
        "Authorization": f"Bearer {ANALYTICS_TOKEN}",
        "Content-Type": "application/json",
    }
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    r = requests.put(url, headers=headers, data=body, timeout=30)
    r.raise_for_status()


# ── MySQL ─────────────────────────────────────────────────────────

def get_db_connection():
    import mysql.connector
    return mysql.connector.connect(
        host=DB_HOST, port=DB_PORT,
        database=DB_NAME, user=DB_USER, password=DB_PASSWORD,
        charset="utf8mb4"
    )

def apply_kv_data(kv_data):
    """把 KV 的資料套用到 wp_cocoon_accesses。"""
    state      = load_state()
    last_applied = state.get("last_applied")
    daily_data = kv_data.get("data", {})

    dates_to_apply = sorted(
        d for d in daily_data
        if last_applied is None or d > last_applied
    )

    if not dates_to_apply:
        print("  Analytics: 沒有新資料，略過")
        return

    print(f"  Analytics: 套用 {len(dates_to_apply)} 天的資料 "
          f"({dates_to_apply[0]} ~ {dates_to_apply[-1]})")

    conn   = get_db_connection()
    cursor = conn.cursor()
    table        = f"{DB_TABLE_PREFIX}cocoon_accesses"
    posts_table  = f"{DB_TABLE_PREFIX}posts"

    applied = 0
    skipped = 0

    for date in dates_to_apply:
        paths = daily_data[date]
        for path, count in paths.items():
            # 從路徑取得 slug（最後一段）
            slug = path.strip("/").split("/")[-1]
            if not slug:
                skipped += 1
                continue

            # 用 slug 查詢 post_id（限定真正的文章/頁面，排除 nav_menu_item、oembed_cache 等內部物件類型）
            cursor.execute(
                f"SELECT ID, post_type FROM `{posts_table}` "
                f"WHERE post_name = %s AND post_status = 'publish' "
                f"AND post_type IN ('post', 'page') LIMIT 1",
                (slug,)
            )
            row = cursor.fetchone()
            if not row:
                skipped += 1
                continue

            post_id, post_type = row

            # 確認是否已有既存紀錄
            cursor.execute(
                f"SELECT id FROM `{table}` "
                f"WHERE post_id = %s AND date = %s LIMIT 1",
                (post_id, date)
            )
            existing = cursor.fetchone()

            if existing:
                cursor.execute(
                    f"UPDATE `{table}` SET count = %s WHERE id = %s",
                    (count, existing[0])
                )
            else:
                cursor.execute(
                    f"INSERT INTO `{table}` "
                    f"(post_id, post_type, date, count, last_ip) "
                    f"VALUES (%s, %s, %s, %s, %s)",
                    (post_id, post_type, date, count, "0.0.0.0")
                )
            applied += 1

    conn.commit()
    cursor.close()
    conn.close()

    state["last_applied"] = dates_to_apply[-1]
    save_state(state)
    print(f"  Analytics: [完成] 套用 {applied} 筆 / 略過 {skipped} 筆"
          f"，last_applied = {dates_to_apply[-1]}")


# ── Backfill (CF GraphQL -> KV) ───────────────────────────────────

def is_page_path(path):
    m = re.search(r'(\.[^/?#]+)(\?|#|$)', path)
    if m and m.group(1).lower() in SKIP_EXTENSIONS:
        return False
    return not any(path.startswith(p) for p in SKIP_PREFIXES)

def fetch_cdn_one_day(date):
    """取得單一天的 CDN 存取資料。若已超過保留期限則回傳 None。"""
    query = """{
  viewer {
    zones(filter: {zoneTag: "%s"}) {
      httpRequestsAdaptiveGroups(
        filter: {
          date_geq: "%s", date_leq: "%s",
          requestSource: "eyeball",
          clientRequestHTTPMethodName: "GET"
        },
        limit: 10000,
        orderBy: [date_ASC]
      ) {
        dimensions { date clientRequestPath }
        sum { visits }
      }
    }
  }
}""" % (CF_ZONE_ID, date, date)

    headers = {
        "Authorization": f"Bearer {ANALYTICS_TOKEN}",
        "Content-Type": "application/json",
    }
    r = requests.post(CF_GRAPHQL, json={"query": query}, headers=headers, timeout=30)
    r.raise_for_status()
    data = r.json()

    errors = data.get("errors", [])
    if errors:
        msg = errors[0].get("message", "")
        if "older than" in msg or "wider than" in msg:
            return None
        raise Exception(f"GraphQL error: {msg}")

    records = (
        data.get("data", {})
            .get("viewer", {})
            .get("zones", [{}])[0]
            .get("httpRequestsAdaptiveGroups", [])
    )
    result = {}
    for rec in records:
        path = rec["dimensions"]["clientRequestPath"]
        if is_page_path(path):
            result[path] = rec["sum"]["visits"]
    return result

def backfill(days=8):
    """從 CF GraphQL API 取得最近 N 天的資料並寫入 KV。"""
    print(f"  Backfill: 正在取得最近 {days} 天的資料...")

    existing = fetch_from_kv() or {"last_updated": None, "data": {}}

    today      = datetime.date.today()
    fetched    = 0

    for i in range(1, days + 1):
        date = str(today - datetime.timedelta(days=i))
        print(f"  {date}...", end=" ", flush=True)
        try:
            day_data = fetch_cdn_one_day(date)
            if day_data is None:
                print("已超過保留期限，略過")
                continue
            existing["data"][date] = day_data
            existing["last_updated"] = date
            fetched += 1
            print(f"取得 {len(day_data)} 個路徑")
        except Exception as e:
            print(f"失敗：{e}")

    # 刪除超過 90 天的舊資料
    cutoff = str(today - datetime.timedelta(days=90))
    existing["data"] = {d: v for d, v in existing["data"].items() if d >= cutoff}

    write_to_kv(existing)
    print(f"  Backfill: [完成] 已寫入 {fetched} 天資料到 KV")


# ── Main ──────────────────────────────────────────────────────────

def main():
    if "--backfill" in sys.argv:
        backfill(days=8)
        return

    print("  Analytics: 正在從 KV 取得資料...")
    try:
        kv_data = fetch_from_kv()
    except Exception as e:
        print(f"  Analytics: KV 取得失敗：{e}")
        sys.exit(1)

    if not kv_data:
        print("  Analytics: KV 尚無資料，略過")
        return

    days_in_kv = len(kv_data.get("data", {}))
    print(f"  Analytics: KV 中有 {days_in_kv} 天資料，最新：{kv_data.get('last_updated')}")

    try:
        apply_kv_data(kv_data)
    except Exception as e:
        print(f"  Analytics: MySQL 寫入失敗：{e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
