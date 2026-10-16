"""The admin Settings page in headless Chromium at 360, 720 (Fold) and 1280 px, light and dark:
every row fits, text reads at 4.5:1 or better, nothing scrolls sideways, the controls are big
enough to tap, a save and a refused value both show their message, and a locked setting can't be
changed. Set MTG_SETTINGS_SHOTS=<dir> to also save screenshots. Skipped without a Chromium build."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from .test_card_search_filters_browser import CONTRAST
from .test_cardsearch_browser import SIZES, _chromium, _page
from .test_overflow_sweep_browser import MEASURE
from .test_suggest_browser import Server

pytest.importorskip("playwright")

SHOTS = os.environ.get("MTG_SETTINGS_SHOTS")
TEXT = (
    ".set-group h2, .set-group summary, .set-label, .set-help, .set-src, .set-err, .range, .unit, "
    ".set-seg label, .set-confirm, .set-nav a, .set-save button, .reset, .notice, .notice li, code"
)

# Every visible control at least as tall as the site's standard control (--ctl, 39 px).
TAPS = """
() => [...document.querySelectorAll(
    '.set-group:not(#g-scan) input[type=number], .set-group:not(#g-scan) .msel-btn,'
    + ' .set-seg label, .set-save button, .set-nav a, .reset')]
  .filter(e => e.getBoundingClientRect().width)
  .filter(e => e.getBoundingClientRect().height < 38.5)
  .map(e => (e.id || e.textContent.trim()).slice(0, 30) + ' ' + Math.round(e.getBoundingClientRect().height))
"""


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path, admin_group="mtg-admins", forge_games=12, env_set=frozenset({"forge_games"}))
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _admin(server: Server) -> str:
    sid = server.sign_in()
    server.db.upsert_user(
        "user-1", email="alice@example.test", name="Alice", preferred_username="alice", groups=["mtg-admins"]
    )
    return sid


def _shot(page, name: str, locator=None) -> None:
    if not SHOTS:
        return
    Path(SHOTS).mkdir(parents=True, exist_ok=True)
    if locator is None:
        page.screenshot(path=str(Path(SHOTS) / name), full_page=True)
    else:
        locator.screenshot(path=str(Path(SHOTS) / name))


@pytest.mark.parametrize("width,theme", SIZES)
def test_the_settings_page_reads_and_works_everywhere(server: Server, width: int, theme: str) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid = _admin(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        page.goto(f"{server.base}/admin/settings", wait_until="networkidle")
        assert page.locator(".set-row").count() >= 40
        assert page.evaluate(MEASURE) == []
        assert page.evaluate(CONTRAST, TEXT) == []
        assert page.evaluate(TAPS) == []
        _shot(page, f"settings-{width}-{theme}.png")

        # the deployment's value: shown, not editable
        forge = page.locator("#f-forge_games")
        assert forge.is_disabled() and forge.input_value() == "12"
        assert "MTG_FORGE_GAMES" in page.locator("#s-forge_games").inner_text()

        # a refused value: nothing saved, the message by the field, the typed value kept
        page.fill("#f-archidekt_backup_copies", "99")
        page.locator("#g-backups button.primary").click()
        page.wait_for_load_state("networkidle")
        err = page.locator("#e-archidekt_backup_copies")
        assert err.is_visible() and "must be between 1 and 50" in err.inner_text()
        assert page.input_value("#f-archidekt_backup_copies") == "99"
        assert "e-archidekt_backup_copies" in page.get_attribute(
            "#f-archidekt_backup_copies", "aria-describedby"
        )
        assert page.evaluate(CONTRAST, TEXT) == []
        assert page.evaluate(MEASURE) == []
        _shot(page, f"settings-error-{width}-{theme}.png", page.locator("#g-backups"))

        # a save: the notice lists the change, and the row says who changed it
        page.fill("#f-archidekt_backup_copies", "12")
        page.locator("#s-row-backup_hour_utc input").fill("5")
        page.locator("#g-backups button.primary").click()
        page.wait_for_load_state("networkidle")
        assert "saved=backups" in page.url
        notice = page.locator(".notice.ok")
        assert notice.is_visible()
        assert "Backup copies kept per deck: 10 → 12" in notice.inner_text()
        assert "Nightly backup hour (UTC): 3 → 5 (after a restart)" in notice.inner_text()
        src = page.locator("#s-backup_hour_utc").inner_text()
        assert "Changed here by alice" in src and "uses 3 until it restarts" in src
        assert page.evaluate(CONTRAST, TEXT) == []
        assert page.evaluate(MEASURE) == []
        _shot(page, f"settings-saved-{width}-{theme}.png", page.locator("main"))

        # the On/Off switch works from the keyboard
        page.focus("#s-row-update_check input[value=on]")
        page.keyboard.press("ArrowRight")
        assert page.is_checked("#s-row-update_check input[value=off]")

        # the scan thresholds open, and fit
        page.locator("#g-scan summary").click()
        assert page.locator("#f-scan_art_max_distance").is_visible()
        assert page.evaluate(MEASURE) == []
        browser.close()
    assert errors == []
