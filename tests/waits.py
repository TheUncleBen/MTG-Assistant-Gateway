"""Waits on what a browser test needs, in place of fixed sleeps (tests/test_no_fixed_waits.py).

``settled(page)``: no finite CSS transition or animation is running (a box's or a sheet's open
or close, a fold, a fade), and no box's opening is still to start, so what a test measures (sizes,
positions, colours) is the end state.
Infinite animations (a spinner, a skeleton) never end and do not count. ``getAnimations()``
flushes pending style first, so a transition a click just started is already seen.

``quiet(page)``: settled, and no Back of the page's own layers (static/layers.js) is still on its
way, and no box (static/box.js) is still closing.
"""

from __future__ import annotations

# (a box's scrim with neither "in" nor "out" is one whose opening has not started yet: static/box.js
# adds "in" in the next frame, so a look before that frame saw no transition and went on to measure
# the box at its starting scale, .98: 43.1 px for a 44 px button on CI)
SETTLED_JS = (
    "() => !document.querySelector('.mtgbox-scrim:not(.in):not(.out)')"
    " && document.getAnimations().every(a => a.playState !== 'running'"
    " || !a.effect || a.effect.getComputedTiming().iterations === Infinity)"
)

QUIET_JS = (
    "() => !(window.MtgLayers && window.MtgLayers.backPending())"
    " && !document.querySelector('.mtgbox-scrim.out')"
    " && document.getAnimations().every(a => a.playState !== 'running'"
    " || !a.effect || a.effect.getComputedTiming().iterations === Infinity)"
)


def settled(page, timeout: float = 5000, scripts: bool = True) -> None:  # noqa: ANN001
    """``scripts=False`` on a page with scripts off: its timers and animation frames never call
    back, so the test looks again from its own side."""
    if scripts:
        page.wait_for_function(SETTLED_JS, timeout=timeout)
    else:
        poll(page, lambda: page.evaluate(SETTLED_JS), timeout=timeout)


def quiet(page, timeout: float = 5000) -> None:  # noqa: ANN001
    page.wait_for_function(QUIET_JS, timeout=timeout)


def expect(*args, **kwargs):  # noqa: ANN002, ANN003, ANN201
    """Playwright's retrying ``expect``, imported when called (the browser tests skip without it)."""
    from playwright.sync_api import expect as _expect

    return _expect(*args, **kwargs)


def frame(page) -> None:  # noqa: ANN001
    """Two animation frames have passed: what the page changed is drawn, and the work it put off
    to the next frame has run."""
    page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(() => r())))")


# A synthetic press and hold (HOLD in tests/test_admin_card_models_browser.py) lifts its finger
# on a page timer; LIFT_WATCH, evaluated before it, marks that lift so ``lifted(page)`` can wait
# for it rather than for a guess at the hold's length.
LIFT_WATCH = (
    "() => { window.__lifted = false; document.addEventListener('pointerup',"
    " () => { window.__lifted = true; }, {once: true, capture: true}); }"
)


def lifted(page, timeout: float = 5000) -> None:  # noqa: ANN001
    page.wait_for_function("() => window.__lifted === true", timeout=timeout)


def form_aged(page, timeout: float = 5000) -> None:  # noqa: ANN001
    """Every render stamp on the page (``input[name=shown]``, clickguard.py) is older than the
    server's MIN_FORM_AGE, so a press now is taken (a faster one is refused as too soon). Waits on
    the page's own stamp rather than on a guess at how long the page took to come."""
    from mtg_gateway.clickguard import MIN_FORM_AGE

    page.wait_for_function(
        "(ms) => { const s = [...document.querySelectorAll('input[name=shown]')];"
        " return s.length > 0 && s.every(i => Date.now() - parseInt(i.value, 10) >= ms); }",
        arg=MIN_FORM_AGE * 1000,
        timeout=timeout,
    )


def bell_read(page, timeout: float = 5000) -> None:  # noqa: ANN001
    """The bell's count is read (static/bell.js: in an idle callback after load, from the tab's
    copy of under a minute or from /notifications/count, then kept in sessionStorage)."""
    page.evaluate("() => new Promise(r => requestIdleCallback(() => r(), {timeout: 2000}))")
    page.wait_for_function(
        "() => { try { const k = JSON.parse(sessionStorage.getItem('mtg-bell') || 'null');"
        " return !!k && Date.now() - k.at < 60000; } catch (e) { return false; } }",
        timeout=timeout,
    )


def poll(page, check, timeout: float = 5000, every: float = 25) -> None:  # noqa: ANN001
    """Until ``check()`` (state on the test's side: a route's held requests, the fake Archidekt's
    records) is true, with a deadline. Playwright delivers its events (a route, a request) while
    the test waits inside it, so the pause between two looks is Playwright's own wait."""
    import time

    end = time.monotonic() + timeout / 1000
    while not check():
        if time.monotonic() >= end:
            raise AssertionError(f"not true within {timeout} ms: {check}")
        page.wait_for_timeout(every)  # fixed wait: poll interval of a loop with a deadline


# Playwright's mouse.wheel sends the wheel and returns before the page has scrolled; SCROLL_WATCH,
# evaluated before it, marks the scroll's end (a scrollend on the page or any box in it) so
# ``scrolled(page)`` waits for that.
SCROLL_WATCH = (
    "() => { window.__scrolled = false; document.addEventListener('scrollend',"
    " () => { window.__scrolled = true; }, {once: true, capture: true}); }"
)


def scrolled(page, timeout: float = 5000) -> None:  # noqa: ANN001
    page.wait_for_function("() => window.__scrolled === true", timeout=timeout)
    frame(page)


def redrawn(page) -> None:  # noqa: ANN001
    """After a change of size (text zoom, a resize): the frame it lays out in has passed, so the
    page scripts' ResizeObserver and resize handlers have run, and what they started is settled."""
    frame(page)
    settled(page)


def history_step(page, js: str = "() => history.forward()") -> None:  # noqa: ANN001
    """Run a history step that Playwright cannot wait on (``js``: history.forward() or back()
    on an entry the page may step over at once), then wait until it has landed: its popstate
    came, and any Back the page's layers sent in answer has landed too (quiet)."""
    page.evaluate(
        "() => { if (!window.__popWatch) { window.__popWatch = true;"
        " addEventListener('popstate', () => { window.__popCount++; }); } window.__popCount = 0; }"
    )
    page.evaluate(js)
    page.wait_for_function("() => window.__popCount >= 1")
    quiet(page)


SIDEWAYS_JS = "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"


def sideways(page) -> int:  # noqa: ANN001
    """How far the page scrolls sideways (0: it does not), measured once it has settled: a card's
    transition caught halfway (a size change, a tap) can stick out for a frame on a busy machine
    (goldfish's card size at 200% text measured 2 px in a full parallel run and 0 alone). Every
    browser test measures sideways scrolling through this (tests/test_no_fixed_waits.py)."""
    settled(page, scripts=False)
    return page.evaluate(SIDEWAYS_JS)
