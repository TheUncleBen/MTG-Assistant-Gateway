"""0.7.34, smarter approval cards: a proposal card already in the chat follows its proposal. It
reads it again while it is on screen, draws a newer version (an amend) with that version's own
code, asks for another press when the change was updated under an Approve, and says when the
assistant withdrew it. Driven by a fake host that speaks the MCP Apps postMessage protocol."""

from __future__ import annotations

import time

import pytest

from mtg_gateway.approve import APPROVAL_META_KEY, card_html

from .test_cards_browser import PNG
from .test_consent_browser import _chromium_or_skip, _launch

pytest.importorskip("playwright")

V1 = {
    "ok": True,
    "proposal_id": "p-1",
    "kind": "edit",
    "deck_id": "42",
    "deck_name": "Sample Deck",
    "state": "pending",
    "version": 1,
    "board_no": 3,
    "diff": "+1 Opt",
    "rows": [{"kind": "add", "name": "Opt", "qty": 1}],
    "created_by": "app: Claude (abc)",
    "expires_at": int(time.time()) + 3600,
    "review_url": "https://mtg.test/proposals/p-1",
    "writes_enabled": True,
    "result": None,
}
V2 = {
    **V1,
    "version": 2,
    "diff": "+1 Opt\n+1 Ponder",
    "rows": [{"kind": "add", "name": "Opt", "qty": 1}, {"kind": "add", "name": "Ponder", "qty": 1}],
    "changed_by": "app: Claude (abc)",
    "what_changed": "now +1 Ponder",
}
UPDATED = "Updated since this card was drawn. Check it above, then press again."
REDRAWN = "Updated: this card now shows the newest version."

# The host: answers ui/initialize, pushes the tool result and answers each get_proposal from
# window.gets (a queue; the last one stays), with window.getMeta as its _meta; "refuse" refuses
# the call. confirm_proposal answers window.confirms the same way. With window.gate set to n,
# the get_proposal calls after the first n wait unanswered until window.release(), so a test
# looks at the card between two reads however slow the machine is.
HOST = """<!DOCTYPE html><html><body>
<iframe id="f" sandbox="allow-scripts" style="width:600px;height:10px;border:0"></iframe>
<script>
window.log = []; window.gets = []; window.getMeta = {}; window.confirms = [];
window.gate = null; window.answered = 0; window.held = [];
var frame = document.getElementById("f");
function next(q) { return q.length > 1 ? q.shift() : q[0]; }
window.release = () => { window.gate = null; const h = window.held; window.held = []; h.forEach(handle); };
window.addEventListener("message", (ev) => {
  const m = ev.data; if (!m || m.jsonrpc !== "2.0") return;
  window.log.push(m);
  if (m.method === "tools/call" && m.params.name === "get_proposal" && window.gate !== null
      && window.answered >= window.gate) { window.held.push(m); return; }
  handle(m);
});
function handle(m) {
  const send = (x) => frame.contentWindow.postMessage(x, "*");
  if (m.method === "ui/initialize") {
    send({jsonrpc: "2.0", id: m.id, result: {protocolVersion: "2026-01-26",
      hostInfo: {name: "fake", version: "0"}, hostCapabilities: {openLinks: {}, serverTools: {}},
      hostContext: {theme: "light", platform: "web"}}});
  } else if (m.method === "ui/notifications/initialized") {
    send({jsonrpc: "2.0", method: "ui/notifications/tool-result", params: window.toolResult});
  } else if (m.method === "tools/call") {
    const get = m.params.name === "get_proposal";
    if (get) window.answered += 1;
    const sc = next(get ? window.gets : window.confirms);
    if (sc === "refuse" || sc === undefined)
      send({jsonrpc: "2.0", id: m.id, error: {code: -32000, message: "blocked"}});
    else send({jsonrpc: "2.0", id: m.id, result: {content: [{type: "text", text: "x"}],
      structuredContent: sc, _meta: get ? window.getMeta : {}}});
  } else if (m.id !== undefined && m.method) {
    send({jsonrpc: "2.0", id: m.id, result: {}});
  }
}
</script></body></html>"""


def _open(
    page,
    gets: list,
    confirms: list | None = None,
    *,
    gate: int | None = None,
    every_ms: int | None = None,
    meta=None,
    applying_ms: int | None = None,
    drawn: dict | None = None,
):
    html = card_html()
    if applying_ms is not None:
        assert "var APPLYING_POLL_MS = 5000;" in html
        html = html.replace("var APPLYING_POLL_MS = 5000;", f"var APPLYING_POLL_MS = {applying_ms};")
    if every_ms is not None:
        assert "var REFRESH_EVERY_MS = 30000," in html
        html = html.replace("var REFRESH_EVERY_MS = 30000,", f"var REFRESH_EVERY_MS = {every_ms},")
    page.route("https://api.scryfall.com/**", lambda r: r.fulfill(content_type="image/png", body=PNG))
    page.set_content(HOST)
    page.evaluate("(g) => { window.gets = g; }", gets)
    page.evaluate("(c) => { window.confirms = c; }", confirms or [])
    page.evaluate("(n) => { window.gate = n; }", gate)
    code = meta if meta is not None else {APPROVAL_META_KEY: "code-v2"}
    page.evaluate("(m) => { window.getMeta = m; }", code)
    first = {"structuredContent": drawn or V1, "_meta": {APPROVAL_META_KEY: "code-v1"}}
    page.evaluate("(r) => { window.toolResult = r; }", first)
    page.evaluate("(h) => { document.getElementById('f').srcdoc = h; }", html)
    page.wait_for_function("() => window.log.some(m => m.method === 'ui/notifications/initialized')")
    return page.frame_locator("#f")


def _calls(page, name: str) -> list[dict]:
    return [
        m["params"]["arguments"]
        for m in page.evaluate("() => window.log")
        if m.get("method") == "tools/call" and m["params"]["name"] == name
    ]


def _run(test) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            page = browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            test(page)
            assert not errors, errors
        finally:
            browser.close()


def test_an_older_card_draws_the_amended_version_and_approves_it_with_its_code() -> None:
    def test(page) -> None:
        card = _open(page, [V2], [{**V2, "state": "rejected"}])
        card.locator("ul.changes li", has_text="Ponder").wait_for(timeout=5_000)
        # the app that made it changed it itself: "Updated: …", not "Changed by" (0.7.46, A20)
        assert "Updated: now +1 Ponder." in card.locator("#note").inner_text()
        assert card.locator("#status").inner_text() == REDRAWN
        card.locator("#reject:enabled").wait_for(timeout=5_000)
        card.locator("#reject").click()
        card.locator("#reject-go").click()
        card.locator("#state.rejected").wait_for(timeout=5_000)
        assert [c["approval"] for c in _calls(page, "confirm_proposal")] == ["code-v2"]

    _run(test)


def test_an_amend_under_an_approve_is_drawn_and_needs_another_press() -> None:
    def test(page) -> None:
        # the first read is refused (the card keeps v1), the read before Approve finds v2
        card = _open(page, ["refuse", V2], [{**V2, "state": "applied"}])
        card.locator("#approve:enabled").wait_for(timeout=5_000)
        card.locator("#approve").click()
        card.locator("ul.changes li", has_text="Ponder").wait_for(timeout=5_000)
        assert UPDATED in card.locator("#status").inner_text()
        assert _calls(page, "confirm_proposal") == []  # nothing approved on that press
        card.locator("#approve:enabled").wait_for(timeout=5_000)
        card.locator("#approve").click()
        card.locator("#state.applied").wait_for(timeout=5_000)
        assert [c["approval"] for c in _calls(page, "confirm_proposal")] == ["code-v2"]

    _run(test)


def test_a_changed_answer_draws_the_new_version_instead_of_locking_the_card() -> None:
    changed = {
        "ok": False,
        "error": "changed",
        "message": "There is a newer version of this change than this card shows, so nothing was sent "
        "to Archidekt. Open it again to see what it does now.",
        "state": "pending",
        "version": 2,
    }

    def test(page) -> None:
        # the reads find v1 until the press is refused as changed, then v2
        card = _open(page, [V1, V1, V2], [changed, {**V2, "state": "applied"}])
        card.locator("#approve:enabled").wait_for(timeout=5_000)
        card.locator("#approve").click()
        card.locator("ul.changes li", has_text="Ponder").wait_for(timeout=5_000)
        assert UPDATED in card.locator("#status").inner_text()
        card.locator("#approve:enabled").wait_for(timeout=5_000)
        card.locator("#approve").click()
        card.locator("#state.applied").wait_for(timeout=5_000)
        assert [c["approval"] for c in _calls(page, "confirm_proposal")] == ["code-v1", "code-v2"]

    _run(test)


def test_a_card_on_screen_reads_again_and_shows_a_withdrawn_change_then_stops() -> None:
    def test(page) -> None:
        card = _open(page, [V1, V1, {**V1, "state": "withdrawn"}], every_ms=300)
        card.locator("#state.withdrawn").wait_for(timeout=5_000)
        assert card.locator("#status").inner_text() == (
            "The assistant withdrew this change, so it no longer waits. Nothing was sent to Archidekt "
            "from this one."
        )
        assert card.locator("#approve").count() == 0
        # a withdrawn change never comes back: no more reads
        page.wait_for_timeout(1_500)
        assert len(_calls(page, "get_proposal")) == 3

    _run(test)


def test_a_newer_version_read_without_a_code_offers_the_review_page_only() -> None:
    def test(page) -> None:
        card = _open(page, [V2], meta={})
        card.locator("ul.changes li", has_text="Ponder").wait_for(timeout=5_000)
        page.wait_for_timeout(900)  # past the arming delay
        assert card.locator("#approve").count() == 0
        assert card.locator("#actions button", has_text="Approve on the review page").count() == 1

    _run(test)


def test_a_redraw_closes_an_open_why_box_so_reject_answers_the_change_shown() -> None:
    def test(page) -> None:
        card = _open(page, [V1, V2], [{**V2, "state": "rejected"}], gate=1, every_ms=300)
        card.locator("#reject:enabled").wait_for(timeout=5_000)
        card.locator("#reject").click()
        assert "hidden" not in (card.locator("#why").get_attribute("class") or "")
        page.evaluate("() => window.release()")
        card.locator("ul.changes li", has_text="Ponder").wait_for(timeout=5_000)  # the timed read
        assert "hidden" in (card.locator("#why").get_attribute("class") or "")
        assert _calls(page, "confirm_proposal") == []

    _run(test)


def test_a_replaced_change_that_comes_back_shows_again_with_its_buttons() -> None:
    def test(page) -> None:
        gone = {**V1, "state": "replaced", "replace_note": "Replaced by #4."}
        card = _open(page, [gone, gone, V1], gate=2, every_ms=300)
        card.locator("#state.replaced").wait_for(timeout=5_000)
        page.evaluate("() => window.release()")
        card.locator("#state.pending").wait_for(timeout=5_000)
        assert card.locator("#status").inner_text() == "This change is waiting again."
        card.locator("#approve:enabled").wait_for(timeout=5_000)

    _run(test)


def test_the_timed_reads_stop_after_a_few() -> None:
    def test(page) -> None:
        card = _open(page, [V1], every_ms=100)
        card.locator("#approve:enabled").wait_for(timeout=5_000)
        page.wait_for_timeout(1_500)
        assert len(_calls(page, "get_proposal")) == 1 + 6  # the read when drawn, then six timed

    _run(test)


def test_a_changed_answer_with_no_read_leaves_the_review_page_not_locked_buttons() -> None:
    changed = {"ok": False, "error": "changed", "message": "There is a newer version.", "state": "pending"}

    def test(page) -> None:
        card = _open(page, [V1, V1, "refuse"], [changed])
        card.locator("#approve:enabled").wait_for(timeout=5_000)
        card.locator("#approve").click()
        card.locator("#actions button", has_text="Approve on the review page").wait_for(timeout=5_000)
        assert card.locator("#approve").count() == 0
        assert card.locator("#status").inner_text() == "There is a newer version."

    _run(test)


def test_a_changed_answer_whose_read_finds_nothing_leaves_the_review_page() -> None:
    changed = {"ok": False, "error": "changed", "message": "There is a newer version.", "state": "pending"}
    gone = {"ok": False, "error": "not_found", "message": "No such proposal for your account."}

    def test(page) -> None:
        card = _open(page, [V1, V1, gone], [changed])
        card.locator("#approve:enabled").wait_for(timeout=5_000)
        card.locator("#approve").click()
        card.locator("#actions button", has_text="Approve on the review page").wait_for(timeout=5_000)
        assert card.locator("#approve").count() == 0

    _run(test)


def test_an_expired_replaced_card_is_not_read_again() -> None:
    """A replaced change comes back only before it expires, so an expired one is not read again."""

    def test(page) -> None:
        gone = int(time.time()) - 60
        old = {**V1, "state": "replaced", "replace_note": "Replaced by #4.", "expires_at": gone}
        card = _open(page, [old], every_ms=100)
        card.locator("#state.replaced").wait_for(timeout=5_000)
        page.wait_for_timeout(1_000)
        assert len(_calls(page, "get_proposal")) == 1  # the read when drawn only

    _run(test)


def test_a_waiting_card_past_its_old_expiry_is_still_read() -> None:
    """An amend restarts the expiry, so a waiting card is read whatever expiry it was drawn with."""

    def test(page) -> None:
        late = {**V1, "expires_at": int(time.time()) - 60}
        card = _open(page, [late], every_ms=100, drawn=late)
        card.locator("#title").wait_for(timeout=5_000)
        page.wait_for_timeout(1_500)
        assert len(_calls(page, "get_proposal")) == 1 + 6

    _run(test)


def test_an_apply_stopped_back_to_waiting_reads_again_and_follows_an_amend() -> None:
    """After a long apply ends back at waiting, the timed reads start again with a fresh count,
    even when this visit's reads were all used before Approve, so a later amend is drawn."""

    def test(page) -> None:
        applying = {**V1, "state": "applying"}
        card = _open(page, [V1], [applying], every_ms=100, applying_ms=400)
        card.locator("#approve:enabled").wait_for(timeout=5_000)
        # 7 = the read when drawn + REFRESH_MAX (6) timed reads, as pinned by the test above
        page.wait_for_function(
            "() => window.log.filter((m) => m.method === 'tools/call'"
            " && m.params.name === 'get_proposal').length >= 7"
        )
        page.wait_for_timeout(300)  # the visit's reads are spent, as on a slow runner
        card.locator("#approve").click()
        card.locator("#state.applying").wait_for(timeout=5_000)  # the timed reads stop here
        card.locator("#state.pending").wait_for(timeout=5_000)  # the apply stopped: waiting again
        page.evaluate("(v) => { window.gets = [v]; }", V2)  # then the assistant amends it
        card.locator("ul.changes li", has_text="Ponder").wait_for(timeout=5_000)
        assert card.locator("#status").inner_text() == REDRAWN

    _run(test)
