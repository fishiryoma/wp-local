"""
app.py
用來校對／查核テス日文部落格文章的本機（local）網頁工具。

有兩種獨立的「種類」(kind)，各自有自己的 session 檔案、備份、審核畫面，不會互相覆蓋：
    - grammar：日文文法校對，引擎是 Gemini API（呼叫 ai_corrector.py）。
    - factcheck：事實查核／改寫，這支程式本身不呼叫任何 AI，只負責把文章內容匯出成
      檔案，實際的查證與改寫是在 Cowork 對話裡由 Claude 直接讀取匯出檔、上網查證後，
      把結果寫回 sessions/factcheck/{post_id}.json（跟這支程式共用同一套審核/套用/備份邏輯）。

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

KINDS = {"grammar", "factcheck"}

SESS_DIR = BASE_DIR / "sessions"
GRAMMAR_SESS_DIR = SESS_DIR / "grammar"
FACTCHECK_SESS_DIR = SESS_DIR / "factcheck"
FACTCHECK_EXPORT_DIR = BASE_DIR / "factcheck_exports"
BACKUP_DIR = BASE_DIR / "backups"
GRAMMAR_SESS_DIR.mkdir(parents=True, exist_ok=True)
FACTCHECK_SESS_DIR.mkdir(parents=True, exist_ok=True)
FACTCHECK_EXPORT_DIR.mkdir(exist_ok=True)
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


def _migrate_legacy_sessions():
    """舊版本把校對 session 直接存在 sessions/{id}.json，這裡搬進 sessions/grammar/。
    只搬「檔名是數字」的檔案，且目的地還不存在時才搬，避免不小心蓋掉或動到不相關的東西。
    """
    if not SESS_DIR.exists():
        return
    for p in SESS_DIR.glob("*.json"):
        if not p.is_file() or not p.stem.isdigit():
            continue
        target = GRAMMAR_SESS_DIR / p.name
        if not target.exists():
            p.rename(target)


_migrate_legacy_sessions()

app = Flask(__name__)


# ── Session（每篇文章、每種 kind 的校對/查核進度）存檔/讀取 ──────────

def sess_dir_for(kind: str) -> Path:
    return GRAMMAR_SESS_DIR if kind == "grammar" else FACTCHECK_SESS_DIR


def session_path(kind: str, post_id: int) -> Path:
    return sess_dir_for(kind) / f"{post_id}.json"


def load_session(kind: str, post_id: int):
    p = session_path(kind, post_id)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return None


def save_session(kind: str, post_id: int, data: dict):
    data["kind"] = kind
    session_path(kind, post_id).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def session_badge(kind: str, post_id: int):
    """給文章清單用的簡易狀態：None＝沒有草稿、"pending"＝審核中、"applied"＝已套用。"""
    session = load_session(kind, post_id)
    if not session:
        return None
    return "applied" if session.get("applied") else "pending"


def session_to_node_records(session: dict):
    """把 session["nodes"]（存檔用的 dict 陣列）轉成 html_diff.build_html 需要的
    格式（protected_original / protected_corrected / shortcode_map / statuses / sources / note）。
    sources、note 是事實查核專用的查證來源／筆記，文法校對的節點沒有這兩個欄位也沒關係。
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
                "sources": n.get("sources"),
                "note": n.get("note"),
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


def staleness_info(session: dict):
    """比對資料庫目前內容跟 session 存的 baseline 是否一致，純比對不做任何修改。
    不同的話代表這段期間文章被改過（WP 後台手動編輯，或另一種 kind 已經套用過），
    只回傳警告文字讓畫面顯示，不會自動重新產生、也不會動到使用者已核准/拒絕的紀錄。
    已經套用過的 session 不需要再比（套用當下內容本來就會變，比較沒有意義）。
    """
    if session.get("applied"):
        return {"stale": False, "reason": ""}
    try:
        current = wp_db.get_post(session["post_id"])
    except Exception:
        return None  # 資料庫連不上，交給呼叫端的其他錯誤處理，這裡不擋
    if not current:
        return {"stale": True, "reason": "這篇文章在 WordPress 裡已經找不到了（可能已被刪除）。"}
    if current["content"] != session["raw_original_content"] or current["title"] != session["title"]:
        return {
            "stale": True,
            "reason": "這篇文章自從產生這份草稿後已經被修改過（可能是在 WP 後台手動編輯，"
            "或另一種校對／查核已經套用過），下面顯示的仍是產生當下的比對結果，建議重新產生。",
        }
    return {"stale": False, "reason": ""}


def build_review_response(kind: str, session: dict):
    """/prepare、/review 等多個 API 共用，組出前端審核畫面需要的資料
    （diff HTML + 摘要 + 標題建議 + 是否已過期）。
    """
    node_records = session_to_node_records(session)
    review_html = html_diff.build_html(session["raw_original_content"], node_records, mode="review", kind=kind)
    resp = {
        "post_id": session["post_id"],
        "kind": kind,
        "title": session["title"],
        "slug": session["slug"],
        "review_html": review_html,
        "summary": build_summary(session),
        "prepared_at": session.get("prepared_at"),
        "applied": session.get("applied", False),
    }
    # 標題 AI 檢查只有 grammar 有（呼叫 Gemini 產生建議）。事實查核不檢查標題，
    # 畫面上也不需要顯示這塊，所以 factcheck 完全不回傳 title_check。
    if kind == "grammar":
        resp["title_check"] = session.get("title_check") or default_title_check(session["title"])
    stale = staleness_info(session)
    if stale:
        resp["stale"] = stale["stale"]
        resp["stale_reason"] = stale["reason"]
    return resp


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
    並在每篇文章附上兩種 kind 各自的草稿狀態（grammar_session / factcheck_session）。
    """
    search = request.args.get("q", "").strip()
    try:
        posts = wp_db.list_posts(search)
    except Exception as e:
        return jsonify({"error": f"資料庫連線失敗：{e}"}), 500
    for p in posts:
        p["grammar_session"] = session_badge("grammar", p["id"])
        p["factcheck_session"] = session_badge("factcheck", p["id"])
    return jsonify(posts)


@app.route("/api/sessions")
def api_sessions():
    """「Session 管理」分頁用：把 grammar/factcheck 兩個資料夾裡所有現存的
    session 攤平列出來，方便一次看到全部累積的草稿並清除不需要的。
    """
    items = []
    for kind in KINDS:
        for p in sorted(sess_dir_for(kind).glob("*.json")):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            note_count = sum(1 for n in (data.get("nodes") or []) if n and n.get("note"))
            # post_id 一定要用「檔名」而不是檔案內容裡存的 post_id 欄位——
            # 早期測試留下的幾份草稿（1.json、2.json...）內容裡的 post_id 是 0，
            # 跟檔名對不上，如果照內容的 post_id 組刪除網址會刪到不存在的檔案，
            # 畫面上看起來就像「按了刪除但沒反應」。檔名才是 session_path() 真正拿來
            # 找檔案的依據，這裡也要跟著用檔名，才能保證「畫面上顯示的這一列」跟
            # 「按刪除會刪到的檔案」是同一份。
            items.append(
                {
                    "post_id": p.stem,
                    "kind": kind,
                    "title": data.get("title") or f"（沒有標題 / 檔案 {p.name}）",
                    "prepared_at": data.get("prepared_at"),
                    "applied": data.get("applied", False),
                    "applied_at": data.get("applied_at"),
                    "summary": build_summary(data),
                    "note_count": note_count,
                }
            )
    items.sort(key=lambda x: x.get("prepared_at") or "", reverse=True)
    return jsonify(items)


@app.route("/api/sessions/<kind>/<int:post_id>", methods=["DELETE"])
def api_delete_session(post_id, kind):
    """「Session 管理」分頁的刪除鈕：只刪除本機的審核草稿檔案，
    不會動到 WordPress 資料庫裡的文章本身。
    """
    if kind not in KINDS:
        return jsonify({"error": f"不支援的種類：{kind}"}), 400
    p = session_path(kind, post_id)
    if p.exists():
        p.unlink()
    return jsonify({"ok": True})


@app.route("/api/posts/<int:post_id>/<kind>/prepare", methods=["POST"])
def api_prepare(post_id, kind):
    """對應「開始 / 重新」按鈕。grammar 跟 factcheck 的行為完全不同：
        - grammar：跟以前一樣，從資料庫抓文章、丟給 Gemini 逐批校正，存成新 session。
        - factcheck：不呼叫任何 AI，只把目前的文章內容匯出成檔案，
          回傳訊息請你到 Cowork 對話請 Claude 查核，查完的結果由 Claude 直接寫進 session。
    """
    if kind not in KINDS:
        return jsonify({"error": f"不支援的種類：{kind}"}), 400

    try:
        post = wp_db.get_post(post_id)
    except Exception as e:
        return jsonify({"error": f"資料庫連線失敗：{e}"}), 500
    if not post:
        return jsonify({"error": "找不到這篇文章"}), 404

    if kind == "factcheck":
        return _prepare_factcheck(post_id, post)

    body = request.get_json(silent=True) or {}
    force = bool(body.get("force"))

    existing = load_session("grammar", post_id)
    if existing and not force:
        return jsonify(build_review_response("grammar", existing))

    return _prepare_grammar(post_id, post)


def _prepare_grammar(post_id, post):
    client = get_client()
    if client is None:
        return jsonify({"error": "GEMINI_API_KEY 未設定。請在專案根目錄的 .env 中新增 GEMINI_API_KEY。"}), 400

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
    save_session("grammar", post_id, session)

    resp = build_review_response("grammar", session)
    if errors:
        resp["warnings"] = [f"部分內容 AI 校正失敗，已保留原文：{e}" for e in errors]
    return jsonify(resp)


def _prepare_factcheck(post_id, post):
    raw_content = post["content"]
    soup, dom_nodes = html_diff.extract_nodes(raw_content)

    nodes_export = []
    for idx, dom_node in enumerate(dom_nodes):
        original_text = str(dom_node)
        protected, shortcode_map = html_diff.protect_shortcodes(original_text)
        nodes_export.append({"index": idx, "text": protected, "shortcode_map": shortcode_map})

    plain_text = BeautifulSoup(raw_content, "html.parser").get_text("\n", strip=True)

    export = {
        "post_id": post_id,
        "title": post["title"],
        "slug": post["slug"],
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "raw_original_content": raw_content,
        "plain_text": plain_text,
        "nodes": nodes_export,
    }
    export_path = FACTCHECK_EXPORT_DIR / f"{post_id}.json"
    export_path.write_text(json.dumps(export, ensure_ascii=False, indent=2), encoding="utf-8")

    existing = load_session("factcheck", post_id)
    resp = {
        "post_id": post_id,
        "title": post["title"],
        "exported": True,
        "export_file": str(export_path),
        "message": f"已把文章內容匯出到 factcheck_exports/{post_id}.json，還沒有查核結果。",
        "has_existing_session": bool(existing),
        "existing_prepared_at": existing.get("prepared_at") if existing else None,
    }
    return jsonify(resp)


@app.route("/api/posts/<int:post_id>/<kind>/review")
def api_review(post_id, kind):
    """從已存在的 session 讀出目前的審核狀況（diff HTML 等）並回傳。
    不會呼叫 AI，純粹是重新顯示既有 session（例如重新選到同一篇文章時使用）。
    """
    if kind not in KINDS:
        return jsonify({"error": f"不支援的種類：{kind}"}), 400
    session = load_session(kind, post_id)
    if not session:
        if kind == "grammar":
            return jsonify({"error": "尚未產生校對建議，請先按「開始 AI 校對」"}), 404
        return jsonify({"error": "這篇文章還沒有查核結果，請先按「開始查核」匯出內容，再到 Cowork 對話請 Claude 查核。"}), 404
    return jsonify(build_review_response(kind, session))


@app.route("/api/posts/<int:post_id>/<kind>/status", methods=["POST"])
def api_status(post_id, kind):
    """把某一個變更點（某節點內的其中一個 diff）切換成 accepted/rejected。
    只有 grammar 支援：事實查核是筆記功能，沒有採用/維持原文的概念，畫面上也不會有按鈕
    可以觸發這支 API，這裡擋一次是保險，避免直接打 API 造成困惑的行為。
    """
    if kind not in KINDS:
        return jsonify({"error": f"不支援的種類：{kind}"}), 400
    if kind != "grammar":
        return jsonify({"error": "事實查核沒有採用/維持原文的功能，直接看筆記自己判斷就好。"}), 400
    session = load_session(kind, post_id)
    if not session:
        return jsonify({"error": "找不到審核進度"}), 404
    if session.get("applied"):
        return jsonify({"error": "這份草稿已經套用覆蓋原文了，內容已經寫進 WordPress，不能再修改逐點的採用/維持原文。如需再調整，請重新產生一份新的校對。"}), 400
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
    save_session(kind, post_id, session)
    return jsonify({"ok": True, "summary": build_summary(session)})


@app.route("/api/posts/<int:post_id>/<kind>/redo", methods=["POST"])
def api_redo(post_id, kind):
    """「要求重新調整」彈窗送出後呼叫。目前只有 grammar 支援：針對單一節點，
    帶著使用者輸入的 instruction 再呼叫一次 Gemini 重新校正該節點。
    factcheck 沒有 in-app 的 AI 可以即時重跑，畫面上也不會顯示這個按鈕，
    這裡仍擋一次是為了保險（避免直接打 API 造成困惑的錯誤訊息）。
    """
    if kind not in KINDS:
        return jsonify({"error": f"不支援的種類：{kind}"}), 400
    if kind != "grammar":
        return jsonify({"error": "事實查核不支援「重新調整」，請回到 Cowork 對話請 Claude 針對這段再調整一次。"}), 400

    client = get_client()
    if client is None:
        return jsonify({"error": "GEMINI_API_KEY 未設定"}), 400

    session = load_session("grammar", post_id)
    if not session:
        return jsonify({"error": "找不到校對進度"}), 404
    if session.get("applied"):
        return jsonify({"error": "這份草稿已經套用覆蓋原文了，不能再要求重新調整。如需再調整，請重新產生一份新的校對。"}), 400

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
    save_session("grammar", post_id, session)

    fragment = html_diff.render_node(
        node_index,
        node["protected_original"],
        node["protected_corrected"],
        node["statuses"],
        "review",
        node["shortcode_map"],
        kind="grammar",
    )
    return jsonify({"ok": True, "fragment_html": fragment, "summary": build_summary(session)})


@app.route("/api/posts/<int:post_id>/<kind>/title/select", methods=["POST"])
def api_title_select(post_id, kind):
    """使用者從 AI 建議的標題清單中選定一個（或自行輸入），
    只更新 session 裡的 title_check.selected，還沒真的寫回資料庫。
    """
    if kind not in KINDS:
        return jsonify({"error": f"不支援的種類：{kind}"}), 400
    if kind != "grammar":
        return jsonify({"error": "事實查核不檢查標題，沒有這個功能。"}), 400
    session = load_session(kind, post_id)
    if not session:
        return jsonify({"error": "找不到審核進度"}), 404

    body = request.get_json(force=True)
    chosen = (body.get("title") or "").strip()
    if not chosen:
        return jsonify({"error": "標題不可為空"}), 400

    title_check = session.get("title_check") or default_title_check(session["title"])
    title_check["selected"] = chosen
    session["title_check"] = title_check
    save_session(kind, post_id, session)
    return jsonify({"ok": True, "selected": chosen})


@app.route("/api/posts/<int:post_id>/<kind>/apply", methods=["POST"])
def api_apply(post_id, kind):
    """「套用並覆蓋原文」按鈕：把 session 中所有 accepted 的變更套用到內文，
    正式寫回 WordPress 資料庫。寫入前會先備份原文，且會檢查文章/標題
    是否在校對期間被人在 WP 後台手動改過。

    只有 grammar 支援。事實查核是筆記功能，不寫回 WordPress——查完的結果只給人看，
    要不要動筆、怎麼改寫由テスさん自己決定並手動編輯，畫面上也沒有這顆按鈕。
    """
    if kind not in KINDS:
        return jsonify({"error": f"不支援的種類：{kind}"}), 400
    if kind != "grammar":
        return jsonify({"error": "事實查核沒有套用功能，這是筆記功能，請自己看筆記手動改寫文章。"}), 400
    session = load_session(kind, post_id)
    if not session:
        return jsonify({"error": "找不到審核進度"}), 404

    try:
        current_post = wp_db.get_post(post_id)
    except Exception as e:
        return jsonify({"error": f"資料庫連線失敗：{e}"}), 500
    if not current_post:
        return jsonify({"error": "找不到這篇文章（可能已被刪除）"}), 404

    # 若目前資料庫內容跟開始校對/查核時不同，代表有人在 WP 後台改過文章，
    # 或另一種 kind 已經先套用過，為避免互相覆蓋，拒絕套用
    if current_post["content"] != session["raw_original_content"]:
        return jsonify(
            {
                "error": "偵測到這篇文章的內容已經被修改過（可能是 WP 後台手動編輯，"
                "或另一種校對／查核已經先套用過），為避免覆蓋掉新的修改，請重新產生這份草稿後再套用。"
            }
        ), 409

    title_check = session.get("title_check") or default_title_check(session["title"])
    final_title = title_check.get("selected") or session["title"]

    # 若標題有變更，且資料庫內標題也跟開始校對時不同，同樣視為被人動過，拒絕覆蓋
    if final_title != session["title"] and current_post["title"] != session["title"]:
        return jsonify(
            {
                "error": "偵測到這篇文章的標題已經被修改過，為避免覆蓋掉新的修改，"
                "請重新產生這份草稿後再套用。"
            }
        ), 409

    node_records = session_to_node_records(session)
    final_content = html_diff.build_html(session["raw_original_content"], node_records, mode="final", kind=kind)

    # 覆蓋前先備份原文（標題+內文），檔名帶上 kind 以區分是哪種校對/查核的備份
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_file = BACKUP_DIR / f"{post_id}-{session.get('slug') or 'post'}-{kind}-{timestamp}.json"
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
    save_session(kind, post_id, session)

    return jsonify({"ok": True, "backup_file": str(backup_file), "final_title": final_title})


if __name__ == "__main__":
    if not GEMINI_API_KEY:
        print("⚠️  GEMINI_API_KEY 未設定，日文校對功能無法使用。請在專案根目錄 .env 加入這一行：")
        print("    GEMINI_API_KEY=AIzaSy........")
        print("    （事實查核不需要這組 Key，靠 Cowork 對話裡的 Claude 直接處理）")
    app.run(host="127.0.0.1", port=5055, debug=True)
