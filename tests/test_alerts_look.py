"""The Alerts list's look (0.7.48, the owner's notes on 0.7.47): a summary's title names its day
("Daily, Nov 22") instead of "Today" on every row, old ones too; its words leave out what is
zero; the unread dot sits by the type word, not far across the row from the ⋮."""

from __future__ import annotations

import calendar

from mtg_gateway import alerts_page, localday, summary


def _ts(y: int, m: int, d: int, h: int = 12) -> int:
    return calendar.timegm((y, m, d, h, 0, 0))


def _cards(n: int) -> list[dict]:
    if not n:
        return []
    return [{"name": "Flaming Fist", "change_pct": 5.9}] + [{"name": "X", "change_pct": 0}] * (n - 1)


def _summary(cards: int = 45, reports: int = 0, waiting: int = 0, applied: int = 0) -> dict:
    return {
        "period": "day",
        "from_day": "2026-11-21",
        "to_day": "2026-11-22",
        "cards": [{"name": "Flaming Fist", "change_pct": 5.9}]
        + [{"name": "X", "change_pct": 0}] * (cards - 1)
        if cards
        else [],
        "decks": [],
        "reports_finished": [{}] * reports,
        "proposals_waiting": waiting,
        "proposals_applied": applied,
    }


def test_a_summary_title_names_its_day() -> None:
    assert summary.push_title({"period": "day", "to_day": "2026-11-22"}) == "Summary · Daily, Nov 22"
    assert summary.push_title({"period": "week", "to_day": "2026-11-23"}) == "Summary · Weekly, Nov 23"


def test_an_old_summary_row_shows_its_own_day_not_today() -> None:
    rows = alerts_page.kept_rows(
        [
            {
                "id": 1,
                "kind": "summary",
                "title": "Summary · Today",
                "message": "m",
                "path": "/",
                "created_at": _ts(2026, 11, 20),
                "read_at": None,
            },
            {
                "id": 2,
                "kind": "summary",
                "title": "Summary · This week",
                "message": "m",
                "path": "/",
                "created_at": _ts(2026, 11, 16),
                "read_at": None,
            },
        ]
    )
    html = "".join(alerts_page.row_html(r, now=_ts(2026, 11, 22)) for r in rows)
    assert "<b class='al-title'>Daily, Nov 20</b>" in html
    assert "<b class='al-title'>Weekly, Nov 16</b>" in html
    assert ">Today<" not in html and "This week" not in html


def test_an_old_summary_row_uses_the_members_own_day() -> None:
    row = alerts_page.kept_rows(
        [
            {
                "id": 1,
                "kind": "summary",
                "title": "Summary · Today",
                "message": "m",
                "path": "/",
                "created_at": _ts(2026, 11, 20, 3),
                "read_at": None,
            }
        ]
    )[0]
    tok = localday.set_offset(-6 * 3600)  # 03:00 UTC is still Nov 19 in CST
    try:
        html = alerts_page.row_html(row, now=_ts(2026, 11, 22))
    finally:
        localday.reset_offset(tok)
    assert "<b class='al-title'>Daily, Nov 19</b>" in html


def test_the_summary_words_leave_out_what_is_zero() -> None:
    assert summary.short_text(_summary()) == "45 watched cards. Biggest move: Flaming Fist +5.9%."
    assert summary.short_text(_summary(cards=1, reports=1, waiting=2, applied=1)) == (
        "1 watched card. Biggest move: Flaming Fist +5.9%. 1 report finished. 2 proposals waiting, 1 applied."
    )
    assert summary.short_text(_summary(cards=0)) == "Nothing new."


def test_the_unread_dot_sits_by_the_type_word() -> None:
    row = alerts_page.kept_rows(
        [
            {
                "id": 7,
                "kind": "price",
                "title": "Price drop · Sol Ring",
                "message": "m",
                "path": "/",
                "created_at": _ts(2026, 11, 20),
                "read_at": None,
            }
        ]
    )[0]
    html = alerts_page.row_html(row, now=_ts(2026, 11, 22))
    meta = html.split("<span class='al-meta'>", 1)[1].split("<b class='al-title'>", 1)[0]
    assert "al-dot" in meta and meta.index("al-dot") < meta.index("al-type")
