"""
app.py
用來 AI 校對テス日文部落格文章的本機（local）網頁工具。
校對引擎使用 Gemini API。

使用方式請參考 README.md：
    python app.py
    瀏覽器開啟 http://localhost:5055
"""
import json
import os
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

import ai_corrector
import html_diff
import wp_db

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR.parent / ".env")

SESS_DIR = BASE_DIR / "sessions"
BACKUP_DIR = BASE_DIR / "backups"
SESS_DIR.mkdir(exist_ok=True)
BACKUP_DIR.mkdir(exist_ok=True)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
BATCH_SIZE = int(os.getenv("CORRECTION_BATCH_SIZE", "12"))

_client = None


def get_client():
    global _client
    if _client is None:
        if not GEMINI_API_KEY:
            return None
        from google import genai

        _client = genai.Client(api_key=GEMINI_API_KEY)
    return _client


app = Flask(__name__)


# ── Session（每篇文章的校對進度）存檔/讀取 ──────────────────────────

def session_path(post_id: int) -> Path:
    return SESS_DIR / f"{post_id}.json"


def load_session(post_id: int):
    p = session_path(post_id)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return None


def save_session(post_id: int, data: dict):
    session_path(post_id).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def session_to_node_records(session: dict):
    """把 session["nodes"]（存檔用的 dict 陣列）轉成 html_diff.build_html 需要的
    格式（protected_original / protected_corrected / shortcode_map / statuses）。
    """
    records = []
    for n in session["nodes"]:
        if n is None:
            records.append(None)
            continue
        records.append(
            {
                "protected_original": n["protected_original"],
                "protected_corrected": n["protected_corrected"],
                "shortcode_map": n["shortcode_map"],
                "statuses": n["statuses"],
            }
        )
    return records


def build_summary(session: dict):
    """統計所有節點的 statuses，組成畫面上方顯示的變更件數摘要。"""
    total = accepted = rejected = 0
    for n in session["nodes"]:
        if not n:
            continue
        for s in n["statuses"]:
            total += 1
            if s == "accepted":
                accepted += 1
            else:
                rejected += 1
    return {"total_changes": total, "accepted": accepted, "rejected": rejected}


def default_title_check(title: str) -> dict:
    """標題 AI 檢查尚未執行時的預設值（先把原標題當作已選定）。"""
    return {"original": title, "grammar_ok": True, "note": "", "suggestions": [], "selected": title}


def build_review_response(session: dict):
    """/prepare、/review 等多個 API 共用，組出前端校對畫面需要的資料
    （diff HTML + 摘要 + 標題建議）。
    """
    node_records = session_to_node_records(session)
    review_html = html_diff.build_html(session["raw_original_content"], node_records, mode="review")
    return {
        "post_id": session["post_id"],
        "title": session["title"],
        "slug": session["slug"],
        "review_html": review_html,
        "summary": build_summary(session),
        "prepared_at": session.get("prepared_at"),
        "applied": session.get("applied", False),
        "title_check": session.get("title_check") or default_title_check(session["title"]),
    }


# ── 路由（Routing） ────────────────────────────────────────────────

@app.route("/")
def index():
    """回傳首頁樣板。之後所有畫面操作都是 index.html 裡的 JS
    呼叫這支檔案的 /api/... 端點來完成。
    """
    return render_template("index.html", has_api_key=bool(GEMINI_API_KEY))


@app.route("/api/posts")
def api_posts():
    """左側側邊欄的文章列表。有搜尋字串 q 就過濾，
    並在每篇文章附上是否有校對中 session（has_session）。
    """
    search = request.args.get("q", "").strip()
    try:
        posts = wp_db.list_posts(search)
    except Exception as e:
        return jsonify({"error": f"資料庫連線失敗：{e}"}), 500
    # 標記哪些文章有正在校對中的草稿
    for p in posts:
        p["has_session"] = session_path(p["id"]).exists()
    return jsonify(posts)


@app.route("/api/posts/<int:post_id>/prepare", methods=["POST"])
def api_prepare(post_id):
    """對應「開始 / 重新 AI 校對」按鈕。
    若 force=False 且已有 session，直接回傳既有結果（不重跑，節省 API 費用）。
    只有 force=True 或尚未執行過時，才會從資料庫抓文章、把 HTML 拆成節點丟給
    Gemini 逐批校正，並把結果存成新的 session。
    """
    client = get_client()
    if client is None:
        return jsonify({"error": "GEMINI_API_KEY 未設定。請在專案根目錄的 .env 中新增 GEMINI_API_KEY。"}), 400

    body = request.get_json(silent=True) or {}
    force = bool(body.get("force"))

    existing = load_session(post_id)
    if existing and not force:
        return jsonify(build_review_response(existing))

    try:
        post = wp_db.get_post(post_id)
    except Exception as e:
        return jsonify({"error": f"資料庫連線失敗：{e}"}), 500
    if not post:
        return jsonify({"error": "找不到這篇文章"}), 404

    raw_content = post["content"]
    soup, dom_nodes = html_diff.extract_nodes(raw_content)

    node_entries = []  # 與 dom_nodes 一一對應（依 index），內容是 None 或 dict
    to_correct = []
    for idx, dom_node in enumerate(dom_nodes):
        original_text = str(dom_node)
        protected, shortcode_map = html_diff.protect_shortcodes(original_text)
        node_entries.append(
            {
                "protected_original": protected,
                "protected_corrected": protected,  # 在 AI 回應回來前，先視為沒有變更
                "shortcode_map": shortcode_map,
                "statuses": [],
            }
        )
        to_correct.append((idx, protected))

    errors = []
    for i in range(0, len(to_correct), BATCH_SIZE):
        chunk = to_correct[i : i + BATCH_SIZE]
        try:
            results = ai_corrector.correct_nodes_batch(client, MODEL, chunk)
        except Exception as e:
            errors.append(str(e))
            continue
        for idx, corrected_protected in results.items():
            rec = node_entries[idx]
            rec["protected_corrected"] = corrected_protected
            spans = html_diff.diff_spans(rec["protected_original"], corrected_protected)
            rec["statuses"] = ["accepted"] * html_diff.count_changes(spans)

    # 讀取文章全文，順便讓 AI 檢查並建議標題
    plain_text = BeautifulSoup(raw_content, "html.parser").get_text("\n", strip=True)
    title_check = default_title_check(post["title"])
    try:
        result = ai_corrector.suggest_title(client, MODEL, post["title"], plain_text)
        title_check.update(
            {
                "grammar_ok": result["grammar_ok"],
                "note": result["note"],
                "suggestions": result["suggestions"],
            }
        )
    except Exception as e:
        errors.append(f"標題建議失敗：{e}")

    session = {
        "post_id": post_id,
        "title": post["title"],
        "slug": post["slug"],
        "raw_original_content": raw_content,
        "nodes": node_entries,
        "title_check": title_check,
        "prepared_at": datetime.now().isoformat(timespec="seconds"),
        "applied": False,
    }
    save_session(post_id, session)

    resp = build_review_response(session)
    if errors:
        resp["warnings"] = [f"部分內容 AI 校正失敗，已保留原文：{e}" for e in errors]
    return jsonify(resp)


@app.route("/api/posts/<int:post_id>/review")
def api_review(post_id):
    """從已存在的 session 讀出目前的校對狀況（diff HTML 等）並回傳。
    不會呼叫 AI，純粹是重新顯示既有 session（例如重新選到同一篇文章時使用）。
    """
    session = load_session(post_id)
    if not session:
        return jsonify({"error": "尚未產生校對建議，請先按「開始 AI 校對」"}), 404
    return jsonify(build_review_response(session))


@app.route("/api/posts/<int:post_id>/status", methods=["POST"])
def api_status(post_id):
    """把某一個變更點（某節點內的其中一個 diff）切換成 accepted/rejected。
    使用者每次逐項核准/拒絕校對結果時就會呼叫這支 API。
    """
    session = load_session(post_id)
    if not session:
        return jsonify({"error": "找不到校對進度"}), 404
    body = request.get_json(force=True)
    node_index = int(body["node_index"])
    change_index = int(body["change_index"])
    status = body["status"]
    if status not in ("accepted", "rejected"):
        return jsonify({"error": "status 必須是 accepted 或 rejected"}), 400

    try:
        node = session["nodes"][node_index]
    except (IndexError, TypeError):
        return jsonify({"error": "node_index 超出範圍"}), 400
    if not node or change_index >= len(node["statuses"]):
        return jsonify({"error": "change_index 超出範圍"}), 400

    node["statuses"][change_index] = status
    save_session(post_id, session)
    return jsonify({"ok": True, "summary": build_summary(session)})


@app.route("/api/posts/<int:post_id>/redo", methods=["POST"])
def api_redo(post_id):
    """「要求重新調整」彈窗送出後呼叫。針對單一節點，帶著使用者輸入的
    instruction（例如「這太書面語了」）再呼叫一次 AI 重新校正該節點，
    覆蓋掉原本的 protected_corrected 與 statuses。
    """
    client = get_client()
    if client is None:
        return jsonify({"error": "GEMINI_API_KEY 未設定"}), 400

    session = load_session(post_id)
    if not session:
        return jsonify({"error": "找不到校對進度"}), 404

    body = request.get_json(force=True)
    node_index = int(body["node_index"])
    instruction = (body.get("instruction") or "").strip()
    if not instruction:
        return jsonify({"error": "請輸入你希望怎麼調整"}), 400

    try:
        node = session["nodes"][node_index]
    except (IndexError, TypeError):
        return jsonify({"error": "node_index 超出範圍"}), 400
    if not node:
        return jsonify({"error": "這個段落沒有校正紀錄"}), 400

    try:
        new_corrected = ai_corrector.correct_node_single(
            client, MODEL, node["protected_original"], instruction, node["protected_corrected"]
        )
    except Exception as e:
        return jsonify({"error": f"呼叫 AI 失敗：{e}"}), 500

    node["protected_corrected"] = new_corrected
    spans = html_diff.diff_spans(node["protected_original"], new_corrected)
    node["statuses"] = ["accepted"] * html_diff.count_changes(spans)
    save_session(post_id, session)

    fragment = html_diff.render_node(
        node_index,
        node["protected_original"],
        node["protected_corrected"],
        node["statuses"],
        "review",
        node["shortcode_map"],
    )
    return jsonify({"ok": True, "fragment_html": fragment, "summary": build_summary(session)})


@app.route("/api/posts/<int:post_id>/title/select", methods=["POST"])
def api_title_select(post_id):
    """使用者從 AI 建議的標題清單中選定一個（或自行輸入），
    只更新 session 裡的 title_check.selected，還沒真的寫回資料庫。
    """
    session = load_session(post_id)
    if not session:
        return jsonify({"error": "找不到校對進度"}), 404

    body = request.get_json(force=True)
    chosen = (body.get("title") or "").strip()
    if not chosen:
        return jsonify({"error": "標題不可為空"}), 400

    title_check = session.get("title_check") or default_title_check(session["title"])
    title_check["selected"] = chosen
    session["title_check"] = title_check
    save_session(post_id, session)
    return jsonify({"ok": True, "selected": chosen})


@app.route("/api/posts/<int:post_id>/apply", methods=["POST"])
def api_apply(post_id):
    """「套用並覆蓋原文」按鈕：把 session 中所有 accepted 的變更套用到內文，
    正式寫回 WordPress 資料庫。寫入前會先備份原文，且會檢查文章/標題
    是否在校對期間被人在 WP 後台手動改過，避免不小心覆蓋掉他人的修改。
    """
    session = load_session(post_id)
    if not session:
        return jsonify({"error": "找不到校對進度"}), 404

    try:
        current_post = wp_db.get_post(post_id)
    except Exception as e:
        return jsonify({"error": f"資料庫連線失敗：{e}"}), 500
    if not current_post:
        return jsonify({"error": "找不到這篇文章（可能已被刪除）"}), 404

    # 若目前資料庫內容跟開始校對時不同，代表有人在 WP 後台改過文章，拒絕覆蓋
    if current_post["content"] != session["raw_original_content"]:
        return jsonify(
            {
                "error": "偵測到這篇文章在 WordPress 後台被修改過（與開始校對時不同），"
                "為避免覆蓋掉你新的手動修改，請按「重新校對」後再套用。"
            }
        ), 409

    title_check = session.get("title_check") or default_title_check(session["title"])
    final_title = title_check.get("selected") or session["title"]

    # 若標題有變更，且資料庫內標題也跟開始校對時不同，同樣視為被人動過，拒絕覆蓋
    if final_title != session["title"] and current_post["title"] != session["title"]:
        return jsonify(
            {
                "error": "偵測到這篇文章的標題在 WordPress 後台被修改過，"
                "為避免覆蓋掉你新的手動修改，請按「重新校對」後再套用。"
            }
        ), 409

    node_records = session_to_node_records(session)
    final_content = html_diff.build_html(session["raw_original_content"], node_records, mode="final")

    # 覆蓋前先備份原文（標題+內文）
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_file = BACKUP_DIR / f"{post_id}-{session.get('slug') or 'post'}-{timestamp}.json"
    backup_file.write_text(
        json.dumps(
            {"title": session["title"], "content": session["raw_original_content"]},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    try:
        wp_db.update_post(post_id, content=final_content, title=final_title if final_title != session["title"] else None)
    except Exception as e:
        return jsonify({"error": f"寫入資料庫失敗：{e}"}), 500

    session["applied"] = True
    session["applied_at"] = datetime.now().isoformat(timespec="seconds")
    session["backup_file"] = str(backup_file)
    save_session(post_id, session)

    return jsonify({"ok": True, "backup_file": str(backup_file), "final_title": final_title})


@app.route("/api/posts/<int:post_id>/session", methods=["DELETE"])
def api_delete_session(post_id):
    """刪掉這篇文章的校對進度 session 檔，讓使用者可以從頭重新開始校對。"""
    p = session_path(post_id)
    if p.exists():
        p.unlink()
    return jsonify({"ok": True})


if __name__ == "__main__":
    if not GEMINI_API_KEY:
        print("⚠️  GEMINI_API_KEY 未設定，請在專案根目錄 .env 加入這一行：")
        print("    GEMINI_API_KEY=AIzaSy........")
    app.run(host="127.0.0.1", port=5055, debug=True)
