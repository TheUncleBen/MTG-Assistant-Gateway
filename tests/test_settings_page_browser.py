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
from .test_text_zoom_browser import ROOT_200, TEXT_200

pytest.importorskip("playwright")

SHOTS = os.environ.get("MTG_SETTINGS_SHOTS")
TEXT = (
    ".set-group h2, .set-group summary, .set-label, .set-help, .set-src, .set-err, .range, .unit, "
    ".set-seg label, .set-choices label, .set-confirm, .set-nav a, .set-save button, .reset, .notice, "
    ".notice li, .set-warn, code"
)

# Every visible control at least as tall as the site's standard control (--ctl, 39 px).
TAPS = """
() => [...document.querySelectorAll(
    '.set-group:not(#g-scan) input[type=number], .set-group:not(#g-scan) .msel-btn,'
    + ' .set-seg label, .set-choices label, .set-save button, .set-nav a, .reset')]
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
        assert err.is_visible() and "Must be between 1 and 50" in err.inner_text()
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


# Each named choice shows its whole name: no label clipped or cut with "…".
CHOICES_WHOLE = """
() => [...document.querySelectorAll('.set-choices label')]
  .filter(l => l.scrollWidth > l.clientWidth + 1 || l.scrollHeight > l.clientHeight + 1)
  .map(l => l.textContent.trim())
"""


@pytest.mark.parametrize(
    ("width", "zoom"),
    [(344, ""), (360, ""), (1280, ""), (360, "root"), (673, "root"), (360, "text"), (673, "text")],
)
def test_review_round_1_settings_details(server: Server, width: int, zoom: str) -> None:
    """Review round 1: a locked setting's variable name stays on one line (finding 4), the approval
    modes show their whole names at any width and text size (9), a refused value doesn't paint the
    tick box red (9), and Back to default keeps the section's other unsaved values (6)."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid = _admin(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, "light", exe)
        page.goto(f"{server.base}/admin/settings", wait_until="networkidle")
        if zoom:  # 200% text, as test_text_zoom_browser makes it
            page.evaluate(ROOT_200 if zoom == "root" else TEXT_200)
        code = page.locator("#s-forge_games code")
        assert code.inner_text() == "MTG_FORGE_GAMES"
        # one line: a single box no taller than two of its own letters
        assert code.evaluate(
            "c => c.getClientRects().length === 1"
            " && c.getBoundingClientRect().height < 2 * parseFloat(getComputedStyle(c).fontSize)"
        )
        modes = page.locator("#s-row-approval_mode_max .set-choices label")
        assert modes.all_inner_texts() == [
            "Ask me every time",
            "Apply small, low-risk edits without asking",
            "Apply deck and collection changes without asking",
        ]
        assert page.evaluate(CHOICES_WHOLE) == []
        assert page.evaluate(MEASURE) == []
        assert page.evaluate(CONTRAST, TEXT) == []
        _shot(page, f"settings-r1-{width}-{zoom or 'plain'}.png", page.locator("#g-approvals"))
        if zoom:
            browser.close()
            assert errors == []
            return

        # a refused value next to a lowered one: only the field gets the red border
        page.fill("#f-archidekt_backup_copies", "99")
        page.fill("#f-snapshots_per_deck", "10")
        page.locator("#g-backups button.primary").click()
        page.wait_for_load_state("networkidle")
        tick = page.locator("#s-row-snapshots_per_deck input[type=checkbox]")
        red = page.locator("#e-archidekt_backup_copies").evaluate("e => getComputedStyle(e).color")
        assert tick.evaluate("t => getComputedStyle(t).borderTopColor") != red
        assert (
            page.locator("#f-archidekt_backup_copies").evaluate("e => getComputedStyle(e).borderTopColor")
            == red
        )

        # Back to default with another unsaved value in the section: kept, and said
        page.goto(f"{server.base}/admin/settings", wait_until="networkidle")
        page.locator("#s-row-backup_hour_utc input").fill("5")
        page.locator("#g-backups button.primary").click()
        page.wait_for_load_state("networkidle")
        page.fill("#f-backup_keep_days", "20")
        page.locator("#s-row-backup_hour_utc .reset").click()
        page.wait_for_load_state("networkidle")
        assert page.input_value("#f-backup_keep_days") == "20"
        assert page.input_value("#s-row-backup_hour_utc input") == "3"
        warn = page.locator(".notice.warn", has_text="Not saved yet")
        assert warn.inner_text() == (
            "Not saved yet: “Days of nightly backups kept”. What you typed is still in the form; "
            "press “Save Backups” to keep it."
        )
        assert page.evaluate(CONTRAST, TEXT) == []
        browser.close()
    assert errors == []


# Ben, 10-16 19:45Z: a setting the stack sets is shown where it would be changed, greyed out, with
# its value, and nothing on the page can change it.
LOCKED = {
    "forge_games": 12,
    "archidekt_backups": False,
    "approval_mode_max": "semi",
    "server_name": "Our Table",
    "log_level": "WARNING",
    "deck_sync_minutes": 60,
}

# Per locked row: its controls' state, shown value, text contrast over the greyed background
# (opacity and alpha blended in), and the background against an editable control's.
LOCKED_ROWS = """
(keys) => {
  function rgb(s) { const m = s.match(/[\\d.]+/g); return m ? m.map(Number) : [0, 0, 0, 1]; }
  function lum(c) {
    const v = c.slice(0, 3).map(x => {
      x /= 255;
      return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2];
  }
  function bg(el) {
    for (let e = el; e; e = e.parentElement) {
      const c = rgb(getComputedStyle(e).backgroundColor);
      if (c.length < 4 || c[3] > 0.9) return c;
    }
    return rgb(getComputedStyle(document.body).backgroundColor);
  }
  function ratio(el) {
    const cs = getComputedStyle(el), b = bg(el), f = rgb(cs.webkitTextFillColor || cs.color);
    const op = parseFloat(cs.opacity);
    let c = f.length > 3 ? f.slice(0, 3).map((x, i) => x * f[3] + b[i] * (1 - f[3])) : f;
    if (op < 1) c = c.map((x, i) => x * op + b[i] * (1 - op));
    const a = lum(c), z = lum(b);
    return (Math.max(a, z) + 0.05) / (Math.min(a, z) + 0.05);
  }
  const editable = getComputedStyle(document.querySelector('#f-proposal_expiry_days')).backgroundColor;
  return keys.map(key => {
    const row = document.getElementById('s-row-' + key);
    const ctls = [...row.querySelectorAll('input, select, button')];
    const shown = ctls.filter(c => c.type !== 'radio' || c.checked)
      .map(c => c.type === 'radio' ? c.value : (c.classList.contains('msel-btn') ? c.innerText : c.value))
      .filter(v => v);
    const faces = [...row.querySelectorAll(
      'input:not([type=radio]), .msel-btn, .set-seg label, .set-choices label')]
      .filter(e => e.getBoundingClientRect().width);
    return {
      key,
      locked: row.classList.contains('locked'),
      line: row.querySelector('.set-lock') && row.querySelector('.set-lock').innerText,
      all_disabled: ctls.length > 0 && ctls.every(c => c.disabled),
      shown,
      low: faces.map(e => ratio(e)).filter(r => r < 4.5).map(r => r.toFixed(2)),
      grey: faces.every(e => getComputedStyle(e).backgroundColor !== editable),
      reset: !!row.querySelector('.reset'),
      tick: !!row.querySelector('.set-confirm'),
    };
  });
}
"""


@pytest.fixture
def locked_server(tmp_path: Path):
    s = Server(tmp_path, admin_group="mtg-admins", env_set=frozenset(LOCKED), **LOCKED)
    s.start()
    try:
        yield s
    finally:
        s.stop()


@pytest.mark.parametrize("width,theme", [*SIZES, (673, "light"), (673, "dark"), (344, "light")])
def test_a_setting_the_stack_sets_shows_greyed_out_and_cannot_be_changed(
    locked_server: Server, width: int, theme: str
) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid = _admin(locked_server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, locked_server, sid, width, theme, exe)
        page.goto(f"{locked_server.base}/admin/settings", wait_until="networkidle")
        rows = {r["key"]: r for r in page.evaluate(LOCKED_ROWS, list(LOCKED))}
        for key, r in rows.items():
            env = "MTG_" + key.upper()
            assert r["locked"] and r["all_disabled"] and not r["reset"] and not r["tick"], r
            assert r["line"] == (
                f"Set in your stack ({env}), so it can't be changed here. Change or remove it in your "
                "stack, then redeploy."
            )
            assert r["low"] == [] and r["grey"], r
        assert rows["forge_games"]["shown"] == ["12"]
        assert rows["archidekt_backups"]["shown"] == ["off"]
        assert rows["approval_mode_max"]["shown"] == ["semi"]
        assert rows["server_name"]["shown"] == ["Our Table"]
        assert "WARNING" in rows["log_level"]["shown"]
        assert rows["deck_sync_minutes"]["shown"] == ["60"]
        # the keyboard never lands on a locked control
        landed = set()
        for _ in range(150):
            page.keyboard.press("Tab")
            landed.add(page.evaluate("() => (document.activeElement.closest('.set-row') || {}).id || ''"))
        assert not {f"s-row-{k}" for k in LOCKED} & landed
        # a screen reader hears the row's name, that it is unavailable, and the stack line
        num = page.locator("#f-forge_games")
        assert num.get_attribute("disabled") is not None
        assert "s-forge_games" in num.get_attribute("aria-describedby")
        assert page.evaluate(MEASURE) == []
        assert page.evaluate(CONTRAST, TEXT) == []
        _shot(page, f"settings-locked-{width}-{theme}.png", page.locator("#g-general"))
        _shot(page, f"settings-locked-approvals-{width}-{theme}.png", page.locator("#g-approvals"))
        browser.close()
    assert errors == []


# Where a locked row's variable name breaks onto a new line: each break must follow an underscore.
MIDWORD_BREAKS = """
() => [...document.querySelectorAll('.set-lock code')].flatMap(c => {
  const text = c.textContent, out = [];
  const walker = document.createTreeWalker(c, NodeFilter.SHOW_TEXT);
  const chars = [];
  for (let n = walker.nextNode(); n; n = walker.nextNode())
    for (let i = 0; i < n.length; i++) chars.push([n, i]);
  let top = null;
  chars.forEach(([n, i], k) => {
    const r = document.createRange(); r.setStart(n, i); r.setEnd(n, i + 1);
    const t = Math.round(r.getBoundingClientRect().top);
    if (top !== null && t > top + 2 && text[k - 1] !== '_') out.push(text.slice(0, k) + '|' + text.slice(k));
    top = t;
  });
  return out;
})
"""

# A locked On/Off or approval-mode row: each label's words, the chosen one, and the radios' own look
# (in On/Off they stay invisible over their label, in the mode list plain: no grey dashed face).
LOCKED_CHOICES = """
(keys) => keys.map(key => {
  const row = document.getElementById('s-row-' + key);
  const labels = [...row.querySelectorAll('.set-seg label, .set-choices label')];
  return {
    key,
    words: labels.map(l => l.innerText.trim()),
    chosen: labels.filter(l => l.querySelector('input').checked).map(l => l.innerText.trim()),
    segInputs: [...row.querySelectorAll('.set-seg input')].map(i => getComputedStyle(i).opacity),
    dashedRadios: [...row.querySelectorAll('input[type=radio]')]
      .filter(i => getComputedStyle(i).borderTopStyle === 'dashed').length,
  };
})
"""


@pytest.mark.parametrize(
    ("width", "theme", "zoom"),
    [
        (344, "light", "text"),
        (360, "light", "text"),
        (360, "dark", "root"),
        (1280, "light", ""),
        (1280, "dark", ""),
    ],
)
def test_a_locked_setting_shows_its_value_in_words_and_its_variable_whole(
    locked_server: Server, width: int, theme: str, zoom: str
) -> None:
    """Review round 2: a locked On/Off row shows "On"/"Off", not the radios on top of them (R2-1); the
    approval modes keep plain radios (no dashed ring); and at 200% text the variable name breaks
    only after an underscore (finding 4)."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid = _admin(locked_server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, locked_server, sid, width, theme, exe)
        page.goto(f"{locked_server.base}/admin/settings", wait_until="networkidle")
        if zoom:
            page.evaluate(ROOT_200 if zoom == "root" else TEXT_200)
        assert page.evaluate(MIDWORD_BREAKS) == []
        rows = {
            r["key"]: r for r in page.evaluate(LOCKED_CHOICES, ["archidekt_backups", "approval_mode_max"])
        }
        backups, modes = rows["archidekt_backups"], rows["approval_mode_max"]
        assert backups["words"] == ["On", "Off"] and backups["chosen"] == ["Off"], backups
        assert backups["segInputs"] and set(backups["segInputs"]) == {"0"}, backups
        semi = "Apply small, low-risk edits without asking"
        assert len(modes["words"]) == 3 and modes["chosen"] == [semi], modes
        assert backups["dashedRadios"] == 0 and modes["dashedRadios"] == 0
        assert page.evaluate(MEASURE) == []
        assert page.evaluate(CONTRAST, TEXT) == []
        _shot(page, f"settings-locked-r2-{width}-{theme}-{zoom or 'plain'}.png", page.locator("#g-backups"))
        _shot(
            page,
            f"settings-locked-r2-modes-{width}-{theme}-{zoom or 'plain'}.png",
            page.locator("#g-approvals"),
        )
        browser.close()
    assert errors == []
