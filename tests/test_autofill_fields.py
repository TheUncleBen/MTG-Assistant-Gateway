"""No form field reads as a payment card's to the browser (Ben, 2026-10-28: Android offered the
saved bank cards on the card name boxes). Browsers guess a field's purpose from its id, its name,
its label, its placeholder and its aria-label; words such as "card", "number", "cc", "cvc" and
"expiry" there make Chrome on Android treat a box as a payment card's. So none of a page's text
fields and selects carries them. Hidden fields (never filled by a person), checkboxes and radio
buttons, and text areas (autofill fills none of them) are left out, and so is "cards", a list's
word (the site search's "Search cards and decks"). The server still takes the old field names,
from a page opened before an update."""

from __future__ import annotations

import re
from html.parser import HTMLParser

from mtg_gateway.lists_page import custom_add_box

from .conftest import Stack
from .test_companion import NAV, linked_browser
from .test_deck_categories import _give_categories
from .test_deck_categories_page import POST, _csrf, _ids

# "cc" only as a word of its own ("cc-add", not "accept" or "account"); "card" but not "cards"
PAYMENT = re.compile(r"card(?!s\b)|number|cvc|cvv|csc|expir|credit|(?<![a-z])cc(?![a-z])", re.IGNORECASE)
SKIP_TYPES = {"hidden", "checkbox", "radio", "submit", "button", "reset", "image", "file", "range", "color"}


class _Fields(HTMLParser):
    """Each field with its id, name, placeholder, aria-label and label text (a label it sits in,
    or one that names it with ``for``; the label's own words, not a select's options)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.fields: list[dict[str, str]] = []
        self.labels: list[dict] = []  # open labels: {for, text, fields}
        self.by_for: dict[str, list[str]] = {}
        self.inside = 0  # depth inside a select or textarea (their text is not the label's)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        if tag == "label":
            self.labels.append({"for": a.get("for", ""), "text": [], "fields": []})
        elif tag in ("input", "select"):
            if tag == "select" or a.get("type", "text").lower() not in SKIP_TYPES:
                f = {k: a.get(k, "") for k in ("id", "name", "placeholder", "aria-label")}
                f["tag"] = tag
                f["label"] = ""
                self.fields.append(f)
                if self.labels:
                    self.labels[-1]["fields"].append(f)
        if tag in ("select", "textarea"):
            self.inside += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("select", "textarea") and self.inside:
            self.inside -= 1
        elif tag == "label" and self.labels:
            lab = self.labels.pop()
            text = " ".join("".join(lab["text"]).split())
            for f in lab["fields"]:
                f["label"] += " " + text
            if lab["for"]:
                self.by_for.setdefault(lab["for"], []).append(text)

    def handle_data(self, data: str) -> None:
        if self.labels and not self.inside:
            self.labels[-1]["text"].append(data)


def payment_words(page: str) -> list[str]:
    """Each field whose id, name, label, placeholder or aria-label has a payment card's word."""
    p = _Fields()
    p.feed(page)
    out = []
    for f in p.fields:
        f["label"] += " " + " ".join(p.by_for.get(f["id"], []))
        for key in ("id", "name", "label", "placeholder", "aria-label"):
            m = PAYMENT.search(f[key])
            if m:
                out.append(f"<{f['tag']} id={f['id']!r} name={f['name']!r}> {key} {f[key].strip()!r}")
    return out


def test_the_check_finds_payment_words_and_leaves_the_rest() -> None:
    page = (
        "<label for='a'>Card name</label><input id='a' name='q'>"
        "<label>Number (optional)<input id='b' name='x'></label>"
        "<input id='cc-add-qty' name='quantity'>"
        "<select id='s' name='deck'><option>Card</option></select><textarea name='card'></textarea>"
        "<label for='site-q'>Search cards and decks</label><input id='site-q' name='q'>"
        "<input type='hidden' name='card'><input type='checkbox' name='card'>"
        "<input id='ok' name='accept' placeholder='Account name' aria-label='Name'>"
    )
    found = payment_words(page)
    assert len(found) == 3, found
    assert any("Card name" in x for x in found) and any("Number" in x for x in found)
    assert any("cc-add-qty" in x for x in found)


async def test_no_page_field_reads_as_a_payment_cards(stack: Stack) -> None:
    _give_categories(stack.ark)
    b = await linked_browser(stack)
    try:
        bad: list[str] = []
        for path in (
            "/cards?q=Sol%20Ring",
            "/collection",
            "/watchlist",
            "/decks/42/edit",
            "/decks/42/settings",
            "/decks/42/registration",
            "/lists/custom",
        ):
            r = await b.http.get(path, headers=NAV)
            assert r.status_code == 200, (path, r.status_code)
            bad += [f"{path}: {x}" for x in payment_words(r.text)]
        # deleting a category that still has cards asks where they go
        page, csrf = await _csrf(b)
        r = await b.http.post(
            POST, data={"csrf": csrf, "action": "delete", "delete": str(_ids(page)["Ramp"])}, headers=NAV
        )
        assert r.status_code == 200 and "Delete the category" in r.text, r.text
        bad += [f"category delete: {x}" for x in payment_words(r.text)]
        # a custom card's Add to deck box
        box = custom_add_box(
            "0" * 32, {"values": {"name": "Homebrew", "id": 1}, "decks": [{"id": 42, "name": "D"}]}
        )
        bad += [f"custom add box: {x}" for x in payment_words(box)]
        assert bad == [], "\n".join(bad)
    finally:
        await b.aclose()


async def test_the_forms_new_field_names_are_taken(stack: Stack) -> None:
    """The renamed fields (cn, cover, dci_id, move_to) reach what they set."""
    _give_categories(stack.ark)
    uid = "be68e315-ffef-40a8-8a46-1c042b148c03"
    stack.ark.decks[42]["cards"][0]["card"]["uid"] = uid
    b = await linked_browser(stack)
    try:
        csrf = await b.csrf("/decks")
        r = await b.http.post(
            "/watchlist",
            data={"csrf": csrf, "action": "add_card", "name": "sol ring", "set_code": "FRC", "cn": "21"},
        )
        assert r.status_code == 303, r.text
        assert "FRC 21" in (await b.http.get("/watchlist")).text
        r = await b.http.post("/decks/42/cover", data={"csrf": csrf, "cover": uid})
        assert r.status_code == 303 and stack.ark.updates[-1]["customFeatured"].endswith(f"/{uid}.webp?11")
        r = await b.http.post("/decks/42/registration", data={"csrf": csrf, "dci_id": "x"})
        assert r.status_code == 400 and "digits only" in r.text, r.text
        page, csrf = await _csrf(b)
        ramp = _ids(page)["Ramp"]
        r = await b.http.post(
            POST,
            data={
                "csrf": csrf,
                "action": "delete",
                "delete": str(ramp),
                "move_to": "Commander",
                "confirmed": "1",
            },
            headers=NAV,
        )
        assert r.status_code == 303 and ramp in stack.ark.category_deletes, r.text
    finally:
        await b.aclose()
