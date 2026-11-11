"""Words a member never reads on a page: a clock time in "UTC" (the page draws every time in the
member's own time zone; the text inside a <time> is the no-script fallback) and "API".

Every page the UI matrix walks, on the same seeded account; the admin pages are for the operator
and are left out, as is a report's Markdown export, which leaves the page as a document."""

from __future__ import annotations

import re

import httpx

from .test_overflow_sweep_browser import _seed, server  # noqa: F401
from .test_suggest_browser import Server
from .ui_matrix import pages

_OUT = re.compile(r"<(script|style|time|template|noscript|textarea)\b.*?</\1>", re.S)
_WORDS = re.compile(r"\bUTC\b|\bAPI\b")


def test_no_member_page_says_utc_or_api(server: Server) -> None:  # noqa: F811
    sid, pending = _seed(server)
    found: list[str] = []
    with httpx.Client(base_url=server.base, cookies={"mtg_session": sid}, timeout=120) as http:
        for path in pages(pending):
            path = path.split("#")[0]
            if path.startswith("/admin"):
                continue
            r = http.get(path, headers={"accept": "text/html"}, follow_redirects=True)
            text = re.sub(r"<[^>]+>", " ", _OUT.sub(" ", r.text))
            for m in _WORDS.finditer(text):
                found.append(f"{path}: …{' '.join(text[max(0, m.start() - 40) : m.end() + 20].split())}…")
    assert not found, "\n".join(found[:20])
