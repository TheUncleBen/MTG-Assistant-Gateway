"""A test still going, where the member is: the running mark on a deck's page and its live update.

Home's row is home.running_row; both carry ``data-running-live`` and the reports they show, so
static/running-live.js asks /history/running for the mark again every few seconds (the step, the
games played, the time so far) and, once those tests have ended, shows that they did with the way
to the report, instead of leaving a spinner that only a reload would clear."""

from __future__ import annotations

import html
from typing import Any

from . import ui
from .theme import SPINNER, icon


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _live_attrs(shape: str, running: list[dict[str, Any]], deck_id: str = "") -> str:
    ids = " ".join(str(r["report_id"]) for r in running)
    deck = f" data-deck-id='{_esc(deck_id)}'" if deck_id else ""
    return f" data-running-live='{shape}' data-ids='{_esc(ids)}'{deck}"


def step_words(r: dict[str, Any]) -> tuple[str, str]:
    """(the step a running test is on, the time so far) for a running mark."""
    step = r.get("step") if isinstance(r.get("step"), dict) else {}
    text = str(step.get("text") or r.get("progress") or "").rstrip(".")
    if step and not text.startswith("Forge"):  # it follows "Test running:" in a sentence
        text = text[:1].lower() + text[1:]
    return text, str(step.get("elapsed_text") or "")


def deck_notice_html(running: list[dict[str, Any]], deck_id: str) -> str:
    """The deck page's mark for its tests still going: the spinner, the step in words (a polite live
    region, so a finished game is announced), the time so far beside it, and the way to the
    report. '' when nothing runs."""
    if not running:
        return ""
    one = running[0]
    text, so_far = step_words(one)
    words = f"Test running: {text}." if len(running) == 1 else f"{len(running)} tests running: {text}."
    time_html = f"<span class='rt'>{_esc(so_far[:1].upper() + so_far[1:])}.</span>" if so_far else ""
    link = ui.cap_link("Open the report", f"/history/reports/{one['report_id']}")
    return (
        f"<p class='notice testing'{_live_attrs('deck', running, deck_id)}>{SPINNER} "
        f"<span role='status'>{_esc(words)}</span> {time_html} {link}</p>"
    )


def ended_words(ended: list[dict[str, Any]]) -> str:
    """What the mark says once the tests it showed have ended: finished when every part came back,
    else that some parts did not run (the report says why)."""
    complete = all(r.get("status") == "complete" for r in ended)
    if len(ended) == 1:
        return "Test finished." if complete else "Test ended. Some parts did not run; the report says why."
    return "Tests finished." if complete else "Tests ended. Some parts did not run; the reports say why."


def deck_done_html(ended: list[dict[str, Any]]) -> str:
    """The deck page's mark once its tests have ended (no live mark: the page stops asking)."""
    if not ended:
        return ""
    link = (
        ui.cap_link("Open the report", f"/history/reports/{ended[0]['report_id']}")
        if len(ended) == 1
        else ui.cap_link("Open reports", "/reports")
    )
    return (
        f"<p class='notice testing done'>{icon('check')} "
        f"<span role='status'>{_esc(ended_words(ended))}</span> {link}</p>"
    )
