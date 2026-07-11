"""
html_diff.py
HTML の中から日本語テキストノードだけを抜き出し、修正前後を diff して
レビュー用 HTML（del/ins マーク付き）または最終確定テキストを組み立てる。

WordPress の post_content には Cocoon テーマのショートコード（例: [box class="pink"]...[/box]）が
生のテキストとして混ざっているため、AI に渡す前に [...] 部分をプレースホルダーに置換して保護する。
"""
import html as html_lib
import re
from difflib import SequenceMatcher

from bs4 import BeautifulSoup, Comment, NavigableString

# ひらがな・カタカナ・漢字（CJK統合漢字）・半角カナ
JP_RE = re.compile(r"[぀-ヿ㐀-鿿ｦ-ﾟ]")

# WordPress/Cocoon ショートコード [xxx] を保護する
SHORTCODE_RE = re.compile(r"\[[^\[\]\n]{1,300}\]")

SKIP_PARENT_TAGS = {"script", "style", "code", "pre", "textarea"}

PLACEHOLDER_OPEN = "⟦SC"
PLACEHOLDER_CLOSE = "⟧"

GAP_MERGE_THRESHOLD = 2  # この文字数以下の「変化なし」区間は前後の変更とまとめる


def _is_skippable(node) -> bool:
    if isinstance(node, Comment):
        return True
    if not isinstance(node, NavigableString):
        return True
    text = str(node)
    if not text.strip():
        return True
    if not JP_RE.search(text):
        return True
    for parent in node.parents:
        if getattr(parent, "name", None) in SKIP_PARENT_TAGS:
            return True
    return False


def extract_nodes(content_html: str):
    soup = BeautifulSoup(content_html or "", "html.parser")
    nodes = [n for n in soup.find_all(string=True) if not _is_skippable(n)]
    return soup, nodes


def protect_shortcodes(text: str):
    mapping = {}

    def _repl(m):
        key = f"{PLACEHOLDER_OPEN}{len(mapping)}{PLACEHOLDER_CLOSE}"
        mapping[key] = m.group(0)
        return key

    protected = SHORTCODE_RE.sub(_repl, text)
    return protected, mapping


def restore_shortcodes(text: str, mapping: dict) -> str:
    for key, val in mapping.items():
        text = text.replace(key, val)
    return text


def diff_spans(original: str, corrected: str):
    if original == corrected:
        return [{"tag": "equal", "text": original}] if original else []

    sm = SequenceMatcher(None, original, corrected, autojunk=False)
    raw_spans = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        o = original[i1:i2]
        c = corrected[j1:j2]
        if tag == "equal":
            if raw_spans and raw_spans[-1]["tag"] == "equal":
                raw_spans[-1]["text"] += o
            else:
                raw_spans.append({"tag": "equal", "text": o})
        else:
            if raw_spans and raw_spans[-1]["tag"] == "change":
                raw_spans[-1]["original"] += o
                raw_spans[-1]["corrected"] += c
            else:
                raw_spans.append({"tag": "change", "original": o, "corrected": c})

    spans = []
    i = 0
    while i < len(raw_spans):
        sp = raw_spans[i]
        if (
            sp["tag"] == "equal"
            and len(sp["text"]) <= GAP_MERGE_THRESHOLD
            and spans
            and spans[-1]["tag"] == "change"
            and i + 1 < len(raw_spans)
            and raw_spans[i + 1]["tag"] == "change"
        ):
            nxt = raw_spans[i + 1]
            spans[-1]["original"] += sp["text"] + nxt["original"]
            spans[-1]["corrected"] += sp["text"] + nxt["corrected"]
            i += 2
            continue
        spans.append(sp)
        i += 1
    return spans


def count_changes(spans) -> int:
    return sum(1 for s in spans if s["tag"] == "change")


def render_node_fragment(node_index, spans, statuses, mode, shortcode_map):
    out = []
    change_idx = 0
    for sp in spans:
        if sp["tag"] == "equal":
            text = restore_shortcodes(sp["text"], shortcode_map)
            out.append(html_lib.escape(text) if mode == "review" else text)
        else:
            status = statuses[change_idx] if change_idx < len(statuses) else "accepted"
            orig = restore_shortcodes(sp["original"], shortcode_map)
            corr = restore_shortcodes(sp["corrected"], shortcode_map)
            if mode == "review":
                cid = f"{node_index}:{change_idx}"
                cls = "accepted" if status == "accepted" else "rejected"
                out.append(
                    '<span class="tp-change ' + cls + '" data-cid="' + cid + '">'
                    + '<del class="tp-del">' + html_lib.escape(orig) + '</del>'
                    + '<ins class="tp-ins">' + html_lib.escape(corr) + '</ins>'
                    + '<span class="tp-ctrl" contenteditable="false">'
                    + '<button type="button" class="tp-btn tp-accept" title="採用する">&#10003;</button>'
                    + '<button type="button" class="tp-btn tp-reject" title="元のままにする">&#10005;</button>'
                    + '<button type="button" class="tp-btn tp-redo" title="この修正をやり直す">&#9998;</button>'
                    + '</span></span>'
                )
            else:
                out.append(corr if status == "accepted" else orig)
            change_idx += 1
    return "".join(out)


def render_node(node_index, protected_original, protected_corrected, statuses, mode, shortcode_map):
    spans = diff_spans(protected_original, protected_corrected)
    inner = render_node_fragment(node_index, spans, statuses, mode, shortcode_map)
    if mode == "review":
        return '<span class="tp-node" data-node-index="' + str(node_index) + '">' + inner + '</span>'
    return inner


def build_html(raw_content: str, node_records: list, mode: str) -> str:
    soup, dom_nodes = extract_nodes(raw_content)
    for idx, dom_node in enumerate(dom_nodes):
        rec = node_records[idx] if idx < len(node_records) else None
        if not rec:
            continue
        if rec["protected_original"] == rec["protected_corrected"]:
            continue
        fragment_html = render_node(
            idx,
            rec["protected_original"],
            rec["protected_corrected"],
            rec["statuses"],
            mode,
            rec["shortcode_map"],
        )
        if mode == "review":
            frag_soup = BeautifulSoup(fragment_html, "html.parser")
            dom_node.replace_with(*list(frag_soup.contents))
        else:
            dom_node.replace_with(fragment_html)
    return str(soup)
