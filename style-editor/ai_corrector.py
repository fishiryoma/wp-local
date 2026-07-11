"""
ai_corrector.py
Gemini API を呼び出して、日本語テキストノードを「テスさんのスタイルガイド」に沿って校正する。
記事タイトルの文法チェック・改善案の提案もここで行う。
"""
import json
import re
from pathlib import Path

from google.genai import types

BASE_DIR = Path(__file__).parent
STYLE_PROFILE_PATH = BASE_DIR / "style_profile.md"

NODE_OPEN = "⟦N{i}⟧"   # ⟦N0⟧
NODE_CLOSE = "⟦/N{i}⟧"  # ⟦/N0⟧


def load_style_profile() -> str:
    if STYLE_PROFILE_PATH.exists():
        return STYLE_PROFILE_PATH.read_text(encoding="utf-8")
    return "(スタイルガイド未設定。誤字脱字と明らかな文法ミスのみ直してください。)"


def build_system_prompt() -> str:
    style = load_style_profile()
    return f"""あなたは台湾人ブロガー「テス」さんの日本語ブログ記事を校正するアシスタントです。
以下はテスさんの文体プロファイルです。必ず読んでから作業してください。

-----BEGIN STYLE PROFILE-----
{style}
-----END STYLE PROFILE-----

# 作業ルール

1. 渡されたテキストは WordPress 記事の一部（1つまたは複数のブロック）です。
   各ブロックは `⟦N数字⟧` と `⟦/N数字⟧` で囲まれています。
2. 各ブロックについて、スタイルプロファイルの「直してほしいこと」に該当する誤り
   （誤字脱字・活用ミス・助詞ミス・漢字とひらがなの使い分け・中国語直訳っぽい不自然な語順など）を修正してください。
   語順がおかしい場合は、単語のレベルは変えずに、大胆に語順・文の組み立てを自然な日本語に直してください。
3. スタイルプロファイルの「絶対に直さない・触らないこと」に該当するものは、
   一文字も変更しないでください。修正不要なブロックは、原文をそのまま返してください。
4. `⟦SC数字⟧` の形のトークンは、隠されたショートコード（元は `[...]` の記号列）です。
   このトークンの文字・数字・記号は絶対に変更・削除・追加せず、そのままの位置関係を保ってください。
   （前後の日本語だけを校正の対象にしてください）
5. 出力は、入力と同じ数のブロックを、同じ `⟦N数字⟧`〜`⟦/N数字⟧` の形式で、
   同じ順番で返してください。説明・前置き・Markdown装飾は一切不要です。
   ブロックの中身だけを、修正後（または変更なしならそのまま）のテキストにしてください。
6. 直すべきか迷ったら、直す方を選んでください（見逃すより多めに提案する方針）。
   ただし、単語をより難しい・書き言葉的なものに言い換えるのは禁止です。あくまで簡単な語彙のまま、
   間違いや不自然さだけを直してください。
"""


def build_title_system_prompt() -> str:
    style = load_style_profile()
    return f"""あなたは台湾人ブロガー「テス」さんのブログ記事タイトルをチェックするアシスタントです。
以下はテスさんの文体・タイトルのスタイルプロファイルです。必ず読んでから作業してください。

-----BEGIN STYLE PROFILE-----
{style}
-----END STYLE PROFILE-----

# 作業内容

記事の現在のタイトルと本文全体を渡します。以下を行ってください。

1. タイトルの日本語文法に明らかな間違い（誤字脱字、意味が通じない助詞ミスなど）がないか確認する。
   スタイルプロファイルの「タイトルのスタイル」に書かれているような、テスさんらしい少し大げさ・
   キャッチーな言い回しは間違いではないので指摘しない。
2. 本文の内容ともっと合っている、あるいはもっとテスさんらしいタイトル案があるかを考える。
3. 出力は必ず次の JSON 形式のみ（説明文やMarkdownは一切付けない）：

{{
  "grammar_ok": true または false,
  "note": "タイトルの文法についての短いコメント（問題なければ「文法上の問題はありません」等）",
  "suggestions": ["案1", "案2", "案3"]
}}

ルール：
- タイトルの文法に問題があるか、明らかにもっと良い案がある場合は、"suggestions" に
  必ずちょうど3つのタイトル案を入れてください。
- タイトルの文法に問題がなく、かつ今のタイトルより良い案が思いつかない場合は、
  "suggestions" は空配列 [] にしてください（無理に3つ出さない）。
- 提案するタイトルは、必ずテスさんのタイトルのスタイル（角括弧タグ、「〜な件」「〜を徹底比較」
  「〜？」で終わる問いかけ等）に沿ったものにしてください。
"""


def _parse_batch_response(raw: str, indices) -> dict:
    results = {}
    for idx in indices:
        open_tag = re.escape(NODE_OPEN.format(i=idx))
        close_tag = re.escape(NODE_CLOSE.format(i=idx))
        m = re.search(open_tag + r"\n?(.*?)\n?" + close_tag, raw, re.S)
        if m:
            results[idx] = m.group(1)
    return results


def _response_text(resp) -> str:
    text = getattr(resp, "text", None)
    if text:
        return text
    # フォールバック：candidates から手動で組み立てる
    out = []
    for cand in getattr(resp, "candidates", []) or []:
        content = getattr(cand, "content", None)
        for part in getattr(content, "parts", []) or []:
            if getattr(part, "text", None):
                out.append(part.text)
    return "".join(out)


def correct_nodes_batch(client, model: str, nodes_texts):
    """nodes_texts: [(node_index, protected_text), ...] を1回のAPI呼び出しでまとめて校正する。
    戻り値: {node_index: corrected_protected_text}
    """
    if not nodes_texts:
        return {}

    parts = []
    for idx, text in nodes_texts:
        parts.append(f"{NODE_OPEN.format(i=idx)}\n{text}\n{NODE_CLOSE.format(i=idx)}")
    user_content = "\n\n".join(parts)

    resp = client.models.generate_content(
        model=model,
        contents=user_content,
        config=types.GenerateContentConfig(
            system_instruction=build_system_prompt(),
            max_output_tokens=8000,
        ),
    )
    raw = _response_text(resp)
    indices = [idx for idx, _ in nodes_texts]
    parsed = _parse_batch_response(raw, indices)

    # パースに失敗したブロックは「変更なし」として原文を採用する（安全側に倒す）
    out = {}
    for idx, text in nodes_texts:
        out[idx] = parsed.get(idx, text)
    return out


def correct_node_single(client, model: str, protected_text: str, instruction: str, previous_correction: str = None) -> str:
    """redo（1ブロックだけやり直す）。ユーザーの追加指示を反映する。"""
    system = build_system_prompt()
    system += (
        "\n\n# 追加指示（今回のやり直し専用）\n"
        "ユーザーは前回の修正結果に納得していません。以下のユーザーの要望を最優先で反映して、"
        "もう一度このブロックだけを校正してください。要望が「直さないでほしい」という内容であれば、"
        "原文により近い形に戻してください。\n"
        f"ユーザーの要望: {instruction}\n"
    )
    prev = previous_correction or protected_text
    user_content = (
        f"{NODE_OPEN.format(i=0)}\n"
        f"[原文]\n{protected_text}\n\n[前回の修正結果]\n{prev}\n"
        f"{NODE_CLOSE.format(i=0)}\n\n"
        "上記の[原文]について、ユーザーの要望を反映した新しい修正結果だけを、"
        f"{NODE_OPEN.format(i=0)} と {NODE_CLOSE.format(i=0)} で囲んで返してください。"
    )
    resp = client.models.generate_content(
        model=model,
        contents=user_content,
        config=types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=4000,
        ),
    )
    raw = _response_text(resp)
    parsed = _parse_batch_response(raw, [0])
    return parsed.get(0, protected_text).strip("\n")


def suggest_title(client, model: str, title: str, article_text: str) -> dict:
    """記事タイトルの文法チェックと改善案の提案。
    戻り値: {"grammar_ok": bool, "note": str, "suggestions": [str, ...]}
    """
    # 本文が長すぎる場合は先頭部分だけ渡す（タイトル判断には十分）
    article_excerpt = article_text[:6000]
    user_content = (
        f"現在のタイトル: {title}\n\n"
        f"本文:\n{article_excerpt}"
    )
    resp = client.models.generate_content(
        model=model,
        contents=user_content,
        config=types.GenerateContentConfig(
            system_instruction=build_title_system_prompt(),
            response_mime_type="application/json",
            max_output_tokens=1500,
        ),
    )
    raw = _response_text(resp).strip()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        m = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(m.group(0)) if m else {}

    suggestions = data.get("suggestions") or []
    if not isinstance(suggestions, list):
        suggestions = []
    suggestions = [s for s in suggestions if isinstance(s, str) and s.strip()][:3]

    return {
        "grammar_ok": bool(data.get("grammar_ok", True)),
        "note": data.get("note") or "",
        "suggestions": suggestions,
    }
