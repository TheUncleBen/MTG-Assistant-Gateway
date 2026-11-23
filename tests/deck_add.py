"""The deck page's Add a card for the browser tests: a name typed (or picked) and Enter open the
card's preview; Add to deck saves it (0.7.48: the deck editor folded into the deck page)."""

from __future__ import annotations

PREVIEW = ".mtgbox.addbox-preview"


def add_card(page, name: str, *, category: str | None = None) -> None:  # noqa: ANN001
    """Type ``name`` into Add a card, open its preview and press Add to deck (in ``category``
    when given). Returns once the box has closed; the save itself runs in the background."""
    box = page.locator("#quick")
    box.fill(name)
    box.press("Enter")
    preview = page.locator(PREVIEW)
    preview.wait_for(timeout=8000)
    if category is not None:
        page.locator("#ap-cat").select_option(category)
    preview.locator("button", has_text="Add to deck").click()
    page.wait_for_function("() => !document.querySelector('.mtgbox.addbox-preview')", timeout=5000)
