"""
wp_db.py
Local WP (MySQL) への接続と、記事の読み書き。
接続情報はプロジェクトルートの .env（sync-analytics.py と共通）を利用する。
"""
import os
from pathlib import Path

import pymysql
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR.parent / ".env")

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "10018"))
DB_NAME = os.getenv("DB_NAME", "local")
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "root")
TABLE_PREFIX = os.getenv("DB_TABLE_PREFIX", os.getenv("WP_TABLE_PREFIX", "wp_"))

POSTS_TABLE = f"{TABLE_PREFIX}posts"


def get_conn():
    return pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        charset="utf8mb4",
        autocommit=False,
    )


def list_posts(search: str = ""):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            sql = (
                f"SELECT ID, post_title, post_name, post_status, post_modified "
                f"FROM `{POSTS_TABLE}` "
                f"WHERE post_type='post' AND post_status IN ('publish','draft','pending','future') "
            )
            params = []
            if search:
                sql += "AND (post_title LIKE %s OR post_name LIKE %s) "
                like = f"%{search}%"
                params += [like, like]
            sql += "ORDER BY post_modified DESC LIMIT 300"
            cur.execute(sql, params)
            rows = cur.fetchall()
            return [
                {
                    "id": r[0],
                    "title": r[1] or "(無題)",
                    "slug": r[2],
                    "status": r[3],
                    "modified": str(r[4]),
                }
                for r in rows
            ]
    finally:
        conn.close()


def get_post(post_id: int):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT ID, post_title, post_name, post_content, post_status "
                f"FROM `{POSTS_TABLE}` WHERE ID=%s AND post_type='post'",
                (post_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return {
                "id": row[0],
                "title": row[1],
                "slug": row[2],
                "content": row[3] or "",
                "status": row[4],
            }
    finally:
        conn.close()


def update_post_content(post_id: int, new_content: str):
    update_post(post_id, content=new_content)


def update_post(post_id: int, content: str = None, title: str = None):
    """content と/または title を更新する。どちらか片方だけの指定も可。"""
    sets = []
    params = []
    if content is not None:
        sets.append("post_content=%s")
        params.append(content)
    if title is not None:
        sets.append("post_title=%s")
        params.append(title)
    if not sets:
        return
    sets.append("post_modified=NOW()")
    sets.append("post_modified_gmt=UTC_TIMESTAMP()")

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            sql = f"UPDATE `{POSTS_TABLE}` SET {', '.join(sets)} WHERE ID=%s"
            params.append(post_id)
            cur.execute(sql, params)
        conn.commit()
    finally:
        conn.close()
