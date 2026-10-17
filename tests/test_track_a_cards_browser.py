"""Release 0.7.29, approvals and proposals: the waiting changes card and the proposal card's
"Apply all" line in a real browser, driven by a fake host that speaks the MCP Apps protocol
(Q134 A3, A6). Nothing is sent before a press; Apply all asks first and sends the set's code."""

from __future__ import annotations

import os
import time

import pytest

from mtg_gateway.approve import APPROVAL_META_KEY, APPROVAL_SET_META_KEY, APPROVALS_META_KEY, card_html
from mtg_gateway.cards import card_html as cards_html

from .test_consent_browser import _chromium_or_skip, _launch

pytest.importorskip("playwright")

NOW = int(time.time())

# The host: answers ui/initialize with the theme it is given, pushes the tool result, records
# everything, and answers each tools/call from window.replies[name] (null: the host refuses).
HOST = """<!DOCTYPE html><html><body style="margin:0">
<iframe id="f" sandbox="allow-scripts" style="width:100%;height:10px;border:0"></iframe>
<script>
window.log = []; window.replies = {}; window.theme = "light";
var frame = document.getElementById("f");
window.addEventListener("message", (ev) => {
  const m = ev.data; if (!m || m.jsonrpc !== "2.0") return;
  window.log.push(m);
  const send = (x) => frame.contentWindow.postMessage(x, "*");
  if (m.method === "ui/initialize") {
    send({jsonrpc: "2.0", id: m.id, result: {protocolVersion: "2026-01-26",
      hostInfo: {name: "fake", version: "0"}, hostCapabilities: {openLinks: {}, serverTools: {}},
      hostContext: {theme: window.theme, platform: "web"}}});
  } else if (m.method === "ui/notifications/initialized") {
    send({jsonrpc: "2.0", method: "ui/notifications/tool-result", params: window.toolResult});
  } else if (m.method === "ui/notifications/size-changed") {
    frame.style.height = (m.params.height + 4) + "px";
  } else if (m.method === "tools/call") {
    const r = window.replies[m.params.name];
    if (r === undefined || r === null) {
      send({jsonrpc: "2.0", id: m.id, error: {code: -32000, message: "blocked"}});
    } else {
      send({jsonrpc: "2.0", id: m.id,
            result: {content: [{type: "text", text: "x"}], structuredContent: r, _meta: {}}});
    }
  } else if (m.id !== undefined && m.method) {
    send({jsonrpc: "2.0", id: m.id, result: {}});
  }
});
</script></body></html>"""


def _line(pid: str, no: int, summary: str, *, own: bool = True, **more: object) -> dict:
    return {
        "proposal_id": pid,
        "board_no": no,
        "kind": "edit",
        "deck_id": "42",
        "deck_name": "Sample Deck",
        "created_at": NOW - 600 * no,
        "expires_at": NOW + 86400 * 5,
        "created_by": "app: Claude (c1)" if own else "app: ChatGPT (c2)",
        "own": own,
        "version": 1,
        "status": None,
        "summary": summary,
        "review_url": f"https://gateway.example.com/proposals/{pid}",
        **more,
    }


WAITING = {
    "ok": True,
    "deck_id": "42",
    "deck_name": "Sample Deck",
    "waiting": [
        _line("p1", 1, "+1 Opt"),
        _line(
            "p2",
            2,
            "-1 Island <img src=x onerror=alert(1)>",
            status="needs_look",
            status_reason="Island is at 13 now.",
        ),
        _line("p3", 3, "+1 Swamp", own=False),
    ],
    "count": 3,
    "own_count": 2,
    "other_apps": [{"app": "ChatGPT", "count": 1}],
    "review_all_url": "https://gateway.example.com/proposals#deck-42",
    "writes_enabled": True,
}
WAITING_META = {
    APPROVALS_META_KEY: {"p1": "code-1", "p2": "code-2"},
    APPROVAL_SET_META_KEY: {"ids": ["p1", "p2"], "code": "set-code"},
}
DONE = {
    "ok": True,
    "batch_id": "b1",
    "state": "done",
    "summary": "Applied 1. 1 not sent.",
    "applied": [{"proposal_id": "p1", "board_no": 1, "summary": "+1 Opt"}],
    "not_sent": [{"proposal_id": "p2", "board_no": 2, "summary": "-1 Island", "why": "Needs a look."}],
    "failed": [],
    "result_url": "https://gateway.example.com/proposals/applied/b1",
}

SHOTS = os.environ.get("MTG_SHOTS_DIR")  # screenshots for the reviewer, only when asked


def _open(page, html: str, result: dict, replies: dict, theme: str = "light"):
    page.set_content(HOST)
    page.evaluate("(r) => { window.toolResult = r; }", result)
    page.evaluate("(r) => { window.replies = r; }", replies)
    page.evaluate("(t) => { window.theme = t; }", theme)
    page.evaluate("(h) => { document.getElementById('f').srcdoc = h; }", html)
    page.wait_for_function("() => window.log.some(m => m.method === 'ui/notifications/initialized')")
    return page.frame_locator("#f")


def _tool_calls(page, name: str) -> list[dict]:
    return [
        m["params"]["arguments"]
        for m in page.evaluate("() => window.log")
        if m.get("method") == "tools/call" and m["params"]["name"] == name
    ]


def _shot(page, name: str) -> None:
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        page.screenshot(path=os.path.join(SHOTS, name + ".png"), full_page=True)


@pytest.mark.parametrize("width,theme", [(360, "light"), (360, "dark"), (884, "dark"), (1280, "light")])
def test_the_waiting_card_approves_one_and_applies_all_after_a_confirm(width: int, theme: str) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            page = browser.new_page(viewport={"width": width, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("dialog", lambda d: errors.append("dialog " + d.message))
            replies = {
                "confirm_proposal": {"ok": True, "proposal_id": "p1", "state": "rejected"},
                "confirm_waiting": DONE,
            }
            card = _open(
                page,
                cards_html("waiting-card"),
                {"structuredContent": WAITING, "_meta": WAITING_META},
                replies,
                theme,
            )
            card.locator("#row-p1").wait_for()
            assert card.locator("h1").inner_text() == "Waiting changes to Sample Deck"
            # another app's change has its review link only (O9)
            assert card.locator("#row-p3 button").count() == 0
            assert card.locator("#row-p3 a.link").inner_text() == "Review"
            # data stays text
            assert "<img" in card.locator("#row-p2 .name").inner_text()
            assert card.locator("#row-p2 .why").inner_text() == "Island is at 13 now."
            assert "1 from ChatGPT also waits." in card.locator("#note").inner_text()
            # nothing is sent before a press
            assert not _tool_calls(page, "confirm_proposal") and not _tool_calls(page, "confirm_waiting")
            card.locator("#apply-all:not([disabled])").wait_for()
            assert card.locator("#apply-all").inner_text() == "Apply all 2"
            # no sideways scrolling at any width
            overflow = card.locator("body").evaluate("b => b.scrollWidth - b.clientWidth")
            assert overflow <= 0, overflow
            _shot(page, f"waiting-card-{width}-{theme}")
            # Reject one row: its own code only
            card.locator("#row-p1 button.danger").click()
            card.locator("#row-p1 .done").wait_for()
            assert _tool_calls(page, "confirm_proposal") == [
                {"proposal_id": "p1", "approval": "code-1", "decision": "reject"}
            ]
            assert card.locator("#row-p1 .done").inner_text() == "Rejected"
            # with one left of the set, Apply all is gone (it needs two)
            assert card.locator("#apply-all").count() == 0
        finally:
            browser.close()
    assert not errors, errors


def test_apply_all_on_the_waiting_card_lists_the_order_then_sends_the_set() -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            page = browser.new_page(viewport={"width": 390, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            changed = {
                **WAITING,
                "waiting": [
                    {
                        **WAITING["waiting"][0],
                        "version": 2,
                        "changed_by": "app: ChatGPT (c2)",
                        "what_changed": "Opt instead of Ponder",
                    },
                    *WAITING["waiting"][1:],
                ],
            }
            card = _open(
                page,
                cards_html("waiting-card"),
                {"structuredContent": changed, "_meta": WAITING_META},
                {"confirm_waiting": DONE},
            )
            assert "Version 2, changed by ChatGPT" in card.locator("#row-p1").inner_text()
            # by keyboard: the press redraws the buttons, and focus lands on what replaced them
            card.locator("#apply-all:not([disabled])").focus()
            page.keyboard.press("Enter")
            assert card.locator("#confirm:focus").count() == 1
            card.locator("#cancel").focus()
            page.keyboard.press("Enter")
            assert card.locator("#apply-all:focus").count() == 1
            page.keyboard.press("Enter")
            assert card.locator("#confirm:focus").count() == 1
            assert "Apply 2 changes in this order" in card.locator("#confirm-text").inner_text()
            assert card.locator("#order li").all_inner_texts() == [
                "#1 +1 Opt\nVersion 2, changed by ChatGPT: Opt instead of Ponder.",
                "#2 -1 Island <img src=x onerror=alert(1)>",
            ]
            assert not _tool_calls(page, "confirm_waiting")
            _shot(page, "waiting-card-confirm-390")
            card.locator("#go:not([disabled])").click()
            card.locator("#result:not(.hidden)").wait_for()
            assert _tool_calls(page, "confirm_waiting") == [
                {"proposal_ids": ["p1", "p2"], "approval": "set-code"}
            ]
            assert card.locator("#status:focus").count() == 1
            assert card.locator("#status").inner_text() == "Applied 1. 1 not sent."
            assert card.locator("#res-list li").all_inner_texts() == [
                "Applied: #1 +1 Opt",
                "Not sent: #2 -1 Island. Needs a look.",
            ]
            assert card.locator("#row-p1 .done").inner_text() == "Applied"
            assert card.locator("#actions a.link", has_text="Undo or see the details").count() == 1
            _shot(page, "waiting-card-result-390")
        finally:
            browser.close()
    assert not errors, errors


PROPOSAL = {
    "ok": True,
    "proposal_id": "p2",
    "board_no": 2,
    "kind": "edit",
    "deck_id": "42",
    "deck_name": "Sample Deck",
    "state": "pending",
    "diff": "+1 Delver of Secrets",
    "rows": [{"kind": "add", "name": "Delver of Secrets", "qty": 1}],
    "created_by": "app: Claude (c1)",
    "expires_at": NOW + 3600,
    "review_url": "https://gateway.example.com/proposals/p2",
    "writes_enabled": True,
    "next_step": "...",
    "result": None,
    "deck_waiting": {
        "earlier": 2,
        "same_app": 2,
        "other_apps": [{"app": "ChatGPT", "count": 1}],
        "review_all_url": "https://gateway.example.com/proposals#deck-42",
        "this_app": "app: Claude (c1)",
        "set_lines": [
            {
                "proposal_id": "p1",
                "board_no": 1,
                "summary": "+1 Opt",
                "status": "held",
                "status_reason": "#3 it depends on was rejected.",
                "version": 2,
                "changed_by": "app: ChatGPT (c2)",
                "what_changed": "Opt instead of Ponder",
            },
            {"proposal_id": "p2", "board_no": 2, "summary": "+1 Delver of Secrets", "version": 1},
        ],
    },
}


@pytest.mark.parametrize("width,theme", [(360, "dark"), (1280, "light")])
def test_the_proposal_card_names_the_others_and_applies_all(width: int, theme: str) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            page = browser.new_page(viewport={"width": width, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.route("https://api.scryfall.com/**", lambda r: r.abort())
            meta = {
                APPROVAL_META_KEY: "code-2",
                APPROVAL_SET_META_KEY: {"ids": ["p1", "p2"], "code": "set-code"},
            }
            applied = {**PROPOSAL, "state": "applied", "applied_at": NOW}
            card = _open(
                page,
                card_html(),
                {"structuredContent": PROPOSAL, "_meta": meta},
                {
                    "confirm_waiting": {**DONE, "summary": "Applied 2.", "not_sent": []},
                    "get_proposal": PROPOSAL,
                },
                theme,
            )
            card.locator("#also:not(.hidden)").wait_for()
            assert (
                card.locator("#also-text").inner_text()
                == "1 more from Claude waits on this deck. 1 from ChatGPT also waits."
            )
            assert card.locator("#also a.link").inner_text() == "Review all"
            _shot(page, f"proposal-card-also-{width}-{theme}")
            card.locator("#apply-all:not([disabled])").focus()
            page.keyboard.press("Enter")
            assert card.locator("#also:focus").count() == 1
            assert (
                card.locator("#also-text")
                .inner_text()
                .startswith("Apply these 2 changes to this deck, oldest first?")
            )
            # the confirm lists what the press applies, a held one and a changed one saying so
            assert card.locator("#also-list li").all_inner_texts() == [
                "#1 +1 Opt\nHeld: #3 it depends on was rejected.\n"
                "Version 2, changed by ChatGPT: Opt instead of Ponder.",
                "#2 +1 Delver of Secrets",
            ]
            _shot(page, f"proposal-card-confirm-{width}-{theme}")
            assert not _tool_calls(page, "confirm_waiting")
            # the card reads the proposal again after the apply
            page.evaluate("(r) => { window.replies.get_proposal = r; }", applied)
            card.locator("#apply-all:not([disabled])", has_text="Apply 2 changes").click()
            card.locator("#status.ok").wait_for()
            assert _tool_calls(page, "confirm_waiting") == [
                {"proposal_ids": ["p1", "p2"], "approval": "set-code"}
            ]
            assert not _tool_calls(page, "confirm_proposal")
            assert card.locator("#status").inner_text() == "Applied 2."
            assert card.locator("#status:focus").count() == 1
            assert card.locator("#also").get_attribute("class") == "also hidden"
            assert card.locator("#actions a.link", has_text="Undo on the review page").count() == 1
            assert card.locator("#actions a.link", has_text="Undo or see the details").count() == 1
            overflow = card.locator("body").evaluate("b => b.scrollWidth - b.clientWidth")
            assert overflow <= 0, overflow
            _shot(page, f"proposal-card-applied-{width}-{theme}")
        finally:
            browser.close()
    assert not errors, errors


@pytest.mark.parametrize("width,theme", [(360, "light"), (1280, "dark")])
def test_the_card_quotes_the_comment_and_offers_no_undo_for_an_account_action(width: int, theme: str) -> None:
    """The social review: a comment delete names the comment it deletes (as the review page does),
    and once applied the card offers no "Undo on the review page", which has no Undo for it."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    action = {
        **PROPOSAL,
        "kind": "action",
        "diff": "Delete your comment on the deck by amy",
        "rows": [
            {
                "kind": "action",
                "action": "comment_delete",
                "label": "Delete comment",
                "text": "Delete your comment on the deck by amy",
                "before_text": "Cut the second Sol Ring.",
                "cannot_undo": True,
            }
        ],
        "deck_waiting": None,
    }
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            page = browser.new_page(viewport={"width": width, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            applied = {**action, "state": "applied", "applied_at": NOW}
            card = _open(
                page,
                card_html(),
                {"structuredContent": applied, "_meta": {}},
                {"get_proposal": applied},
                theme,
            )
            card.locator("#rows li").first.wait_for()
            text = card.locator("#rows").inner_text()
            assert "Delete your comment on the deck by amy: “Cut the second Sol Ring.”" in text, text
            assert card.locator("a.link", has_text="Undo on the review page").count() == 0
            assert card.locator("a.link", has_text="Open in the app or browser").count() == 1
            _shot(page, f"proposal-card-comment-delete-{width}-{theme}")
        finally:
            browser.close()
    assert not errors, errors


@pytest.mark.parametrize("width,theme", [(360, "light"), (1280, "dark")])
def test_the_card_says_who_changed_a_change_and_where_to_approve_another_apps(width: int, theme: str) -> None:
    """G16, Q134 item 5: an amended change says which app changed it and what. A change another
    app made says where it can be approved, without calling the card the review page."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    amended = {
        **PROPOSAL,
        "version": 3,
        "changed_by": "app: ChatGPT (c2)",
        "what_changed": "Added Delver of Secrets",
        "deck_waiting": None,
    }
    other = {**PROPOSAL, "card_other_app": True, "deck_waiting": None}
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            for data, want in (
                (amended, "Version 3, changed by ChatGPT: Added Delver of Secrets."),
                (
                    other,
                    "Claude made this change, so this card can't approve it. Approve it on Claude's card "
                    "or in your browser.",
                ),
            ):
                page = browser.new_page(viewport={"width": width, "height": 900})
                errors: list[str] = []
                page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
                page.route("https://api.scryfall.com/**", lambda r: r.abort())
                card = _open(page, card_html(), {"structuredContent": data, "_meta": {}}, {}, theme)
                card.locator("#note:not(.hidden)").wait_for()
                note = card.locator("#note").inner_text()
                assert want in note, note
                assert "here on the review page" not in note
                assert "version 3" not in card.locator("#foot").inner_text()  # said once, above
                _shot(
                    page,
                    f"proposal-card-changed-{width}-{theme}"
                    if data is amended
                    else f"proposal-card-other-app-{width}-{theme}",
                )
                assert not errors, errors
                page.close()
        finally:
            browser.close()


def test_a_change_decided_on_the_waiting_card_is_not_reported_as_not_sent() -> None:
    """The waiting card's Apply all sends its whole signed set; one already rejected on the card
    comes back as "no longer waiting" and is left out of the result list, not shown as not sent."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    three = {
        **WAITING,
        "waiting": [_line("p1", 1, "+1 Opt"), _line("p2", 2, "+1 Ponder"), _line("p4", 4, "+1 Brainstorm")],
        "count": 3,
        "own_count": 3,
        "other_apps": [],
    }
    meta = {
        APPROVALS_META_KEY: {"p1": "code-1", "p2": "code-2", "p4": "code-4"},
        APPROVAL_SET_META_KEY: {"ids": ["p1", "p2", "p4"], "code": "set-code"},
    }
    done = {
        **DONE,
        "summary": "Applied 2. 1 not sent.",
        "applied": [
            {"proposal_id": "p2", "board_no": 2, "summary": "+1 Ponder"},
            {"proposal_id": "p4", "board_no": 4, "summary": "+1 Brainstorm"},
        ],
        "not_sent": [
            {"proposal_id": "p1", "board_no": 1, "summary": "+1 Opt", "why": "It is no longer waiting."}
        ],
    }
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            page = browser.new_page(viewport={"width": 390, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            replies = {
                "confirm_proposal": {"ok": True, "proposal_id": "p1", "state": "rejected"},
                "confirm_waiting": done,
            }
            card = _open(
                page, cards_html("waiting-card"), {"structuredContent": three, "_meta": meta}, replies
            )
            card.locator("#row-p1 button.danger:not([disabled])").click()
            card.locator("#row-p1 .done").wait_for()
            card.locator("#apply-all:not([disabled])").click()
            assert card.locator("#order li").all_inner_texts() == ["#2 +1 Ponder", "#4 +1 Brainstorm"]
            card.locator("#go:not([disabled])").click()
            card.locator("#result:not(.hidden)").wait_for()
            assert card.locator("#res-list li").all_inner_texts() == [
                "Applied: #2 +1 Ponder",
                "Applied: #4 +1 Brainstorm",
            ]
        finally:
            browser.close()
    assert not errors, errors


@pytest.mark.parametrize("width,theme", [(360, "dark"), (1280, "light")])
def test_approve_reads_the_change_again_and_shows_a_new_wait_first(width: int, theme: str) -> None:
    """Review round 2: a card drawn before the change was held (a restart, another app's step)
    shows the hold when Approve is pressed, and applies only on a second press."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    plain = {**PROPOSAL, "deck_waiting": None}
    now_held = {
        **plain,
        "status": "held",
        "status_reason": "It depends on #1, which failed.",
        "press_reason": "is held because a change it depends on didn't go through",
    }
    applied = {**plain, "state": "applied", "applied_at": NOW}
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            page = browser.new_page(viewport={"width": width, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.route("https://api.scryfall.com/**", lambda r: r.abort())
            card = _open(
                page,
                card_html(),
                {"structuredContent": plain, "_meta": {APPROVAL_META_KEY: "code-2"}},
                {"get_proposal": plain, "confirm_proposal": {"ok": True, **applied}},
                theme,
            )
            card.locator("#approve:not([disabled])").wait_for()
            # held after the card was drawn (its first step failed in a restart)
            page.evaluate("(r) => { window.replies.get_proposal = r; }", now_held)
            card.locator("#approve:not([disabled])").click()
            card.locator("#status.err").wait_for()
            assert card.locator("#status").inner_text() == (
                "This change has changed status since the card was drawn. Read the note above, then "
                "press Approve again if you still want it."
            )
            note = card.locator("#note").inner_text()
            assert "is held because a change it depends on didn't go through, so it needs your press" in note
            assert not _tool_calls(page, "confirm_proposal")
            _shot(page, f"proposal-card-newly-held-{width}-{theme}")
            card.locator("#approve:not([disabled])").click()
            page.wait_for_function(
                "() => window.log.some(m => m.method === 'tools/call'"
                " && m.params.name === 'confirm_proposal')"
            )
            assert _tool_calls(page, "confirm_proposal") == [
                {"proposal_id": "p2", "approval": "code-2", "decision": "approve"}
            ]
        finally:
            browser.close()
    assert not errors, errors


def test_the_cards_size_their_text_in_rem() -> None:
    """Review round 2: the chat cards follow a larger text size (200%), so no text is sized in px."""
    import re as _re

    for html in (card_html(), cards_html("waiting-card")):
        sizes = _re.findall(r"font-size:([^;}]+)", html)
        assert sizes, "no font sizes found"
        assert [x for x in sizes if x.strip().endswith("px") and x.strip() != "14px"] == [], sizes


def test_approve_says_when_it_could_not_check_and_waits_for_a_second_press() -> None:
    """Review round 3: when Approve's re-read gets no answer (the host refuses it), nothing is
    approved on that press and the card says so; the next press approves the change as shown."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    plain = {**PROPOSAL, "deck_waiting": None}
    applied = {**plain, "state": "applied", "applied_at": NOW}
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            page = browser.new_page(viewport={"width": 390, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.route("https://api.scryfall.com/**", lambda r: r.abort())
            # no get_proposal reply: the host refuses the card's read
            card = _open(
                page,
                card_html(),
                {"structuredContent": plain, "_meta": {APPROVAL_META_KEY: "code-2"}},
                {"confirm_proposal": {"ok": True, **applied}},
            )
            card.locator("#approve:not([disabled])").click()
            card.locator("#status.err").wait_for()
            assert card.locator("#status").inner_text() == (
                "This card could not check the change again, so nothing was approved yet. Press "
                "Approve again to approve it as shown here, or use the review page."
            )
            assert not _tool_calls(page, "confirm_proposal")
            card.locator("#approve:not([disabled])").click()
            page.wait_for_function(
                "() => window.log.some(m => m.method === 'tools/call'"
                " && m.params.name === 'confirm_proposal')"
            )
            assert _tool_calls(page, "confirm_proposal") == [
                {"proposal_id": "p2", "approval": "code-2", "decision": "approve"}
            ]
        finally:
            browser.close()
    assert not errors, errors


def test_the_waiting_cards_apply_all_confirm_warns_of_a_held_change() -> None:
    """Q140 item 8 (Ben approved A, 10-17): a held change in the set shows its Held line on the
    Apply all confirm, before anything is sent."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    held = {
        **WAITING,
        "waiting": [
            _line("p1", 1, "+1 Opt"),
            _line("p2", 2, "+1 Ponder", status="held", status_reason="It depends on #3, which failed."),
        ],
        "count": 2,
        "own_count": 2,
        "other_apps": [],
    }
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            page = browser.new_page(viewport={"width": 390, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            card = _open(
                page, cards_html("waiting-card"), {"structuredContent": held, "_meta": WAITING_META}, {}
            )
            card.locator("#apply-all:not([disabled])").click()
            assert card.locator("#order li").all_inner_texts() == [
                "#1 +1 Opt",
                "#2 +1 Ponder\nHeld: it depends on #3, which failed.",
            ]
            assert not _tool_calls(page, "confirm_waiting")
        finally:
            browser.close()
    assert not errors, errors
