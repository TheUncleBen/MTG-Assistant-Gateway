"""The proposal card (static/proposal-card.html) in a real browser, driven by a fake host that
speaks the MCP Apps postMessage protocol: it draws the proposal, its buttons call
confirm_proposal with the one-time code, and nothing but a read-only get_proposal (to catch a
proposal closed since it was made) is called before the person presses."""

from __future__ import annotations

import json
import time

import pytest

from mtg_gateway.approve import APPROVAL_META_KEY, card_html

from .test_cards_browser import HOST as SIZED_HOST
from .test_cards_browser import PNG
from .test_consent_browser import _chromium_or_skip, _launch

pytest.importorskip("playwright")

PROPOSAL = {
    "ok": True,
    "proposal_id": "p-1",
    "kind": "edit",
    "deck_id": "42",
    "deck_name": "Sample <b>Deck</b>",
    "state": "pending",
    "diff": "+1 Sol Ring\n-1 Island",
    "rows": [
        {"kind": "add", "name": "Sol Ring", "qty": 1, "category": "Ramp"},
        {"kind": "remove", "name": "Island <img src=x onerror=alert(1)>", "qty": 1},
        {"kind": "category", "name": "Counterspell", "before": "Main", "after": "Maybeboard", "leaves": 1},
    ],
    "created_by": "app: Claude (abc)",
    "expires_at": int(time.time()) + 3600,
    "review_url": "https://mtg.test/proposals/p-1",
    "writes_enabled": True,
    "next_step": "...",
    "result": None,
}

# The host side of the protocol, in the parent page: answers ui/initialize, pushes the tool
# result, records everything the card sends, and answers tools/call the way the gateway would.
HOST = """<!DOCTYPE html><html><body>
<iframe id="f" sandbox="allow-scripts" style="width:600px;height:10px;border:0"></iframe>
<script>
window.log = [];
window.confirmReply = null;  // set by the test: the structuredContent confirm_proposal answers with
// get_proposal's structuredContent; null: the gateway answers with what the card drew (the change
// is as shown); "refuse": the host refuses the call, as a host may refuse a card's tools/call
window.getReply = null;
var frame = document.getElementById("f");
window.addEventListener("message", (ev) => {
  const m = ev.data; if (!m || m.jsonrpc !== "2.0") return;
  window.log.push(m);
  const send = (x) => frame.contentWindow.postMessage(x, "*");
  if (m.method === "ui/initialize") {
    send({jsonrpc: "2.0", id: m.id, result: {
      protocolVersion: "2026-01-26", hostInfo: {name: "fake", version: "0"},
      hostCapabilities: window.hostCaps || {openLinks: {}, serverTools: {}},
      hostContext: {theme: "dark", platform: "web",
                    styles: {variables: {"--color-text-primary": "rgb(1, 2, 3)"}},
                    safeAreaInsets: {top: 0, right: 0, bottom: 0, left: 0}}}});
  } else if (m.method === "ui/notifications/initialized") {
    send({jsonrpc: "2.0", method: "ui/notifications/tool-result", params: window.toolResult});
  } else if (m.method === "tools/call" && m.params.name === "get_proposal") {
    const sc = window.getReply === null ? (window.toolResult || {}).structuredContent : window.getReply;
    if (sc === "refuse") send({jsonrpc: "2.0", id: m.id, error: {code: -32000, message: "blocked"}});
    else send({jsonrpc: "2.0", id: m.id,
               result: {content: [{type: "text", text: JSON.stringify(sc)}], structuredContent: sc}});
  } else if (m.method === "tools/call") {
    const sc = window.confirmReply;
    const text = JSON.stringify(sc);
    send({jsonrpc: "2.0", id: m.id,
          result: {content: [{type: "text", text: text}], structuredContent: sc, isError: false}});
  } else if (m.method === "ui/open-link" || m.method === "ui/update-model-context" ||
             m.method === "ui/message") {
    send({jsonrpc: "2.0", id: m.id, result: {}});
  } else if (m.id !== undefined && m.method) {
    send({jsonrpc: "2.0", id: m.id, error: {code: -32601, message: "nope"}});
  }
});
// A request the host makes of the card; the card must answer it.
window.teardown = () => { frame.contentWindow.postMessage(
  {jsonrpc: "2.0", id: 999, method: "ui/resource-teardown", params: {reason: "test"}}, "*"); };
</script></body></html>"""


def _open(
    page,
    result: dict,
    confirm_reply: dict | None = None,
    get_reply: dict | None = None,
    *,
    arm_ms: int | None = None,
    host_caps: dict | None = None,
    openai: bool = False,
):
    """The card in the host page. ``arm_ms``: a longer arming delay than the card's 700 ms, for a
    test that reads the disabled buttons, so a loaded machine cannot run past it first.
    ``host_caps``: the host's capabilities instead of the default ones. ``openai``: the card's
    window also has ChatGPT's ``window.openai.sendFollowUpMessage``, which logs its call."""
    html = card_html()
    if openai:
        stub = (
            "<script>window.openai = {sendFollowUpMessage: function (a) { parent.postMessage("
            '{jsonrpc: "2.0", method: "openai/sendFollowUpMessage", params: a}, "*"); }};</script>'
        )
        html = html.replace("<head>", "<head>" + stub, 1)
    if arm_ms is not None:
        assert "var ARM_DELAY_MS = 700;" in html
        html = html.replace("var ARM_DELAY_MS = 700;", f"var ARM_DELAY_MS = {arm_ms};")
    # the card's pictures come from Scryfall: a stand-in picture keeps the test offline
    page.route("https://api.scryfall.com/**", lambda r: r.fulfill(content_type="image/png", body=PNG))
    page.set_content(HOST)
    page.evaluate("(c) => { window.hostCaps = c; }", host_caps)
    page.evaluate("(r) => { window.toolResult = r; }", result)
    page.evaluate("(r) => { window.confirmReply = r; }", confirm_reply)
    page.evaluate("(r) => { window.getReply = r; }", get_reply)
    page.evaluate("(html) => { document.getElementById('f').srcdoc = html; }", html)
    page.wait_for_function(
        "() => window.log.some(m => m.method === 'ui/notifications/initialized')", timeout=10_000
    )
    return page.frame_locator("#f")


def _calls(page, method: str) -> list[dict]:
    return [m for m in page.evaluate("() => window.log") if m.get("method") == method]


def _confirms(page) -> list[dict]:
    """The card's confirm_proposal calls (its only writes)."""
    return [m for m in _calls(page, "tools/call") if m["params"]["name"] == "confirm_proposal"]


def test_card_renders_and_approve_calls_confirm_with_the_code() -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            page = browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            applied = {
                **PROPOSAL,
                "state": "applied",
                "result": {"deck_url": "https://archidekt.com/decks/42"},
            }
            card = _open(
                page,
                {"structuredContent": PROPOSAL, "_meta": {APPROVAL_META_KEY: "code-123"}},
                applied,
                arm_ms=4000,
            )
            # Drawn from the fields, never as markup: the hostile names appear as text.
            card.locator("#title").wait_for()
            # The buttons start disabled for a moment after drawing (ARM_DELAY_MS, 4 s here so a
            # loaded machine cannot run past it): read that first, before the other assertions.
            approve = card.locator("#approve")
            assert approve.is_disabled() and card.locator("#reject").is_disabled()
            assert card.locator("#title").inner_text() == "Change to Sample <b>Deck</b>"
            assert card.locator("#state").inner_text().lower() == "waiting"  # the status word
            rows = card.locator("ul.changes li:not(.group)")
            assert (
                rows.count() == 3 and card.locator("ul.changes li.group").count() == 3
            )  # one heading per kind
            assert "Island <img src=x onerror=alert(1)>" in rows.nth(1).inner_text()
            assert card.locator("ul.changes img.pic").count() == 3  # one small Scryfall image per card row
            assert "leaves the deck" in rows.nth(2).inner_text()
            assert (
                "+1 card" in card.locator("#summary").inner_text()
                and "1 card" in card.locator("#summary").inner_text()
            )
            # The host's theme and style variables were applied.
            assert page.frame_locator("#f").locator("html").get_attribute("data-theme") == "dark"
            assert card.locator("#title").evaluate("e => getComputedStyle(e).color") == "rgb(1, 2, 3)"
            # Buttons: Approve and Reject come live once the arming delay has passed.
            card.locator("#approve:enabled").wait_for(timeout=10_000)
            assert approve.is_enabled() and card.locator("#reject").is_enabled()
            assert _confirms(page) == []  # nothing was confirmed by itself
            reads = _calls(page, "tools/call")  # one read-only look at the proposal's state, at most
            assert len(reads) <= 1 and all(
                m["params"] == {"name": "get_proposal", "arguments": {"proposal_id": "p-1"}} for m in reads
            )
            assert _calls(page, "ui/notifications/size-changed"), "the card reports its size"
            # Press Approve: one confirm_proposal call with the proposal id, the code and the
            # status the card shows (none here).
            approve.click()
            page.wait_for_function(
                "() => window.log.some(m => m.params && m.params.name === 'confirm_proposal')",
                timeout=5_000,
            )
            calls = _confirms(page)
            assert len(calls) == 1
            assert calls[0]["params"] == {
                "name": "confirm_proposal",
                "arguments": {
                    "proposal_id": "p-1",
                    "approval": "code-123",
                    "decision": "approve",
                    "shown_status": "",
                },
            }
            card.locator("#status.ok").wait_for(timeout=5_000)
            assert "Applied" in card.locator("#status").inner_text()
            assert card.locator("#state").inner_text().lower() == "applied"
            assert card.locator("#approve").count() == 0  # no second press
            assert card.locator("a.link", has_text="Open the deck on Archidekt").count() == 1
            # The model is told what happened, in words, through the host.
            ctx = _calls(page, "ui/update-model-context")
            assert ctx and "pressed Approve" in ctx[0]["params"]["content"][0]["text"]
            # A host request to tear down gets an answer.
            page.evaluate("() => window.teardown()")
            page.wait_for_function("() => window.log.some(m => m.id === 999 && 'result' in m)", timeout=5_000)
            assert not errors, errors
        finally:
            browser.close()


def test_big_edit_shows_a_summary_first_and_the_auto_modes_hide_the_buttons() -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    rows = [{"kind": "add", "name": f"Card {i}", "qty": 1} for i in range(9)] + [
        {"kind": "remove", "name": f"Old {i}", "qty": 1} for i in range(3)
    ]
    big = {
        **PROPOSAL,
        "rows": rows,
        "risk": "high",
        "risk_reason": "changes 12 rows",
        "approval_mode": "semi",
    }
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            page = browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            card = _open(page, {"structuredContent": big, "_meta": {APPROVAL_META_KEY: "code-123"}})
            card.locator("#title").wait_for()
            # Summary first: the counts, the risk, the reason; then only the first rows.
            assert "12 rows" in card.locator("#summary").inner_text()
            assert card.locator("#risk").inner_text().lower() == "high"  # the risk word
            assert "needs your approval" in card.locator("#note").inner_text()
            assert card.locator("ul.changes li:not(.group):not(.more-row)").count() == 8
            assert card.locator("ul.changes li.group").first.inner_text().lower().startswith("added (9)")
            more = card.locator("#more")
            assert more.inner_text() == "Show all 12 changes"
            more.click()
            card.locator("ul.changes li:not(.group):not(.more-row)").nth(11).wait_for(timeout=5_000)
            assert card.locator("#more").count() == 0
            assert card.locator("ul.changes li.group").count() == 2  # Added, Removed
            assert card.locator("#approve").count() == 1  # high risk in semi mode: the person decides
            # A proposal the assistant may apply itself (auto, or low risk in semi) shows no buttons
            # at all, only the note and the review link.
            auto = {**PROPOSAL, "approval_mode": "auto", "risk": "low", "assistant_may_apply": True}
            card = _open(page, {"structuredContent": auto})
            card.locator("#title").wait_for()
            page.wait_for_timeout(900)
            assert "lets your assistant apply this itself" in card.locator("#note").inner_text()
            assert card.locator("button:visible").count() == 0
            assert card.locator("a.link", has_text="Open in the app or browser").count() == 1
            assert not errors, errors
        finally:
            browser.close()


def test_card_without_a_code_only_offers_the_review_page() -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            page = browser.new_page()
            card = _open(page, {"structuredContent": PROPOSAL})
            card.locator("#title").wait_for()
            page.wait_for_timeout(900)
            assert card.locator("#approve").count() == 0 and card.locator("#reject").count() == 0
            button = card.locator("button", has_text="Approve on the review page")
            assert button.count() == 1
            button.click()
            page.wait_for_function("() => window.log.some(m => m.method === 'ui/open-link')", timeout=5_000)
            assert _calls(page, "ui/open-link")[0]["params"] == {"url": "https://mtg.test/proposals/p-1"}
            assert _confirms(page) == []
            # Reject goes through the same tool with decision=reject, and a refusal is shown.
            card = _open(
                page,
                {"structuredContent": PROPOSAL, "_meta": {APPROVAL_META_KEY: "c"}},
                {
                    "ok": False,
                    "error": "invalid_approval",
                    "message": "This approval code does not belong here.",
                },
            )
            card.locator("#reject").wait_for()
            page.wait_for_timeout(900)
            card.locator("#reject").click()
            # Reject first asks why (optional), so the assistant can do better; nothing is sent yet.
            card.locator("#why:not(.hidden)").wait_for(timeout=5_000)
            assert _confirms(page) == []
            card.locator("#reason").fill("keep the Islands")
            card.locator("#reject-go").click()
            card.locator("#status.err").wait_for(timeout=5_000)
            assert "does not belong" in card.locator("#status").inner_text()
            assert _confirms(page)[0]["params"]["arguments"]["decision"] == "reject"
            assert card.locator("#reject").is_enabled()  # the person may try the other button or the page
            assert card.locator("#why").get_attribute("class") == "why hidden"
            # An error result (the proposal could not be made) shows the message and no buttons.
            card = _open(
                page, {"structuredContent": {"ok": False, "error": "invalid", "message": "bad change"}}
            )
            card.locator("#status.err").wait_for(timeout=5_000)
            assert "bad change" in card.locator("#status").inner_text()
            assert card.locator("button:visible").count() == 0
            assert json.loads(json.dumps(PROPOSAL))["state"] == "pending"
        finally:
            browser.close()


def test_narrow_card_keeps_badges_whole_and_names_readable() -> None:
    """At phone widths the state and risk badges stay one line each and clear of the title (beside
    it, or under it when the title would get less than 10rem), and a
    long change ("Main → Maybeboard (leaves the deck, −1)") wraps beside its action label, never over
    it, instead of squeezing the card name to a letter a line."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    long_name = {
        **PROPOSAL,
        "deck_name": "A very long deck name that goes on " * 2,
        "risk": "low",
        "risk_reason": "3 rows",
    }
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            for width in (320, 344, 360, 390, 420):
                page = browser.new_page(viewport={"width": width + 20, "height": 900})
                page.set_content(HOST.replace("width:600px", f"width:{width}px"))
                page.evaluate(
                    "(r) => { window.toolResult = r; }",
                    {"structuredContent": long_name, "_meta": {APPROVAL_META_KEY: "c"}},
                )
                page.evaluate("(html) => { document.getElementById('f').srcdoc = html; }", card_html())
                page.wait_for_function(
                    "() => window.log.some(m => m.method === 'ui/notifications/initialized')", timeout=10_000
                )
                card = page.frame_locator("#f")
                card.locator("#risk").wait_for()
                geo = page.frames[1].evaluate(
                    """() => {
  const h = document.getElementById('title').getBoundingClientRect();
  const b = [...document.querySelectorAll('.badges .badge')].map(e => e.getBoundingClientRect());
  const n = document.querySelector('ul.changes li.category .name').getBoundingClientRect();
  // measure the text itself: a squeezed grid cell can be narrower than the words it overflows with
  const ink = (sel) => { const r = document.createRange();
    r.selectNodeContents(document.querySelector(sel)); return r.getBoundingClientRect(); };
  const a = ink('ul.changes li.category .act');
  const q = ink('ul.changes li.category .qty');
  const apart = a.right <= q.left || q.right <= a.left || a.bottom <= q.top || q.bottom <= a.top;
  return {titleRight: h.right, titleBottom: h.bottom, badges: b.map(r => [r.left, r.height, r.top]),
          nameWidth: n.width,
          actQtyApart: apart, act: [a.left, a.right, a.top, a.bottom],
          qty: [q.left, q.right, q.top, q.bottom]};
}"""
                )
                # the action label and the change beside it never overlap (seen at 390 px)
                assert geo["actQtyApart"], (width, geo)
                assert all(h <= 26 for _, h, _ in geo["badges"]), (width, geo)
                assert all(
                    left >= geo["titleRight"] or top >= geo["titleBottom"] for left, _, top in geo["badges"]
                ), (width, geo)
                assert geo["nameWidth"] > 80, (width, geo)
                page.close()
        finally:
            browser.close()


def test_rows_read_on_a_phone_with_no_empty_column_and_the_footer_names_the_app() -> None:
    """At 380 px a row is two lines (the action with its count or change, then the card), a
    row with no picture (a setting, or a picture that did not load) leaves no empty column, tag
    changes say what is added and taken off, and the footer names the app, never its client id."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    details = {
        **PROPOSAL,
        "created_by": "app: ChatGPT (cid-0123456789abcdef)",
        "rows": [
            {"kind": "add", "name": "Arcane Signet", "qty": 1},
            {"kind": "change", "name": "Forest", "before": 15, "after": 13},
            {
                "kind": "detail",
                "field": "tags",
                "before": "budget",
                "after": "ramp, landfall",
                "added": ["ramp", "landfall"],
                "removed": ["budget"],
            },
            {"kind": "detail", "field": "cover", "before": "Sol Ring", "after": "Cultivate"},
        ],
    }
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            page = browser.new_page(viewport={"width": 400, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.route("https://api.scryfall.com/**", lambda r: r.abort())  # no picture loads
            page.set_content(SIZED_HOST % ("#ffffff", 380, "light"))  # a host that sizes the frame
            page.evaluate(
                "(r) => { window.toolResult = r; }",
                {"structuredContent": details, "_meta": {APPROVAL_META_KEY: "c"}},
            )
            page.evaluate("(html) => { document.getElementById('f').srcdoc = html; }", card_html())
            card = page.frame_locator("#f")
            card.locator("#approve").wait_for()
            page.frames[1].wait_for_function("() => document.querySelectorAll('li.nopic').length === 4")
            geo = page.frames[1].evaluate(
                """() => [...document.querySelectorAll('ul.changes li:not(.group)')].map(li => {
  const r = li.getBoundingClientRect(), box = s => li.querySelector(s).getBoundingClientRect();
  return {cls: li.className, h: r.height, left: r.left, act: box('.act'), name: box('.name'),
          qty: box('.qty'), pics: li.querySelectorAll('.pic').length};
})"""
            )
            for row in geo:
                assert row["pics"] == 0 and row["act"]["left"] - row["left"] < 30, row  # no 34 px empty slot
                assert row["name"]["top"] >= row["act"]["bottom"] - 1, row  # the card under the action
                if row["qty"]["width"]:
                    assert abs(row["qty"]["top"] - row["act"]["top"]) < 6, row  # count on the action's line
                assert "detail" in row["cls"] or row["h"] < 60, row  # a card row: two lines, not three
            rows = card.locator("ul.changes li:not(.group)")
            tags = rows.nth(2).locator(".name")
            assert tags.inner_text() == "budget → ramp, landfall\nAdds ramp, landfall; removes budget"
            assert tags.locator(".sub").count() == 1  # the note is its own smaller line
            assert rows.nth(3).locator(".name").inner_text() == "Sol Ring → Cultivate"
            foot = card.locator("#foot").inner_text()
            assert foot.startswith("Proposed by ChatGPT · expires") and "cid-" not in foot, foot
            assert not errors, errors
        finally:
            browser.close()


def test_a_card_drawn_for_a_proposal_closed_since_shows_that_and_no_buttons() -> None:
    """The QA round's ChatGPT session: the proposal was rejected (by the assistant's own
    reject_proposal) seconds after it was made, while its card still showed live buttons. The
    card reads the proposal once when drawn and shows what happened instead."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    rejected = {
        **PROPOSAL,
        "state": "rejected",
        "closed_at": int(time.time()) - 60,
        "closed_by": "assistant",
        "closed_by_app": "ChatGPT",
    }
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            page = browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            card = _open(
                page, {"structuredContent": PROPOSAL, "_meta": {APPROVAL_META_KEY: "c"}}, get_reply=rejected
            )
            card.locator("#state.rejected").wait_for(timeout=5_000)
            status = card.locator("#status").inner_text()
            assert "Already rejected by the assistant in ChatGPT (its reject_proposal call) at " in status
            assert "Ask the assistant for a new proposal" in status
            page.wait_for_timeout(900)  # past the arming delay: still no buttons
            assert card.locator("#approve").count() == 0 and card.locator("#reject").count() == 0
            assert _confirms(page) == []
            assert [m["params"]["name"] for m in _calls(page, "tools/call")] == ["get_proposal"]
            assert not errors, errors
        finally:
            browser.close()


def test_a_press_on_a_closed_proposal_shows_its_state_and_drops_the_buttons() -> None:
    """When the card cannot read the proposal (the host refuses the call), the buttons stay; a press
    on a proposal closed meanwhile gets not_pending with its state, and the card shows that state
    instead of re-enabling the buttons."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    refusal = {
        "ok": False,
        "error": "not_pending",
        "message": "This proposal is already rejected, so it cannot be applied.",
        "state": "rejected",
        "closed_at": int(time.time()) - 5,
        "closed_by": "review_page",
    }
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            page = browser.new_page()
            card = _open(page, {"structuredContent": PROPOSAL, "_meta": {APPROVAL_META_KEY: "c"}}, refusal)
            card.locator("#approve:enabled").wait_for(timeout=5_000)  # the read failed: buttons stay
            card.locator("#approve").click()
            card.locator("#state.rejected").wait_for(timeout=5_000)
            assert "Already rejected by you on the review page at " in card.locator("#status").inner_text()
            assert card.locator("#approve").count() == 0 and card.locator("#reject").count() == 0
            assert len(_confirms(page)) == 1
            ctx = _calls(page, "ui/update-model-context")
            assert ctx and "already rejected" in ctx[0]["params"]["content"][0]["text"]
        finally:
            browser.close()


@pytest.mark.parametrize(
    ("message", "shown"),
    [
        (
            "Archidekt returned HTTP 503",
            "Archidekt returned HTTP 503. The review page shows what happened. Ask the assistant for a "
            "new proposal if you still want this change.",
        ),
        (
            "Archidekt is busy and asked the gateway to wait longer than an apply may wait, so the apply "
            "stopped. Nothing was changed on Archidekt. Try again later with a new proposal.",
            "Archidekt is busy and asked the gateway to wait longer than an apply may wait, so the apply "
            "stopped. Nothing was changed on Archidekt. Try again later with a new proposal. "
            "The review page shows what happened.",
        ),
    ],
)
def test_a_failed_approve_shows_failed_with_one_message_and_no_buttons(message: str, shown: str) -> None:
    """An Approve whose apply fails answers with the proposal's state (failed). The card
    shows Failed, the reason once as an error with what to do next, and no Approve or Reject (it
    used to keep Pending and live buttons that would only be refused)."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    refusal = {"ok": False, "error": "upstream", "message": message, "state": "failed"}
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            page = browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            card = _open(page, {"structuredContent": PROPOSAL, "_meta": {APPROVAL_META_KEY: "c"}}, refusal)
            card.locator("#approve:enabled").wait_for(timeout=5_000)
            card.locator("#approve").click()
            card.locator("#state.failed").wait_for(timeout=5_000)
            assert card.locator("#state").inner_text().lower() == "failed"
            status = card.locator("#status")
            assert status.inner_text() == shown
            assert "err" in (status.get_attribute("class") or "").split()
            assert card.locator("#approve").count() == 0 and card.locator("#reject").count() == 0
            assert card.locator(".status").count() == 1
            page.wait_for_timeout(900)  # nothing comes back after the arming delay
            assert card.locator("#approve").count() == 0
            assert len(_confirms(page)) == 1
            ctx = _calls(page, "ui/update-model-context")
            assert ctx and message in ctx[0]["params"]["content"][0]["text"]
            assert not errors, errors
        finally:
            browser.close()


STOPPED = (
    "Archidekt is busy and asked the gateway to wait longer than an apply may wait, so the apply "
    "stopped. Nothing was changed on Archidekt. The proposal is still pending, so it can be approved "
    "again later."
)


def test_an_approve_stopped_back_to_pending_keeps_pending_and_working_buttons() -> None:
    """An Approve whose apply Archidekt's waits stopped before anything changed answers
    with state pending. The card keeps Pending, shows the assistant's message once (no "review
    page" or "new proposal" sentence), and its Approve works again with the same code."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    refusal = {"ok": False, "error": "rate_limited", "message": STOPPED, "state": "pending"}
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            page = browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            card = _open(page, {"structuredContent": PROPOSAL, "_meta": {APPROVAL_META_KEY: "c"}}, refusal)
            card.locator("#approve:enabled").wait_for(timeout=5_000)
            card.locator("#approve").click()
            card.locator("#status.err").wait_for(timeout=5_000)
            status = card.locator("#status")
            assert status.inner_text() == STOPPED
            assert card.locator("#state").inner_text().lower() == "waiting"  # the status word
            assert card.locator(".status").count() == 1
            card.locator("#approve:enabled").wait_for(timeout=5_000)
            assert card.locator("#reject").is_enabled()
            card.locator("#approve").click()
            page.wait_for_function(
                "() => window.log.filter(m => m.method === 'tools/call' && "
                "m.params.name === 'confirm_proposal').length === 2",
                timeout=5_000,
            )
            assert [c["params"]["arguments"]["approval"] for c in _confirms(page)] == ["c", "c"]
            assert not errors, errors
        finally:
            browser.close()


def test_a_long_approve_that_ends_back_at_pending_offers_approve_again() -> None:
    """The Approve outlasted the gateway's wait ("applying"); the card follows it with
    get_proposal and finds it pending again after a stop on Archidekt's waits. It shows Pending,
    the stop's own message (not the assistant's next_step), and Approve with the card's code."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    applying = {**PROPOSAL, "state": "applying", "progress": {"sent_entries": 0, "of_entries": 3}}
    back = {
        **PROPOSAL,
        "state": "pending",
        "next_step": "for the assistant",
        "result": {"error": "rate_limited", "detail": STOPPED, "snapshot_id": "s1"},
    }
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            page = browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            card = _open(
                page, {"structuredContent": PROPOSAL, "_meta": {APPROVAL_META_KEY: "c"}}, applying, back
            )
            card.locator("#approve:enabled").wait_for(timeout=5_000)
            card.locator("#approve").click()
            card.locator("#state.applying").wait_for(timeout=5_000)
            card.locator("#state.pending").wait_for(timeout=10_000)  # the card reads it every 5 s
            card.locator("#status.err").wait_for(timeout=5_000)
            assert card.locator("#status").inner_text() == STOPPED
            # The assistant is told the stop's own approved message (section E),
            # nothing the owner has not approved
            told = [m["params"]["content"][0]["text"] for m in _calls(page, "ui/update-model-context")]
            assert len(told) == 2 and told[1] == STOPPED, told
            card.locator("#approve:enabled").wait_for(timeout=5_000)
            card.locator("#approve").click()
            page.wait_for_function(
                "() => window.log.filter(m => m.method === 'tools/call' && "
                "m.params.name === 'confirm_proposal').length === 2",
                timeout=5_000,
            )
            assert [c["params"]["arguments"]["approval"] for c in _confirms(page)] == ["c", "c"]
            assert not errors, errors
        finally:
            browser.close()


def test_a_long_approve_that_ends_failed_shows_the_same_failed_text() -> None:
    """The Approve outlasted the gateway's wait ("applying") and the apply then failed.
    The card shows Failed with the same text as an Approve that failed at once: the
    reason, "The review page shows what happened." and to ask for a new proposal, never the
    assistant's next_step."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    applying = {**PROPOSAL, "state": "applying", "progress": {"sent_entries": 0, "of_entries": 3}}
    failed = {
        **PROPOSAL,
        "state": "failed",
        "closed_at": int(time.time()),
        "next_step": "Nothing further was changed. Create a new proposal if you still want these edits.",
        "result": {"error": "upstream", "detail": "Archidekt returned HTTP 503", "snapshot_id": "s1"},
    }
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            page = browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            # get_proposal is refused until the press (the card keeps its buttons), then says failed
            card = _open(page, {"structuredContent": PROPOSAL, "_meta": {APPROVAL_META_KEY: "c"}}, applying)
            card.locator("#approve:enabled").wait_for(timeout=5_000)
            card.locator("#approve").click()
            card.locator("#state.applying").wait_for(timeout=5_000)
            page.evaluate("(r) => { window.getReply = r; }", failed)
            card.locator("#state.failed").wait_for(timeout=10_000)  # the card reads it every 5 s
            status = card.locator("#status")
            assert status.inner_text() == (
                "Archidekt returned HTTP 503. The review page shows what happened. Ask the assistant for a "
                "new proposal if you still want this change."
            )
            assert "err" in (status.get_attribute("class") or "").split()
            assert card.locator("#approve").count() == 0 and card.locator("#reject").count() == 0
            assert card.locator(".status").count() == 1
            assert not errors, errors
        finally:
            browser.close()


# WCAG contrast of each locked button's text against the card behind it (the button is see-through)
LOCKED = """() => {
  function rgb(s) { const m = s.match(/[\\d.]+/g); return m ? m.map(Number) : [0, 0, 0, 0]; }
  function lum(c) {
    const a = c.slice(0, 3).map(v => {
      v /= 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * a[0] + 0.7152 * a[1] + 0.0722 * a[2];
  }
  function bg(e) {
    for (; e; e = e.parentElement) {
      const c = rgb(getComputedStyle(e).backgroundColor);
      if (c.length < 4 || c[3] >= 1) return c;
    }
    return [255, 255, 255];
  }
  return ["approve", "reject"].map(id => {
    const b = document.getElementById(id), s = getComputedStyle(b);
    const a = lum(rgb(s.color)), z = lum(bg(b));
    return {id, disabled: b.disabled, opacity: s.opacity, border: s.borderTopStyle,
            borderColour: s.borderTopColor, colour: s.color,
            ratio: (Math.max(a, z) + 0.05) / (Math.min(a, z) + 0.05)};
  });
}"""


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_locked_buttons_read_at_full_strength(theme: str) -> None:
    """As on the review page, while Approve and Reject are locked (the 700 ms after
    drawing, and while a press is out) they are not faded: full-strength text with a dashed outline
    in each button's own colour, at least 4.5:1 against the card, at 344, 390, 673 and 1280 px."""
    from playwright.sync_api import sync_playwright

    from .test_cards_browser import _shot

    exe = _chromium_or_skip()
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            for width in (344, 390, 673, 1280):
                page = browser.new_page(viewport={"width": width, "height": 900})
                page.route(
                    "https://api.scryfall.com/**", lambda r: r.fulfill(content_type="image/png", body=PNG)
                )
                page.set_content(
                    SIZED_HOST % ("#262624" if theme == "dark" else "#ffffff", width - 24, theme)
                )
                page.evaluate(
                    "(r) => { window.toolResult = r; }",
                    {"structuredContent": PROPOSAL, "_meta": {APPROVAL_META_KEY: "c"}},
                )
                page.evaluate("(html) => { document.getElementById('f').srcdoc = html; }", card_html())
                card = page.frame_locator("#f")
                # Locked by the arming delay, or (to measure it at leisure) by a press still out: the
                # same disabled state either way. The 700 ms arming ends first (waited on the buttons'
                # state, not on time), so its timer cannot unlock them during the measure.
                card.locator("#approve:enabled").wait_for(timeout=5_000)
                card.locator("#reject:enabled").wait_for(timeout=5_000)
                frame = page.frames[1]
                frame.evaluate("() => { approve.disabled = true; reject.disabled = true; }")
                for b in frame.evaluate(LOCKED):
                    assert b["disabled"] and b["opacity"] == "1", (width, b)
                    assert b["border"] == "dashed" and b["borderColour"] == b["colour"], (width, b)
                    assert b["ratio"] >= 4.5, (width, b)
                page.wait_for_timeout(100)  # the frame takes the card's height (size-changed)
                _shot(page, f"l8-chatcard-locked-{width}-{theme}")
                page.close()
        finally:
            browser.close()


NUDGE = "Approved, go on"


def _nudges(page) -> list[dict]:
    """What the card posted in the chat as the person: ui/message, or ChatGPT's follow-up call."""
    return [
        m
        for m in page.evaluate("() => window.log")
        if m.get("method") in ("ui/message", "openai/sendFollowUpMessage")
    ]


def _press(page, card, button: str) -> None:
    card.locator(f"#{button}:enabled").wait_for(timeout=10_000)
    card.locator(f"#{button}").click()
    if button == "reject":
        card.locator("#reason").wait_for()
        card.locator("#reject-go").click()
    page.wait_for_function(
        "() => window.log.some(m => m.method === 'ui/update-model-context')", timeout=5_000
    )


def test_approve_nudges_the_assistant_only_where_the_host_takes_a_message() -> None:
    """Ben, 0.7.31 test round: after he pressed Approve the assistant sat still until he wrote
    again. Where the host lets a card post a message (MCP Apps' ui/message, or ChatGPT's
    sendFollowUpMessage), Approve now also posts "Approved, go on", after the card has told the
    model what happened; Reject posts nothing, and a host with neither gets nothing."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    applied = {**PROPOSAL, "state": "applied", "result": {"deck_url": "https://archidekt.com/decks/42"}}
    rejected = {**PROPOSAL, "state": "rejected"}
    start = {"structuredContent": PROPOSAL, "_meta": {APPROVAL_META_KEY: "code-123"}}
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            # 1. An MCP Apps host that takes messages: one ui/message, after the model context.
            page = browser.new_page()
            card = _open(
                page, start, applied, host_caps={"openLinks": {}, "serverTools": {}, "message": {"text": {}}}
            )
            _press(page, card, "approve")
            page.wait_for_function("() => window.log.some(m => m.method === 'ui/message')", timeout=5_000)
            log = page.evaluate("() => window.log")
            sent = [m for m in log if m.get("method") == "ui/message"]
            assert len(sent) == 1 and sent[0]["params"] == {
                "role": "user",
                "content": [{"type": "text", "text": NUDGE}],
            }
            told = next(i for i, m in enumerate(log) if m.get("method") == "ui/update-model-context")
            assert told < log.index(sent[0])
            page.close()
            # 2. ChatGPT's own follow-up call, where the host doesn't list ui/message.
            page = browser.new_page()
            card = _open(page, start, applied, openai=True)
            _press(page, card, "approve")
            page.wait_for_function(
                "() => window.log.some(m => m.method === 'openai/sendFollowUpMessage')", timeout=5_000
            )
            assert [m["params"] for m in _nudges(page)] == [{"prompt": NUDGE}]
            page.close()
            # 3. A host with neither: nothing is posted, as before.
            page = browser.new_page()
            card = _open(page, start, applied)
            _press(page, card, "approve")
            page.wait_for_timeout(300)
            assert _nudges(page) == []
            page.close()
            # 4. Reject never nudges, even where the host would take it.
            page = browser.new_page()
            card = _open(page, start, rejected, host_caps={"message": {"text": {}}}, openai=True)
            _press(page, card, "reject")
            page.wait_for_timeout(300)
            assert _nudges(page) == []
            page.close()
        finally:
            browser.close()
