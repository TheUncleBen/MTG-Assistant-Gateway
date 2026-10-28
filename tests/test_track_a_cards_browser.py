"""Release 0.7.29, approvals and proposals: the waiting changes card and the proposal card's
"Apply all" line in a real browser, driven by a fake host that speaks the MCP Apps protocol.
Nothing is sent before a press; Apply all asks first and sends the set's code."""

from __future__ import annotations

import os
import time

import pytest

from mtg_gateway.approve import APPROVAL_META_KEY, APPROVAL_SET_META_KEY, APPROVALS_META_KEY, card_html
from mtg_gateway.cards import card_html as cards_html

from .test_consent_browser import _chromium_or_skip, _launch

# the proposal card's way to the review page: a grey capsule under the status line
OPEN_CAP = "#status + .open-row a.cap"
OPEN_WORDS = "Open in the app or browser"

pytest.importorskip("playwright")

NOW = int(time.time())

# The host: answers ui/initialize with the theme it is given, pushes the tool result, records
# everything, and answers each tools/call from window.replies[name] (null: the host refuses).
HOST = """<!DOCTYPE html><html><body style="margin:0">
<iframe id="f" sandbox="allow-scripts" style="width:100%;height:10px;border:0"></iframe>
<script>
window.log = []; window.replies = {}; window.theme = "light"; window.caps = null;
var frame = document.getElementById("f");
window.addEventListener("message", (ev) => {
  const m = ev.data; if (!m || m.jsonrpc !== "2.0") return;
  window.log.push(m);
  const send = (x) => frame.contentWindow.postMessage(x, "*");
  if (m.method === "ui/initialize") {
    send({jsonrpc: "2.0", id: m.id, result: {protocolVersion: "2026-01-26",
      hostInfo: {name: "fake", version: "0"},
      hostCapabilities: window.caps || {openLinks: {}, serverTools: {}},
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
            result: {content: [{type: "text", text: "x"}], structuredContent: r, _meta: r.__meta || {}}});
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


def _open(page, html: str, result: dict, replies: dict, theme: str = "light", caps: dict | None = None):
    page.set_content(HOST)
    page.evaluate("(c) => { window.caps = c; }", caps)
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
            # another app's change has its review link only
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
            assert "Changed by ChatGPT" in card.locator("#row-p1").inner_text()
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
                "#1 +1 Opt\nChanged by ChatGPT. Last change: Opt instead of Ponder.",
                "#2 -1 Island <img src=x onerror=alert(1)>",
            ]
            assert not _tool_calls(page, "confirm_waiting")
            _shot(page, "waiting-card-confirm-390")
            card.locator("#go:not([disabled])").click()
            card.locator("#result:not(.hidden)").wait_for()
            # with the status each change showed: one held since is not sent
            assert _tool_calls(page, "confirm_waiting") == [
                {
                    "proposal_ids": ["p1", "p2"],
                    "approval": "set-code",
                    "shown": {"p1": {"status": ""}, "p2": {"status": "needs_look"}},
                }
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
    },
}


# what Apply all applies, one line each: card-only, so in _meta with the set's code (standard 16)
SET_LINES = [
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
]


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
                APPROVAL_SET_META_KEY: {"ids": ["p1", "p2"], "code": "set-code", "lines": SET_LINES},
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
                "Changed by ChatGPT. Last change: Opt instead of Ponder.",
                "#2 +1 Delver of Secrets",
            ]
            _shot(page, f"proposal-card-confirm-{width}-{theme}")
            assert not _tool_calls(page, "confirm_waiting")
            # the card reads the proposal again after the apply
            page.evaluate("(r) => { window.replies.get_proposal = r; }", applied)
            card.locator("#apply-all:not([disabled])", has_text="Apply 2 changes").click()
            card.locator("#status.ok").wait_for()
            assert _tool_calls(page, "confirm_waiting") == [
                {
                    "proposal_ids": ["p1", "p2"],
                    "approval": "set-code",
                    "shown": {"p1": {"status": "held"}, "p2": {"status": ""}},
                }
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
            assert card.locator(OPEN_CAP, has_text=OPEN_WORDS).count() == 1
            _shot(page, f"proposal-card-comment-delete-{width}-{theme}")
        finally:
            browser.close()
    assert not errors, errors


@pytest.mark.parametrize("width,theme", [(360, "light"), (1280, "dark")])
def test_the_card_says_who_changed_a_change_and_where_to_approve_another_apps(width: int, theme: str) -> None:
    """An amended change says which app changed it and what. A change another
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
                (amended, "Changed by ChatGPT. Last change: Added Delver of Secrets."),
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
    """A card drawn before the change was held (a restart, another app's step)
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
            # the Held line says why, once; the next line says what it means
            assert "Held. It depends on #1, which failed." in note and "It needs your press." in note, note
            assert "is held because" not in note, note
            assert not _tool_calls(page, "confirm_proposal")
            _shot(page, f"proposal-card-newly-held-{width}-{theme}")
            card.locator("#approve:not([disabled])").click()
            page.wait_for_function(
                "() => window.log.some(m => m.method === 'tools/call'"
                " && m.params.name === 'confirm_proposal')"
            )
            # the press says the card showed the hold, so the gateway applies it
            assert _tool_calls(page, "confirm_proposal") == [
                {"proposal_id": "p2", "approval": "code-2", "decision": "approve", "shown_status": "held"}
            ]
        finally:
            browser.close()
    assert not errors, errors


def test_the_cards_size_their_text_in_rem() -> None:
    """The chat cards follow a larger text size (200%), so no text is sized in px."""
    import re as _re

    for html in (card_html(), cards_html("waiting-card")):
        sizes = _re.findall(r"font-size:([^;}]+)", html)
        assert sizes, "no font sizes found"
        assert [x for x in sizes if x.strip().endswith("px") and x.strip() != "14px"] == [], sizes


def test_approve_says_when_it_could_not_check_and_waits_for_a_second_press() -> None:
    """When Approve's re-read gets no answer (the host refuses it), nothing is
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
                {"proposal_id": "p2", "approval": "code-2", "decision": "approve", "shown_status": ""}
            ]
        finally:
            browser.close()
    assert not errors, errors


NOW_HELD_TEXT = (
    "This change is now held, so nothing was approved. Read the note above, then press Approve "
    "again if you still want it."
)


@pytest.mark.parametrize(
    "width,theme,text",
    [(344, "light", "200%"), (360, "dark", "100%"), (884, "dark", "200%"), (1280, "light", "100%")],
)
def test_a_hold_set_after_the_card_was_drawn_is_shown_when_the_card_cannot_check(
    width: int, theme: str, text: str
) -> None:
    """The host refuses the card's read, so the second press
    sends the status the card showed. The gateway refuses a change held since, the card shows the
    hold with nothing approved, and a press after that (the card now shows "Held") applies it."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    # drawn while the change it replaces still waited, in Semi-auto as a low-risk change
    plain = {
        **PROPOSAL,
        "deck_waiting": None,
        "replace_note": "This replaces #1, which still waits.",
        "approval_mode": "semi",
        "risk": "low",
        "risk_reason": "adds 1 card",
        "assistant_may_apply": True,
    }
    refused = {
        "ok": False,
        "error": "now_held",
        "message": "This change is now held, so nothing was sent to Archidekt. It would replace #1, "
        "which was applied. The user can read why on the card and still apply it there.",
        "press_reason": "would replace #1, which was applied",
        "replace_note": "",
        "assistant_may_apply": False,
        "state": "pending",
        "version": 1,
        "status": "held",
        "status_reason": "It would replace #1, which was applied.",
        "status_at": NOW,
    }
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            page = browser.new_page(viewport={"width": width, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.route("https://api.scryfall.com/**", lambda r: r.abort())
            # no get_proposal reply: the host refuses the card's read
            card = _open(
                page,
                card_html(),
                {"structuredContent": plain, "_meta": {APPROVAL_META_KEY: "code-2"}},
                {"confirm_proposal": refused},
                theme,
            )
            frame = page.frames[1]
            frame.evaluate("(t) => { document.documentElement.style.fontSize = t; }", text)
            card.locator("#approve:not([disabled])").click()
            card.locator("#status.err").wait_for()
            card.locator("#approve:not([disabled])").click()
            card.locator("#status.err", has_text="now held").wait_for()
            assert card.locator("#status").inner_text() == NOW_HELD_TEXT
            body = card.locator("#card").inner_text()
            assert "Held. It would replace #1, which was applied." in body
            # said once: the Held line says why, the next line only what it means
            assert "It needs your press." in body and "This one would replace" not in body, body
            # no line the hold made untrue stays on the card
            assert "still waits" not in body and "Low risk (" not in body, body
            assert _tool_calls(page, "confirm_proposal") == [
                {"proposal_id": "p2", "approval": "code-2", "decision": "approve", "shown_status": ""}
            ]
            box = frame.evaluate(
                "() => ({page: document.documentElement.clientWidth,"
                " scroll: document.documentElement.scrollWidth})"
            )
            assert box["scroll"] <= box["page"], box
            _shot(page, f"proposal-card-now-held-{width}-{theme}-{text.rstrip('%')}")
            # the card shows the hold now, so the member's next press (read refused again, then
            # once more) says so to the gateway, which applies it
            page.evaluate(
                "(r) => { window.replies.confirm_proposal = r; }", {**plain, "ok": True, "state": "applied"}
            )
            card.locator("#approve:not([disabled])").click()
            card.locator("#status.err", has_text="could not check").wait_for()
            card.locator("#approve:not([disabled])").click()
            card.locator("#status.ok").wait_for()
            # with the time the hold it now shows was set, from the refusal
            assert _tool_calls(page, "confirm_proposal")[-1] == {
                "proposal_id": "p2",
                "approval": "code-2",
                "decision": "approve",
                "shown_status": "held",
                "shown_status_at": NOW,
            }
        finally:
            browser.close()
    assert not errors, errors


WAITING_NOW_HELD_TEXT = (
    "This change is now held, so nothing was approved. Read why on its row, then press Approve "
    "again if you still want it."
)


@pytest.mark.parametrize(
    "width,theme,text",
    [(344, "light", "200%"), (360, "dark", "100%"), (884, "dark", "200%"), (1280, "light", "100%")],
)
def test_approve_on_the_waiting_card_shows_a_hold_set_after_it_was_drawn(
    width: int, theme: str, text: str
) -> None:
    """Approve on a row sends the status the row showed. The gateway refuses a change held since,
    the row shows the hold with nothing approved, and the next press says it showed the hold."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    refused = {
        "ok": False,
        "error": "now_held",
        "message": "This change is now held, so nothing was sent to Archidekt. It would replace #3, "
        "which was applied. The user can read why on the card and still apply it there.",
        "state": "pending",
        "version": 1,
        "status": "held",
        "status_reason": "It would replace #3, which was applied.",
        "status_at": NOW,
        "replace_note": "",
    }
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            page = browser.new_page(viewport={"width": width, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            # drawn while the change it replaces still waited
            drawn = {
                **WAITING,
                "waiting": [
                    {**WAITING["waiting"][0], "link_note": "This replaces #3, which still waits."},
                    *WAITING["waiting"][1:],
                ],
            }
            card = _open(
                page,
                cards_html("waiting-card"),
                {"structuredContent": drawn, "_meta": WAITING_META},
                {"confirm_proposal": refused},
                theme,
            )
            frame = page.frames[1]
            frame.evaluate("(t) => { document.documentElement.style.fontSize = t; }", text)
            card.locator("#row-p1 button.primary:not([disabled])").click()
            card.locator("#status.err").wait_for()
            assert _tool_calls(page, "confirm_proposal") == [
                {"proposal_id": "p1", "approval": "code-1", "decision": "approve", "shown_status": ""}
            ]
            assert card.locator("#status").inner_text() == WAITING_NOW_HELD_TEXT
            row = card.locator("#row-p1").inner_text()
            assert "Held" in row and "It would replace #3, which was applied." in row, row
            assert "still waits" not in row, row  # no line the hold made untrue
            assert card.locator("#row-p1 .done").count() == 0  # still waiting: Approve stays
            box = frame.evaluate(
                "() => ({page: document.documentElement.clientWidth,"
                " scroll: document.documentElement.scrollWidth})"
            )
            assert box["scroll"] <= box["page"], box
            _shot(page, f"waiting-card-now-held-{width}-{theme}-{text.rstrip('%')}")
            # the row shows the hold now: the member's next press says so, and it applies
            page.evaluate("(r) => { window.replies.confirm_proposal = r; }", {"ok": True, "state": "applied"})
            card.locator("#row-p1 button.primary:not([disabled])").click()
            card.locator("#row-p1 .done.ok").wait_for()
            assert _tool_calls(page, "confirm_proposal")[-1] == {
                "proposal_id": "p1",
                "approval": "code-1",
                "decision": "approve",
                "shown_status": "held",
                "shown_status_at": NOW,
            }
        finally:
            browser.close()
    assert not errors, errors


@pytest.mark.parametrize(
    "width,theme",
    [(344, "light"), (344, "dark"), (884, "light"), (884, "dark"), (1280, "light"), (1280, "dark")],
)
def test_apply_all_on_the_proposal_card_lists_what_it_did_not_send_and_why(width: int, theme: str) -> None:
    """A change held after the card was drawn is left out of Apply all; the card lists it with
    why, as the waiting card and the Proposals page's result do (200% text)."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    why = (
        "Not sent: it is held for a reason that came up after you saw it. It would replace #3, "
        "which was applied. It is still waiting."
    )
    result = {
        **DONE,
        "summary": "Applied 1. 1 not sent.",
        "applied": [{"proposal_id": "p2", "board_no": 2, "summary": "+1 Delver of Secrets"}],
        "not_sent": [{"proposal_id": "p1", "board_no": 1, "summary": "+1 Opt", "why": why}],
    }
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            page = browser.new_page(viewport={"width": width, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.route("https://api.scryfall.com/**", lambda r: r.abort())
            meta = {
                APPROVAL_META_KEY: "code-2",
                APPROVAL_SET_META_KEY: {"ids": ["p1", "p2"], "code": "set-code", "lines": SET_LINES},
            }
            card = _open(
                page,
                card_html(),
                {"structuredContent": PROPOSAL, "_meta": meta},
                {"confirm_waiting": result, "get_proposal": PROPOSAL},
                theme,
            )
            frame = page.frames[1]
            frame.evaluate("() => { document.documentElement.style.fontSize = '200%'; }")
            card.locator("#apply-all:not([disabled])").click()
            # the card reads the proposal again after the apply
            page.evaluate("(r) => { window.replies.get_proposal = r; }", {**PROPOSAL, "state": "applied"})
            card.locator("#apply-all:not([disabled])", has_text="Apply 2 changes").click()
            card.locator("#status.ok").wait_for()
            assert card.locator("#status").inner_text() == "Applied 1. 1 not sent."
            assert card.locator("#not-sent li").all_inner_texts() == ["#1 +1 Opt: " + why]
            assert card.locator("#not-sent").is_visible()
            box = frame.evaluate(
                "() => ({page: document.documentElement.clientWidth,"
                " scroll: document.documentElement.scrollWidth})"
            )
            assert box["scroll"] <= box["page"], box
            _shot(page, f"proposal-card-not-sent-{width}-{theme}")
        finally:
            browser.close()
    assert not errors, errors


@pytest.mark.parametrize("decision", ["approve", "reject", "failed", "expired", "read_again"])
def test_the_proposal_cards_not_sent_list_goes_when_its_own_change_is_decided(decision: str) -> None:
    """Apply all left out the card's own change; the member then approves or rejects it on the
    card: the list that said it is still waiting goes."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    why = (
        "Not sent: it is held for a reason that came up after you saw it. It would replace #3, "
        "which was applied. It is still waiting."
    )
    result = {
        **DONE,
        "summary": "Applied 1. 1 not sent.",
        "applied": [{"proposal_id": "p1", "board_no": 1, "summary": "+1 Opt"}],
        "not_sent": [{"proposal_id": "p2", "board_no": 2, "summary": "+1 Delver of Secrets", "why": why}],
    }
    state = {
        "approve": "applied",
        "reject": "rejected",
        "failed": "failed",
        "expired": "expired",
        "read_again": "rejected",
    }[decision]
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            page = browser.new_page(viewport={"width": 390, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.route("https://api.scryfall.com/**", lambda r: r.abort())
            meta = {
                APPROVAL_META_KEY: "code-2",
                APPROVAL_SET_META_KEY: {"ids": ["p1", "p2"], "code": "set-code", "lines": SET_LINES},
            }
            card = _open(
                page,
                card_html(),
                {"structuredContent": PROPOSAL, "_meta": meta},
                {"confirm_waiting": result, "get_proposal": PROPOSAL},
            )
            card.locator("#apply-all:not([disabled])").click()
            # the read after the apply: still waiting, with its own code again
            page.evaluate(
                "(r) => { window.replies.get_proposal = r; }",
                {**PROPOSAL, "deck_waiting": None, "__meta": {APPROVAL_META_KEY: "code-2"}},
            )
            card.locator("#apply-all:not([disabled])", has_text="Apply 2 changes").click()
            card.locator("#status.ok").wait_for()
            assert card.locator("#not-sent li").all_inner_texts() == ["#2 +1 Delver of Secrets: " + why]
            reply = {**PROPOSAL, "deck_waiting": None, "ok": True, "state": state}
            if decision in ("failed", "expired"):
                # the press is refused because the change closed (its apply failed, or it expired)
                reply = {"ok": False, "error": decision, "message": "Not applied.", "state": state}
            page.evaluate(
                "(r) => { window.replies.confirm_proposal = r; }",
                reply,
            )
            if decision == "read_again":
                # the chat app hands the card the change again, now closed elsewhere
                page.evaluate(
                    "(r) => document.getElementById('f').contentWindow.postMessage("
                    "{jsonrpc: '2.0', method: 'ui/notifications/tool-result',"
                    " params: {structuredContent: r}}, '*')",
                    {**PROPOSAL, "deck_waiting": None, "state": state},
                )
            elif decision in ("approve", "failed", "expired"):
                card.locator("#approve:not([disabled])").click()
            else:
                card.locator("#reject:not([disabled])").click()
                card.locator("#reject-go").click()
            card.locator("#state", has_text=state.capitalize()).wait_for()
            assert card.locator("#not-sent li").count() == 0
            assert not card.locator("#not-sent").is_visible()
        finally:
            browser.close()
    assert not errors, errors


def test_the_waiting_cards_result_says_not_sent_once() -> None:
    """The gateway's reasons start with "Not sent: ", which the card's line already says."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    why = (
        "Not sent: it is held for a reason that came up after you saw it. It would replace #3, "
        "which was applied. It is still waiting."
    )
    result = {**DONE, "not_sent": [{"proposal_id": "p2", "board_no": 2, "summary": "-1 Island", "why": why}]}
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            page = browser.new_page(viewport={"width": 390, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            card = _open(
                page,
                cards_html("waiting-card"),
                {"structuredContent": WAITING, "_meta": WAITING_META},
                {"confirm_waiting": result},
            )
            card.locator("#apply-all:not([disabled])").click()
            card.locator("#go:not([disabled])").click()
            card.locator("#result:not(.hidden)").wait_for()
            assert card.locator("#res-list li").all_inner_texts()[-1] == (
                "Not sent: #2 -1 Island. It is held for a reason that came up after you saw it. It would "
                "replace #3, which was applied. It is still waiting."
            )
        finally:
            browser.close()
    assert not errors, errors


def test_the_waiting_cards_apply_all_confirm_warns_of_a_held_change() -> None:
    """A held change in the set shows its Held line on the
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


@pytest.mark.parametrize("width", [344, 360, 390])
@pytest.mark.parametrize("text", ["100%", "200%"])
def test_the_no_press_badge_fits_a_phone_at_any_text_size(width: int, text: str) -> None:
    """A high-risk change Full auto applies keeps its High label and shows a plain "No press
    needed" note beside it (F14, Ben 21 Oct); on the Fold cover (344 px) and a phone (390 px), at
    100% and 200% text, both stay inside the card and the title keeps room. The sizes are printed
    for the record."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            page = browser.new_page(viewport={"width": width, "height": 900})
            page.route("https://api.scryfall.com/**", lambda r: r.abort())
            sc = {
                **PROPOSAL,
                "deck_waiting": None,
                "risk": "high",
                "risk_reason": "changes 8 card rows",
                "approval_mode": "auto",
                "assistant_may_apply": True,
            }
            card = _open(page, card_html(), {"structuredContent": sc}, {"get_proposal": sc})
            badge = card.locator("#risk")
            badge.wait_for()
            frame = page.frames[1]
            frame.evaluate("(t) => { document.documentElement.style.fontSize = t; }", text)
            page.wait_for_timeout(100)
            assert badge.inner_text().lower() == "high"
            assert "high" in (badge.get_attribute("class") or "")
            note = card.locator("#nopress")
            assert note.inner_text().lower() == "no press needed"
            assert "hidden" not in (note.get_attribute("class") or "")
            assert badge.locator("svg").count() == 1 and note.locator("svg").count() == 1  # their icons
            box = frame.evaluate(
                "() => { const b = document.getElementById('risk').getBoundingClientRect();"
                " const n = document.getElementById('nopress').getBoundingClientRect();"
                " const t = document.getElementById('title').getBoundingClientRect();"
                " const meets = (a) => a.left < t.right && t.left < a.right"
                " && a.top < t.bottom && t.top < a.bottom;"
                " return {right: Math.max(b.right, n.right), left: b.left, width: b.width, title: t.width,"
                " overlap: meets(b) || meets(n), page: document.documentElement.clientWidth,"
                " scroll: document.documentElement.scrollWidth}; }"
            )
            print(f"D-4 badge at {width}px, {text} text: {box}")
            assert box["right"] <= box["page"] + 0.5 and box["scroll"] <= box["page"], box
            assert box["title"] >= 100 and not box["overlap"], box
            _shot(page, f"proposal-card-nopress-{width}-{text.rstrip('%')}")
        finally:
            browser.close()


@pytest.mark.parametrize("width,theme", [(380, "light"), (380, "dark"), (700, "light"), (700, "dark")])
def test_a_low_risk_copy_is_called_a_change_not_an_edit(width: int, theme: str) -> None:
    """A private copy of a deck that Semi-auto may apply reads "Low risk (copies a deck …)":
    a copy edits nothing, so "edit" was wrong. The note stays inside the card."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    clone = {
        **PROPOSAL,
        "kind": "clone",
        "deck_waiting": None,
        "risk": "low",
        "risk_reason": "copies a deck and changes nothing that exists",
        "approval_mode": "semi",
        "assistant_may_apply": True,
    }
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            page = browser.new_page(viewport={"width": width, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.route("https://api.scryfall.com/**", lambda r: r.abort())
            card = _open(page, card_html(), {"structuredContent": clone, "_meta": {}}, {}, theme)
            card.locator("#note:not(.hidden)").wait_for()
            note = card.locator("#note").inner_text()
            assert (
                "Low risk (copies a deck and changes nothing that exists): your assistant may apply this "
                "without asking." in note
            ), note
            assert "Low-risk edit" not in note
            frame = page.frames[1]
            fit = frame.evaluate(
                "() => { const n = document.getElementById('note').getBoundingClientRect();"
                " return {right: n.right, page: document.documentElement.clientWidth,"
                " scroll: document.documentElement.scrollWidth}; }"
            )
            assert fit["right"] <= fit["page"] + 0.5 and fit["scroll"] <= fit["page"], fit
            _shot(page, f"proposal-card-clone-{width}-{theme}")
            assert not errors, errors
        finally:
            browser.close()


def test_apply_all_on_the_waiting_card_posts_nothing_as_the_user_and_a_row_approve_says_what_it_did() -> None:
    """Owner decision 0.7.46: no card posts "Approved, go on" as the person's message any more,
    neither after a row's Approve nor after Apply all (done or still applying); the assistant is
    told the outcome through the model context. The row's result is the gateway's own words for
    what it did (a like is not a deck that matches)."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    caps = {"openLinks": {}, "serverTools": {}, "message": {"text": {}}}
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            page = browser.new_page(viewport={"width": 390, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            liked = {
                "ok": True,
                "proposal_id": "p1",
                "state": "applied",
                "applied_text": "The deck is liked on Archidekt.",
            }
            card = _open(
                page,
                cards_html("waiting-card"),
                {"structuredContent": WAITING, "_meta": WAITING_META},
                {"confirm_proposal": liked},
                caps=caps,
            )
            card.locator("#row-p1 button:not(.danger):not([disabled])").first.click()
            card.locator("#row-p1 .done").wait_for()
            page.wait_for_function("() => window.log.some(m => m.method === 'ui/update-model-context')")
            assert card.locator("#status").inner_text() == "Applied. The deck is liked on Archidekt."
            page.wait_for_timeout(300)
            assert not [m for m in page.evaluate("() => window.log") if m.get("method") == "ui/message"]
            page.close()
            page = browser.new_page(viewport={"width": 390, "height": 900})
            page.on("pageerror", lambda e: errors.append(str(e)))
            card = _open(
                page,
                cards_html("waiting-card"),
                {"structuredContent": WAITING, "_meta": WAITING_META},
                {"confirm_waiting": DONE},
                caps=caps,
            )
            card.locator("#apply-all:not([disabled])").click()
            card.locator("#go:not([disabled])").click()
            card.locator("#result:not(.hidden)").wait_for()
            page.wait_for_function("() => window.log.some(m => m.method === 'ui/update-model-context')")
            page.wait_for_timeout(300)
            log = page.evaluate("() => window.log")
            assert not [m for m in log if m.get("method") == "ui/message"]
            page.close()
            # a large batch still applying: the assistant is told while it runs and when it ends,
            # and nothing is posted as the user either time
            page = browser.new_page(viewport={"width": 390, "height": 900})
            page.on("pageerror", lambda e: errors.append(str(e)))
            running = {**DONE, "state": "applying", "summary": "Applying 1.", "applied": [], "not_sent": []}
            card = _open(
                page,
                cards_html("waiting-card"),
                {"structuredContent": WAITING, "_meta": WAITING_META},
                {"confirm_waiting": running, "get_apply_result": DONE},
                caps=caps,
            )
            card.locator("#apply-all:not([disabled])").click()
            card.locator("#go:not([disabled])").click()
            card.locator("#status", has_text="Applied 1.").wait_for(timeout=8000)
            page.wait_for_timeout(300)
            log = page.evaluate("() => window.log")
            assert len([m for m in log if m.get("method") == "ui/update-model-context"]) == 2, log
            assert not [m for m in log if m.get("method") == "ui/message"]
        finally:
            browser.close()
    assert not errors, errors
