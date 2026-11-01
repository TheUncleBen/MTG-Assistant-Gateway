"""The Assistant tab's pages are titled as the tab is (0.7.46 audit QG-27, QOL-23): "Assistant:
Proposals", "Assistant: History" and "Assistant: Reports", as Binder's pages are "Binder: Cards"
and so on, so the tab and the page it opens carry the same word; the switch between them keeps
its three parts."""

from __future__ import annotations

from .conftest import Stack, stack_with
from .test_companion import COMPANION, NAV, linked_browser

stack_options = stack_with(COMPANION)


async def test_the_assistant_pages_carry_the_tabs_name(stack: Stack) -> None:
    b = await linked_browser(stack)
    try:
        for path, part in (("/proposals", "Proposals"), ("/history", "History"), ("/reports", "Reports")):
            page = (await b.http.get(path, headers=NAV)).text
            assert f"<title>Assistant: {part} · " in page, path
            assert f"<h1 class='sr-only'>Assistant: {part}</h1>" in page, path
            for other in ("Proposals", "History", "Reports"):
                assert f">{other}</a>" in page or f">{other}<" in page, (path, other)
    finally:
        await b.aclose()
