"""The proposal card (static/proposal-card.html) in a real browser, driven by a fake host that
speaks the MCP Apps postMessage protocol: it draws the proposal, its buttons call
confirm_proposal with the one-time code, and nothing is called before the person presses."""

from __future__ import annotations

import json
import time

import pytest

from mtg_gateway.approve import APPROVAL_META_KEY, card_html

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
var frame = document.getElementById("f");
window.addEventListener("message", (ev) => {
  const m = ev.data; if (!m || m.jsonrpc !== "2.0") return;
  window.log.push(m);
  const send = (x) => frame.contentWindow.postMessage(x, "*");
  if (m.method === "ui/initialize") {
    send({jsonrpc: "2.0", id: m.id, result: {
      protocolVersion: "2026-01-26", hostInfo: {name: "fake", version: "0"},
      hostCapabilities: {openLinks: {}, serverTools: {}},
      hostContext: {theme: "dark", platform: "web",
                    styles: {variables: {"--color-text-primary": "rgb(1, 2, 3)"}},
                    safeAreaInsets: {top: 0, right: 0, bottom: 0, left: 0}}}});
  } else if (m.method === "ui/notifications/initialized") {
    send({jsonrpc: "2.0", method: "ui/notifications/tool-result", params: window.toolResult});
  } else if (m.method === "tools/call") {
    const sc = window.confirmReply;
    const text = JSON.stringify(sc);
    send({jsonrpc: "2.0", id: m.id,
          result: {content: [{type: "text", text: text}], structuredContent: sc, isError: false}});
  } else if (m.method === "ui/open-link" || m.method === "ui/update-model-context") {
    send({jsonrpc: "2.0", id: m.id, result: {}});
  } else if (m.id !== undefined && m.method) {
    send({jsonrpc: "2.0", id: m.id, error: {code: -32601, message: "nope"}});
  }
});
// A request the host makes of the card; the card must answer it.
window.teardown = () => { frame.contentWindow.postMessage(
  {jsonrpc: "2.0", id: 999, method: "ui/resource-teardown", params: {reason: "test"}}, "*"); };
</script></body></html>"""


def _open(page, result: dict, confirm_reply: dict | None = None):
    page.set_content(HOST)
    page.evaluate("(r) => { window.toolResult = r; }", result)
    page.evaluate("(r) => { window.confirmReply = r; }", confirm_reply)
    page.evaluate("(html) => { document.getElementById('f').srcdoc = html; }", card_html())
    page.wait_for_function(
        "() => window.log.some(m => m.method === 'ui/notifications/initialized')", timeout=10_000
    )
    return page.frame_locator("#f")


def _calls(page, method: str) -> list[dict]:
    return [m for m in page.evaluate("() => window.log") if m.get("method") == method]


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
                page, {"structuredContent": PROPOSAL, "_meta": {APPROVAL_META_KEY: "code-123"}}, applied
            )
            # Drawn from the fields, never as markup: the hostile names appear as text.
            card.locator("#title").wait_for()
            assert card.locator("#title").inner_text() == "Change to Sample <b>Deck</b>"
            assert card.locator("#state").inner_text().lower() == "pending"
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
            # Buttons: Approve and Reject, disabled for a moment after drawing, then live.
            approve = card.locator("#approve")
            assert approve.is_disabled() and card.locator("#reject").is_disabled()
            page.wait_for_timeout(900)
            assert approve.is_enabled()
            assert _calls(page, "tools/call") == []  # nothing was called by itself
            assert _calls(page, "ui/notifications/size-changed"), "the card reports its size"
            # Press Approve: one confirm_proposal call with the proposal id and the code.
            approve.click()
            page.wait_for_function("() => window.log.some(m => m.method === 'tools/call')", timeout=5_000)
            calls = _calls(page, "tools/call")
            assert len(calls) == 1
            assert calls[0]["params"] == {
                "name": "confirm_proposal",
                "arguments": {"proposal_id": "p-1", "approval": "code-123", "decision": "approve"},
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
            assert card.locator("#risk").inner_text().lower() == "high risk"
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
            assert "assistant applies this itself" in card.locator("#note").inner_text()
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
            assert _calls(page, "tools/call") == []
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
            assert _calls(page, "tools/call") == []
            card.locator("#reason").fill("keep the Islands")
            card.locator("#reject-go").click()
            card.locator("#status.err").wait_for(timeout=5_000)
            assert "does not belong" in card.locator("#status").inner_text()
            assert _calls(page, "tools/call")[0]["params"]["arguments"]["decision"] == "reject"
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
    """At phone widths the state and risk badges stay one line each and clear of the title, and a
    long change ("Main → Maybeboard (leaves the deck, −1)") wraps under the card name instead of
    squeezing it to a letter a line."""
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
            for width in (320, 360, 420):
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
  return {titleRight: h.right, badges: b.map(r => [r.left, r.height]), nameWidth: n.width};
}"""
                )
                assert all(h <= 26 for _, h in geo["badges"]), (width, geo)
                assert all(left >= geo["titleRight"] for left, _ in geo["badges"]), (width, geo)
                assert geo["nameWidth"] > 80, (width, geo)
                page.close()
        finally:
            browser.close()
