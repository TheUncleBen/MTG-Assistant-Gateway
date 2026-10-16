"""Keep formatting (0.7.29, Q137 "Keep it, new words plain"; amends Q70 and Q90-Q92).

A deck description or a comment on Archidekt is a Quill document: text with bold, italics, links,
headings and lists, card links and pictures. An assistant and the gateway's forms see and write
plain text only. Before 0.7.29 every edit replaced the whole document with that plain text, so
bold text and links the member had made on archidekt.com were lost.

Now an edit keeps them: the old document's text and the new plain text are compared word by word,
the parts whose words did not change keep their formatting (and their card links and pictures),
and every new or changed word is plain text (Q70 still holds for new text: an assistant can never
add a link or formatting). A picture or card link inside text that changed goes with that text.

Before anything is written the member sees the result with its formatting: the review page draws
it (``doc_html``), and a change that keeps formatting is approved there, not on a card in the
chat or in a batch, which cannot show it (``keeps_formatting``). A restore and a clone write the
stored document unchanged, as before (Q70)."""

from __future__ import annotations

import difflib
import html
import json
import re
from typing import Any
from urllib.parse import urlsplit

from .archidekt import _plain_insert, _quill_ops, rich_text

# One token per word, per white-space character and per other character, so every character of a
# text belongs to exactly one token and a changed word is replaced whole.
_TOKEN = re.compile(r"\w+|\s|[^\w\s]")


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text)


def _starts(tokens: list[str]) -> list[int]:
    """The character offset where each token starts, plus the total length at the end."""
    out = [0]
    for t in tokens:
        out.append(out[-1] + len(t))
    return out


def is_formatted(value: Any) -> bool:
    """Whether ``value`` is a Quill document with anything besides plain text: formatting, a link,
    a card link, a picture or a heading or list line."""
    ops = _quill_ops(value)
    return ops is not None and not all(_plain_insert(op) for op in ops)


def _length(op: dict[str, Any]) -> int:
    """How many characters of the plain text (``rich_text``) one op makes: its text, a card link's
    card name, and nothing for another embed (a picture)."""
    insert = op.get("insert")
    if isinstance(insert, str):
        return len(insert)
    if isinstance(insert, dict) and isinstance(insert.get("card-link"), str):
        return len(insert["card-link"])
    return 0


def _push(out: list[dict[str, Any]], insert: Any, attributes: Any = None) -> None:
    """Append an op, joining it to the last one when both are text with the same attributes."""
    if isinstance(insert, str) and not insert:
        return
    op: dict[str, Any] = {"insert": insert}
    if attributes:
        op["attributes"] = attributes
    if out and isinstance(insert, str) and isinstance(out[-1].get("insert"), str):
        if (out[-1].get("attributes") or None) == (attributes or None):
            out[-1]["insert"] += insert
            return
    out.append(op)


# Above this many token pairs the changed middle is compared line by line first (a 20,000-character
# description rewritten throughout would otherwise take seconds); inside a changed line, word by
# word again while that stays small.
_MAX_PAIRS = 400_000


def _opcodes(a: list[str], b: list[str]) -> list[tuple[str, int, int, int, int]]:
    """SequenceMatcher's opcodes for two token lists, quick for the usual edit: the common start
    and end are matched first, and only the middle is compared."""
    head = 0
    while head < len(a) and head < len(b) and a[head] == b[head]:
        head += 1
    tail = 0
    while tail < len(a) - head and tail < len(b) - head and a[-1 - tail] == b[-1 - tail]:
        tail += 1
    out: list[tuple[str, int, int, int, int]] = []
    if head:
        out.append(("equal", 0, head, 0, head))
    a_mid, b_mid = a[head : len(a) - tail], b[head : len(b) - tail]
    for tag, i1, i2, j1, j2 in _middle(a_mid, b_mid):
        out.append((tag, i1 + head, i2 + head, j1 + head, j2 + head))
    if tail:
        out.append(("equal", len(a) - tail, len(a), len(b) - tail, len(b)))
    return _whole_changes(out, a)


def _whole_changes(
    ops: list[tuple[str, int, int, int, int]], a: list[str]
) -> list[tuple[str, int, int, int, int]]:
    """Joins changes split only by white space ("the guide" -> "my notes" matches the space), so
    a changed phrase is replaced whole and no formatting is left on a lone space between them."""
    out: list[tuple[str, int, int, int, int]] = []
    for op in ops:
        if (
            len(out) >= 2
            and op[0] != "equal"
            and out[-1][0] == "equal"
            and out[-2][0] != "equal"
            and all(not t.strip() for t in a[out[-1][1] : out[-1][2]])
        ):
            out.pop()  # the white space between them
            first = out.pop()
            out.append(("replace", first[1], op[2], first[3], op[4]))
            continue
        if out and op[0] != "equal" and out[-1][0] != "equal":
            first = out.pop()
            out.append(("replace", first[1], op[2], first[3], op[4]))
            continue
        out.append(op)
    return out


def _lines(tokens: list[str]) -> list[tuple[int, int]]:
    """Token spans of each line, its line break included."""
    spans, start = [], 0
    for i, t in enumerate(tokens):
        if t == "\n":
            spans.append((start, i + 1))
            start = i + 1
    if start < len(tokens):
        spans.append((start, len(tokens)))
    return spans


def _middle(a: list[str], b: list[str]) -> list[tuple[str, int, int, int, int]]:
    if not a and not b:
        return []
    if not a or not b:
        return [("delete" if a else "insert", 0, len(a), 0, len(b))]
    if len(a) * len(b) <= _MAX_PAIRS:
        return difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes()
    a_lines, b_lines = _lines(a), _lines(b)
    a_keys = ["".join(a[s:e]) for s, e in a_lines]
    b_keys = ["".join(b[s:e]) for s, e in b_lines]
    out: list[tuple[str, int, int, int, int]] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a_keys, b_keys, autojunk=False).get_opcodes():
        a1 = a_lines[i1][0] if i1 < len(a_lines) else len(a)
        a2 = a_lines[i2 - 1][1] if i2 > i1 else a1
        b1 = b_lines[j1][0] if j1 < len(b_lines) else len(b)
        b2 = b_lines[j2 - 1][1] if j2 > j1 else b1
        if tag == "replace" and (a2 - a1) * (b2 - b1) <= _MAX_PAIRS:
            matcher = difflib.SequenceMatcher(None, a[a1:a2], b[b1:b2], autojunk=False)
            out.extend((t, x1 + a1, x2 + a1, y1 + b1, y2 + b1) for t, x1, x2, y1, y2 in matcher.get_opcodes())
        else:
            out.append((tag, a1, a2, b1, b2))
    return out


def keep_formatting(old: Any, new_text: str) -> tuple[str, int] | None:
    """The Quill document an edit to ``new_text`` writes when the stored ``old`` value has
    formatting, with the number of pictures and card links it drops (they sat in text that
    changed); None when there is nothing to keep (``old`` is plain text or plain inserts only),
    when ``new_text`` is empty (the text is cleared), or when ``old`` holds an op this cannot
    place (then the edit is written as plain text, as before 0.7.29).

    The document's plain text is ``new_text`` exactly (checked), so the review's text and what
    is written agree."""
    ops = _quill_ops(old)
    if ops is None or not new_text or all(_plain_insert(op) for op in ops):
        return None
    if not all(isinstance(op, dict) and "insert" in op and set(op) <= {"insert", "attributes"} for op in ops):
        return None  # a retain or delete op: not a stored document
    pieces: list[tuple[int, int, dict[str, Any]]] = []
    pos = 0
    for op in ops:
        n = _length(op)
        pieces.append((pos, pos + n, op))
        pos += n
    old_full = rich_text(old) + "\n"
    if pos == len(old_full) - 1:  # the document did not end with a line break: add one
        pieces.append((pos, pos + 1, {"insert": "\n"}))
        pos += 1
    if pos != len(old_full):
        return None
    new_full = new_text + "\n"
    a_tok, b_tok = _tokens(old_full), _tokens(new_full)
    a_at, b_at = _starts(a_tok), _starts(b_tok)
    out: list[dict[str, Any]] = []
    gone: set[int] = set()  # pictures and card links that go with changed text
    for tag, i1, i2, j1, j2 in _opcodes(a_tok, b_tok):
        a, b = a_at[i1], a_at[i2]
        if tag == "equal":
            for s, e, op in pieces:
                if s == e:
                    if a <= s < b:  # a picture inside unchanged text stays
                        _push(out, op["insert"], op.get("attributes"))
                    continue
                if e <= a or s >= b:
                    continue
                insert = op["insert"]
                if isinstance(insert, str):
                    _push(out, insert[max(a, s) - s : min(b, e) - s], op.get("attributes"))
                elif a <= s and e <= b:  # a card link wholly inside unchanged text stays
                    _push(out, insert, op.get("attributes"))
                else:  # part of a card link's name: its words, plain
                    _push(out, insert["card-link"][max(a, s) - s : min(b, e) - s])
        else:
            for k, (s, e, op) in enumerate(pieces):
                if s == e and s == a:  # a picture just before the changed text stays
                    _push(out, op["insert"], op.get("attributes"))
                elif (s == e and a < s < b) or (
                    s < e and s < b and e > a and not isinstance(op["insert"], str)
                ):
                    gone.add(k)
            _push(out, new_full[b_at[j1] : b_at[j2]])
    total = len(old_full)
    for s, e, op in pieces:  # a picture at the very end
        if s == e == total:
            _push(out, op["insert"], op.get("attributes"))
    if not out or not isinstance(out[-1].get("insert"), str) or not out[-1]["insert"].endswith("\n"):
        _push(out, "\n")
    doc = json.dumps({"ops": out}, ensure_ascii=False, separators=(",", ":"))
    if rich_text(doc) != new_text:
        return None
    return doc, len(gone)


def keeps_formatting(row: dict[str, Any]) -> bool:
    """Whether a stored proposal writes a document that keeps formatting (a review row with
    ``after_doc``): the member approves it on the review page, which shows it (Q137)."""
    return any(isinstance(r, dict) and r.get("after_doc") for r in row.get("rows") or [])


# -- drawing a document for the review page ------------------------------------------------------

_INLINE = (("bold", "strong"), ("italic", "em"), ("underline", "u"), ("strike", "s"), ("code", "code"))


def _href(value: Any) -> str | None:
    """A link's address when it is an http(s) one, else None (drawn as plain text)."""
    url = str(value or "").strip()
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    return url if parts.scheme in ("http", "https") and parts.netloc else None


def _inline_html(insert: Any, attrs: dict[str, Any]) -> str:
    if isinstance(insert, dict):
        if isinstance(insert.get("card-link"), str):
            return f"<span class='qcard'>{html.escape(insert['card-link'])}</span>"
        return "<span class='qpic'>[picture]</span>"
    text = html.escape(str(insert))
    for key, tag in _INLINE:
        if attrs.get(key):
            text = f"<{tag}>{text}</{tag}>"
    link = attrs.get("link")
    if link:
        href = _href(link)
        host = urlsplit(href).netloc if href else str(link)
        if href:
            text = (
                f"<a href='{html.escape(href)}' rel='noopener noreferrer nofollow' target='_blank'>{text}</a>"
                f"<span class='qhost'> ({html.escape(host)})</span>"
            )
        else:
            text += f"<span class='qhost'> (link: {html.escape(host)})</span>"
    return text


def doc_html(value: Any) -> str:
    """A Quill document drawn as safe HTML for the review page: bold, italics, underline, strike,
    code, links (each with its site shown, opening in a new tab), headings, lists, quotes, card
    links (as their names) and pictures (as "[picture]", never loaded). Text that is not a
    document is drawn as plain lines."""
    ops = _quill_ops(value)
    if ops is None:
        return "".join(f"<p>{html.escape(line) or '<br>'}</p>" for line in str(value or "").split("\n"))
    blocks: list[tuple[str, dict[str, Any]]] = []
    line = ""
    for op in ops:
        if not isinstance(op, dict):
            continue
        insert, attrs = op.get("insert"), op.get("attributes") or {}
        if not isinstance(attrs, dict):
            attrs = {}
        if isinstance(insert, str):
            parts = insert.split("\n")
            for i, part in enumerate(parts):
                if part:
                    line += _inline_html(part, attrs)
                if i < len(parts) - 1:  # a line break ends the line; its attributes format it
                    blocks.append((line, attrs))
                    line = ""
        elif insert is not None:
            line += _inline_html(insert, attrs)
    if line:
        blocks.append((line, {}))
    out: list[str] = []
    open_list = ""
    for content, attrs in blocks:
        kind = attrs.get("list")
        tag = "ol" if kind == "ordered" else "ul" if kind in ("bullet", "checked", "unchecked") else ""
        if tag != open_list:
            if open_list:
                out.append(f"</{open_list}>")
            if tag:
                out.append(f"<{tag}>")
            open_list = tag
        body = content or "<br>"
        if tag:
            out.append(f"<li>{body}</li>")
        elif attrs.get("header") in (1, 2, 3, 4, 5, 6):
            level = min(6, int(attrs["header"]) + 2)  # under the page's own headings
            out.append(f"<h{level} class='qh'>{body}</h{level}>")
        elif attrs.get("blockquote"):
            out.append(f"<blockquote>{body}</blockquote>")
        elif attrs.get("code-block"):
            out.append(f"<pre>{body}</pre>")
        else:
            out.append(f"<p>{body}</p>")
    if open_list:
        out.append(f"</{open_list}>")
    return "".join(out)


DOC_CSS = """
.qdoc{border:1px solid var(--border);border-radius:var(--radius-panel);padding:.5rem .75rem;margin:.4rem 0;
  background:var(--surface);overflow-wrap:anywhere;max-height:28rem;overflow:auto}
.qdoc p{margin:.2rem 0}
.qdoc ul,.qdoc ol{margin:.2rem 0;padding-left:1.4rem}
.qdoc blockquote{margin:.3rem 0;padding-left:.6rem;border-left:3px solid var(--border)}
.qdoc pre{white-space:pre-wrap;margin:.3rem 0}
.qdoc .qh{margin:.4rem 0 .2rem;font-size:1rem}
.qdoc .qhost{color:var(--text-muted);font-size:.85em}
.qdoc .qcard{text-decoration:underline dotted}
.qdoc .qpic{color:var(--text-muted);font-style:italic}
"""
