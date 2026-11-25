"""No page grows its own version of a shared part (tests/shared_parts.py): a stylesheet rule that
restyles the title, a heading, the button, the chip, the panel, the box or the menu outside the
shared sheets; their markup written by hand outside the shared builders; a script setting their look.
Each file's count is held to tests/shared_parts_baseline.json and may only go down: moving a page
onto the shared part lowers it (``python -m tests.shared_parts --lower``), and nothing raises it."""

from __future__ import annotations

import json

from . import shared_parts as SP


def test_no_file_draws_more_of_its_own_parts_than_before() -> None:
    now = SP.census()
    base = json.loads(SP.BASELINE.read_text())
    up, _down = SP.compare(now, base)
    assert up == [], (
        "A page restyles or hand-draws a shared part (use the shared one from ui.py / theme.py; "
        "docs/UI-STANDARD.md):\n" + "\n".join(up)
    )


def test_the_census_sees_a_page_restyling_a_button_and_a_box() -> None:
    css = (
        ".mine .btn{border-radius:3px;font-size:13px;margin:0}"
        "@media (max-width:600px){.x h1{font-size:20px}}"
        ".mine .ui-box{box-shadow:none}.mine .ctxmenu a:hover{background:red}"
        ".mine .btn{margin-top:4px}"  # where it sits is not its look
    )
    got = sorted({(p, prop) for p, _s, prop in SP.restyles(css)})
    assert got == [
        ("box", "box-shadow"),
        ("button", "border-radius"),
        ("button", "font-size"),
        ("menu", "background"),
        ("title", "font-size"),
    ], got
    assert SP.MARKUP["box"].search("<div role='dialog' aria-modal='true'>")
    assert SP.SCRIPT_LOOK.search("el.style.borderRadius = '4px'")


def test_a_file_going_up_is_named_and_one_going_down_is_not_a_failure() -> None:
    up, down = SP.compare({"a.py": {"css:button": 3}}, {"a.py": {"css:button": 2}, "b.py": {"css:chip": 1}})
    assert up == ["a.py: css:button 2 -> 3"] and down == ["b.py: css:chip 1 -> 0"]
