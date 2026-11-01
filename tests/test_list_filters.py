"""Search inside the Lists tables (0.7.46 audit QG-25, QOL-16): category templates, color tags,
binder tags and pinned cards carry a search box that narrows the rows on the page as you type
(the rows carry what the box matches), as Default categories already does."""

from __future__ import annotations

from .conftest import Stack, stack_with
from .test_approval_modes import SHIPPED
from .test_companion import NAV
from .test_lists_page import _member, _seed

stack_options = stack_with(SHIPPED)


async def test_every_lists_table_has_a_search_box_and_rows_it_can_match(stack: Stack) -> None:
    _seed(stack.ark)
    b = await _member(stack)
    try:
        for slug, action, names in (
            ("templates", "/lists/templates", ("Ramp", "Commander")),
            ("tags", "/lists/tags", ("Cut",)),
            ("pins", "/collection/wants", ()),
        ):
            page = (await b.http.get(action, headers=NAV)).text
            assert f"<form class='ui-tools' role='search' method='get' action='{action}'>" in page, slug
            assert "<input type='search' name='q' value='' placeholder='Name'>" in page, slug
            assert "data-filter-count" in page, slug
            for name in names:
                assert f"data-q='{name.casefold()}'" in page, (slug, name)
            # the rows that are on the page match whatever the box holds
            rows = page.count("class='tr' data-kind=")
            assert page.count(" data-q='") >= rows, slug
    finally:
        await b.aclose()
