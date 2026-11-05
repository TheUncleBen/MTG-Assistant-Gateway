"""Nothing typed is lost (0.7.46 audit QG-01, QG-02, QG-03, QG-04; QOL-03): the New deck box, the
Binder's Import a list and a deck's comment keep what was typed through a reload, and the New
deck form comes back filled in after a sign-in that ended while it was being filled in. A deck
that was created leaves no draft behind. Driven in Chromium at phone and desktop widths."""

from __future__ import annotations

from pathlib import Path

import pytest

from .test_deck_menu_browser import Server, _chromium, _page

pytest.importorskip("playwright")

LIST = "1 Sol Ring\n1 Arcane Signet\n1 Cultivate"


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _open_paste(page) -> None:
    box = page.locator("#newdeck")
    box.wait_for()
    box.locator(".nd-tabs [data-src=paste]").click()


@pytest.mark.parametrize("width", [412, 1280])
def test_new_deck_box_keeps_its_draft_through_a_reload_and_an_ended_sign_in(
    server: Server, width: int
) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width)
        try:
            page.goto(f"{server.base}/decks/new", wait_until="networkidle")
            _open_paste(page)
            page.locator("#newdeck #name").fill("Kept draft")
            page.locator("#newdeck #source").fill(LIST)
            page.locator("#newdeck input[name=private]").check()
            # a reload: the name, the list, the switch and the Paste tab are back
            page.reload(wait_until="networkidle")
            box = page.locator("#newdeck")
            box.wait_for()
            assert box.locator("#name").input_value() == "Kept draft"
            assert box.locator("#source").input_value() == LIST
            assert box.locator("input[name=private]").is_checked()
            assert box.locator(".nd-tabs [data-src=paste]").get_attribute("aria-selected") == "true"
            assert "3 cards" in box.locator(".nd-pn").inner_text()

            # the sign-in ends while the form is filled in: Create deck goes to the sign-in page
            # with ?draft=1 in the address it comes back to (the POST's body is dropped)
            page.route(
                "**/login?*", lambda r: r.fulfill(status=200, content_type="text/html", body="<p>sign in</p>")
            )
            page.context.clear_cookies()
            box.locator(".acts button.primary", has_text="Create deck").click()
            page.wait_for_url("**/login?next=/decks/new?draft=1", timeout=8000)
            # signed in again, the sign-in lands on that address: the form is filled in again
            page.context.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
            page.goto(f"{server.base}/decks/new?draft=1", wait_until="networkidle")
            box = page.locator("#newdeck")
            box.wait_for()
            assert box.locator("#name").input_value() == "Kept draft"
            assert box.locator("#source").input_value() == LIST

            # created: the deck exists, and the next New deck box starts empty
            box.locator(".acts button.primary", has_text="Create deck").click()
            page.wait_for_url("**/decks/*?ok=created", timeout=15000)
            page.goto(f"{server.base}/decks/new", wait_until="networkidle")
            box = page.locator("#newdeck")
            box.wait_for()
            assert box.locator("#name").input_value() == ""
            assert box.locator("#source").input_value() == ""
            assert errors == [], errors
        finally:
            browser.close()


def test_binder_import_and_deck_comment_keep_their_drafts(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1280)
        try:
            page.goto(f"{server.base}/collection?add=import", wait_until="networkidle")
            area = page.locator("#importtext")
            area.wait_for()
            area.fill("4 Lightning Bolt\n2 Counterspell")
            page.reload(wait_until="networkidle")
            page.goto(f"{server.base}/collection?add=import", wait_until="networkidle")
            assert page.locator("#importtext").input_value() == "4 Lightning Bolt\n2 Counterspell"

            page.goto(f"{server.base}/decks/42", wait_until="networkidle")
            ctext = page.locator("#ctext")
            ctext.scroll_into_view_if_needed()
            ctext.fill("A long comment typed on a phone")
            page.reload(wait_until="networkidle")
            assert page.locator("#ctext").input_value() == "A long comment typed on a phone"
            # another member on this browser never sees it: the draft is keyed by the member
            page.evaluate(
                "() => document.querySelector('header.topbar [data-who]').setAttribute('data-who', 'other')"
            )
            keys = page.evaluate("() => Object.keys(localStorage).filter(k => k.startsWith('mtg_draft:'))")
            assert keys and all(":other:" not in k for k in keys), keys
            assert errors == [], errors
        finally:
            browser.close()


@pytest.mark.parametrize("width", [412, 1280])
def test_a_draft_put_back_says_so_and_discard_clears_it_while_esc_keeps_it(
    server: Server, width: int
) -> None:
    """A restored draft is never silent (0.7.46 re-audit N04): the form says "Your unsent draft
    is back." with Discard. Esc and Cancel keep the draft (that is what a draft is for); Discard
    clears it, and the form is empty after a reload too. The same note on every kept form: the
    deck comment here."""
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width)
        try:
            page.goto(f"{server.base}/decks/new", wait_until="networkidle")
            box = page.locator("#newdeck")
            box.wait_for()
            assert box.locator(".draft-note").count() == 0  # nothing to put back: no note
            box.locator("#name").fill("Old idea")
            # Esc closes the box; New deck opens it again with the draft and the note
            page.keyboard.press("Escape")
            page.wait_for_function("() => document.getElementById('newdeck').hidden")
            page.goto(f"{server.base}/decks/new", wait_until="networkidle")
            box = page.locator("#newdeck")
            box.wait_for()
            assert box.locator("#name").input_value() == "Old idea"
            note = box.locator(".draft-note")
            assert note.is_visible() and note.inner_text().startswith("Your unsent draft is back.")
            assert note.get_attribute("role") == "status"
            discard = note.locator("button", has_text="Discard")
            bb = discard.bounding_box()
            assert bb and bb["height"] >= 44, bb
            # Cancel keeps it too
            box.locator(".acts button", has_text="Cancel").click()
            page.wait_for_function("() => document.getElementById('newdeck').hidden")
            page.goto(f"{server.base}/decks/new", wait_until="networkidle")
            box = page.locator("#newdeck")
            box.wait_for()
            assert box.locator("#name").input_value() == "Old idea"
            # Discard: the form is empty, the note is gone, and a reload shows nothing
            box.locator(".draft-note button", has_text="Discard").click()
            assert box.locator("#name").input_value() == ""
            assert box.locator(".draft-note").count() == 0
            assert box.locator(".nd-pt").inner_text() == "Unnamed deck"  # the preview followed
            page.goto(f"{server.base}/decks/new", wait_until="networkidle")
            box = page.locator("#newdeck")
            box.wait_for()
            assert box.locator("#name").input_value() == "" and box.locator(".draft-note").count() == 0

            # a deck's comment: the same note, and typing on takes the note away (the draft is
            # the member's again; Discard would throw away the new typing)
            page.goto(f"{server.base}/decks/42", wait_until="networkidle")
            ctext = page.locator("#ctext")
            ctext.scroll_into_view_if_needed()
            ctext.fill("A comment typed on a phone")
            page.reload(wait_until="networkidle")
            form = page.locator("form.newcomment")
            assert form.locator("#ctext").input_value() == "A comment typed on a phone"
            note = form.locator(".draft-note")
            assert note.is_visible() and note.inner_text().startswith("Your unsent draft is back.")
            form.locator("#ctext").press("End")
            form.locator("#ctext").type(" and more")
            assert form.locator(".draft-note").count() == 0
            page.reload(wait_until="networkidle")
            form = page.locator("form.newcomment")
            assert form.locator("#ctext").input_value() == "A comment typed on a phone and more"
            form.locator(".draft-note button", has_text="Discard").click()
            assert form.locator("#ctext").input_value() == ""
            assert errors == [], errors
        finally:
            browser.close()


# -- every free-text field (audit F-3): the pre-push check ------------------------------------------
# Each form the audit listed, and the same class elsewhere: the page it is on, what opens it, and
# the fields typed in. Typed without leaving the field (nothing auto-saves on the way), reloaded,
# and the text must be back. A form drawn hidden in a box is opened by its own address, or by the
# press that opens it (``open``). A fold is opened before its field is typed in.
FIELDS: list[tuple[str, str, dict[str, str]]] = [
    # page, what opens the form ("" when it is on the page), the fields with what is typed
    ("/decks/42/settings", "", {"#name": "Kept name", "#description": "Kept description"}),
    (
        "/decks/42/edit",
        "fold:details.pastebox summary",
        {"#addname": "Sol Ri", "#pastetext": "2 Lightning Bolt"},
    ),
    (
        "/decks/42/compare",
        "fold:.cmp-form summary",
        {"#cmp-with": "9999", ".cmp-form textarea[name=paste]": "1 Sol Ring"},
    ),
    ("/decks/42/help", "", {"#dx-hg": "Win by turn six every game", "#dx-hi": "Too slow to start"}),
    ("/decks/42/report-to-archidekt", "", {"#dx-rm": "Stolen art on the cover"}),
    ("/decks/42/contest", "", {"#dx-es": "Simic ramp, big sea monsters"}),
    (
        "/forum/thread/700007",
        "click:#fm-contest .btn:has-text('Enter a deck…')",
        {"#fm-es": "Big sea monsters"},
    ),
    ("/forum/thread/700002", "report", {"#dx-rcm": "Spam in the first post"}),
    ("/reports", "fold:.rp-paste summary", {".rp-paste-f textarea": "1 Sol Ring\n1 Arcane Signet"}),
    ("/collection/wants", "", {".lp-note textarea": "Trade for it"}),
]


def _open(page, how: str) -> None:
    if not how:
        return
    kind, _, sel = how.partition(":")
    if kind == "fold":
        page.locator(sel).click()
    elif kind == "click":
        page.locator(sel).click()
    elif kind == "report":
        page.locator(".fm-post .kebab").first.click()
        page.locator(".ctxmenu [role=menuitem]", has_text="Report comment…").click()
        page.locator("#reportcomment").wait_for(state="visible")
    else:
        raise AssertionError(how)


def _post_reject(page, base: str, csrf: str) -> None:
    """The Reject confirm page: the checked rows posted to /proposals/select without ``sure``."""
    page.evaluate(
        """([base, csrf]) => {
          const f = document.createElement("form");
          f.method = "post"; f.action = base + "/proposals/select";
          const fields = [["csrf", csrf], ["pick", "abc"], ["action", "reject"], ["back", "/proposals"]];
          for (const [k, v] of fields) {
            const i = document.createElement("input");
            i.type = "hidden"; i.name = k; i.value = v;
            f.appendChild(i);
          }
          document.body.appendChild(f); f.submit();
        }""",
        [base, csrf],
    )
    page.wait_for_selector("form.rejectconfirm")
    # the deferred draft script has taken the form (typing before that would keep nothing)
    page.wait_for_function("() => document.querySelector('form.rejectconfirm').__draft")


def test_every_free_text_field_keeps_what_was_typed_through_a_reload(server: Server) -> None:
    """Audit F-3 (0.7.46, eccfa3d9): the deck settings' Name and Description, the editor's add and
    paste boxes, Compare, the deck's help, report, comment-report and contest boxes, the forum's
    contest entry, Test a pasted list, a pin's note and the Reject reason all keep what was typed
    through a reload (static/draft.js). The pre-push check for every free-text field on the site."""
    from playwright.sync_api import sync_playwright

    from .test_forum_page import _contest, _reply

    ark = server.ark
    _contest(ark)
    _reply(ark, 700002, "amy", "Arcane Signet, every time.")
    acct = ark.account
    acct.pins["alice"] = [
        {"id": 31, "card": acct._card(9003), "notes": "", "modifier": "Normal", "quantity": 1}
    ]
    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1280)
        try:
            for path, how, fields in FIELDS:
                page.goto(f"{server.base}{path}", wait_until="networkidle")
                _open(page, how)
                for sel, text in fields.items():
                    f = page.locator(sel).first
                    f.wait_for(state="visible")
                    f.click()
                    page.keyboard.press("Control+a")
                    f.press_sequentially(text)
                page.reload(wait_until="networkidle")
                _open(page, how)
                for sel, text in fields.items():
                    assert page.locator(sel).first.input_value() == text, (path, sel)
                assert page.locator(".draft-note:visible").count() >= 1, path
                page.evaluate("() => localStorage.clear()")
            # the Reject reason: the confirm is the answer to a post, so it is asked again
            page.goto(f"{server.base}/proposals", wait_until="networkidle")
            csrf = page.locator("input[name=csrf]").first.input_value()
            _post_reject(page, server.base, csrf)
            page.locator("#reject-reason").press_sequentially("Not this week")
            _post_reject(page, server.base, csrf)
            assert page.locator("#reject-reason").input_value() == "Not this week"
            assert errors == [], errors
        finally:
            browser.close()
