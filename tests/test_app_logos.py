"""The connected app's own mark beside its name (Ben, 22 Oct): Claude and Claude Code show the
Claude spark, ChatGPT and Codex the OpenAI blossom (black on the light theme, white on the dark
one, following the site's own theme switch), every other app the gateway's neutral icon. The files
are the official ones, byte for byte, served as static files with a year's cache, and named in
docs/THIRD-PARTY-NOTICES.md. The browser part is skipped without a Chromium build."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest
from starlette.requests import Request
from starlette.routing import Route

from mtg_gateway import theme, ui
from mtg_gateway.companion import STATIC_DIR

from .conftest import Stack, stack_with
from .test_companion import COMPANION
from .test_decks_and_proxy import Browser

stack_options = stack_with(COMPANION)

# sha256 of the files as extracted from the press kits (anthropic.com/press-kit, openai.com/brand)
SOURCES = {
    "claude.svg": "6d53db4be375e899c937c26cf16684a80d6e869b1928d72b37748bef2560e219",
    "openai-black.svg": "75c1e9fffa5e8c437bec1d67197a73992bca45d166c6ff23215185dea8fae92a",
    "openai-white.svg": "01d158767c4eec0e47bd617e67759c33da0accd1438be1a8d29dfdb99ce87285",
}
IMG = "alt='' width='16' height='16'>"
NAV = {"Sec-Fetch-Mode": "navigate", "Accept": "text/html"}
CLAUDE_CIMD = "https://claude.ai/oauth/mcp-oauth-client-metadata"
OPENAI_CIMD = "https://chatgpt.com/oauth/client-metadata.json"


def test_the_logo_files_are_the_official_ones_byte_for_byte() -> None:
    for name, digest in SOURCES.items():
        data = (STATIC_DIR / "logos" / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == digest, name
        text = data.decode()
        # a plain picture: no script, no link, no outside reference
        assert text.lstrip().startswith("<svg") and "<script" not in text and "href" not in text
    assert sorted(p.name for p in (STATIC_DIR / "logos").iterdir()) == sorted(SOURCES)
    notice = (Path(__file__).resolve().parents[1] / "docs" / "THIRD-PARTY-NOTICES.md").read_text()
    assert "static/logos/" in notice and "Anthropic" in notice and "OpenAI" in notice
    assert "endorse" in notice.casefold() and "unmodified" in notice.casefold()


def test_each_known_app_gets_its_own_mark_and_every_other_app_the_neutral_icon() -> None:
    """The mark comes from the app's verified address (N-3: test_the_mark_follows_...): Claude and
    Claude Code from claude.ai, ChatGPT and Codex from chatgpt.com."""
    claude = f"<img class='logo' src='/static/logos/claude.svg' {IMG}"
    openai = (
        f"<img class='logo on-light' src='/static/logos/openai-black.svg' {IMG}"
        f"<img class='logo on-dark' src='/static/logos/openai-white.svg' {IMG}"
    )
    assert ui.app_logo("claude") == claude and ui.app_logo("openai") == openai
    for name in ("Claude", "claude", " Claude  Code ", "claude-code"):
        assert ui.app_chip(name, client_id=CLAUDE_CIMD) == (
            f"<span class='ui-app'>{claude}<span class='nm'>{name.strip()}</span></span>"
        )
    for name in ("ChatGPT", "chatgpt", "Codex", "CODEX"):
        assert ui.app_chip(name, client_id=OPENAI_CIMD).startswith(f"<span class='ui-app'>{openai}"), name
    # the name stays beside the mark in full, and the mark itself says nothing (alt='')
    assert ui.app_chip("ChatGPT", client_id=OPENAI_CIMD).endswith("<span class='nm'>ChatGPT</span></span>")
    chip = ui.app_chip("Claude", client_id=CLAUDE_CIMD)
    assert "alt=''" in chip and "title=" not in chip
    # anything else, and an app that gave no name: the gateway's own icon, never a guessed mark
    plug = theme.icon("app")
    assert ui.app_logo(None) == plug
    for name in ("browser", "Claudette", "Open AI", "Forge", "Claude Desktop", "Claude", "ChatGPT"):
        assert "/static/logos/" not in ui.app_chip(name), name
    assert (
        ui.app_chip("", unnamed=True)
        == f"<span class='ui-app unnamed'>{plug}<span class='nm'>Unnamed app</span></span>"
    )
    assert "/static/logos/" not in ui.app_chip(None)
    # no wording of endorsement anywhere near the chip
    for word in ("official", "partner", "endorsed", "powered by"):
        assert word not in chip.casefold()


@pytest.mark.parametrize(
    ("client_id", "info", "brand"),
    [
        (CLAUDE_CIMD, None, "claude"),
        ("https://www.anthropic.com/mcp/client.json", None, "claude"),
        (OPENAI_CIMD, None, "openai"),
        ("https://platform.openai.com/c.json", None, "openai"),
        ("reg-1", {"redirect_uris": ["https://chatgpt.com/connector_platform_oauth_redirect"]}, "openai"),
        ("reg-2", {"redirect_uris": ["https://claude.ai/api/mcp/auth_callback"]}, "claude"),
        # named like the app but sent back somewhere else, or partly: never the mark
        ("reg-3", {"client_name": "Claude", "redirect_uris": ["https://evil.example/cb"]}, None),
        (
            "reg-4",
            {"redirect_uris": ["https://claude.ai/api/mcp/auth_callback", "https://evil.example/cb"]},
            None,
        ),
        ("reg-5", {"redirect_uris": ["http://claude.ai/cb"]}, None),
        ("reg-6", {"redirect_uris": ["https://claude.ai.evil.example/cb"]}, None),
        ("reg-7", {"redirect_uris": ["http://127.0.0.1:5000/callback"]}, None),
        ("https://notclaude.ai/c.json", None, None),
        ("https://claude.ai.evil.example/c.json", None, None),
        ("http://claude.ai/c.json", None, None),
        ("unknown", None, None),
        (None, None, None),
    ],
)
def test_the_mark_follows_the_apps_verified_address_not_its_name(client_id, info, brand) -> None:  # noqa: ANN001
    """N-3 (review 2026-10-22): an app gets the official mark only when the gateway knows where it
    comes from: its metadata document's own https address (the client id itself) on claude.ai or
    anthropic.com, chatgpt.com or openai.com, or a registered app that sends members back only to
    those. The name the app gives itself never decides."""
    assert ui.client_brand(client_id, info) == brand


def test_a_chip_shows_a_mark_only_for_a_verified_app(monkeypatch: pytest.MonkeyPatch) -> None:
    plug = theme.icon("app")
    claude = f"<img class='logo' src='/static/logos/claude.svg' {IMG}"
    registered = {
        "evil": {"client_name": "Claude", "redirect_uris": ["https://evil.example/cb"]},
        "gpt": {
            "client_name": "ChatGPT",
            "redirect_uris": ["https://chatgpt.com/connector_platform_oauth_redirect"],
        },
    }
    monkeypatch.setattr(ui, "_client_info", registered.get)
    # the name alone never brings the mark: the text stays the same either way
    assert ui.app_chip("Claude") == f"<span class='ui-app'>{plug}<span class='nm'>Claude</span></span>"
    assert ui.app_chip("Claude", client_id="evil") == ui.app_chip("Claude")
    assert ui.app_chip("Claude", client_id=CLAUDE_CIMD) == (
        f"<span class='ui-app'>{claude}<span class='nm'>Claude</span></span>"
    )
    assert "/static/logos/openai-black.svg" in ui.app_chip("ChatGPT", client_id="gpt")
    assert "/static/logos/" not in ui.app_chip("Website", client_id="browser")


async def test_the_gateway_reads_a_registered_apps_addresses_for_its_mark(stack: Stack) -> None:
    """The running gateway hands the chips its own record of each registered app: one named
    "Claude" that signs members back elsewhere keeps the neutral icon on every list."""
    db = stack.h.app.state.gateway.db
    db.save_client("named-claude", {"client_name": "Claude", "redirect_uris": ["https://evil.example/cb"]})
    db.save_client(
        "real-chatgpt",
        {
            "client_name": "ChatGPT",
            "redirect_uris": ["https://chatgpt.com/connector_platform_oauth_redirect"],
        },
    )
    assert ui.client_brand("named-claude") is None
    assert "/static/logos/" not in ui.app_chip("Claude", client_id="named-claude")
    assert ui.client_brand("real-chatgpt") == "openai"
    assert "/static/logos/openai-black.svg" in ui.app_chip("ChatGPT", client_id="real-chatgpt")


def test_the_theme_switch_shows_one_openai_file_at_a_time() -> None:
    css = theme.UI_CSS
    assert ".ui-app img.logo{width:16px;height:16px" in css
    # by default (dark palette) the black file is hidden; the site's own toggle and the system
    # setting each pick one, the explicit data-theme winning over the system
    assert ".ui-app img.on-light{display:none}" in css
    assert ":root[data-theme=light] .ui-app img.on-light{display:inline-block}" in css
    assert ":root[data-theme=light] .ui-app img.on-dark{display:none}" in css
    assert ":root[data-theme=dark] .ui-app img.on-dark{display:inline-block}" in css
    assert ":root[data-theme=dark] .ui-app img.on-light{display:none}" in css
    light_sys = "@media (prefers-color-scheme: light){ :root:not([data-theme=dark]) .ui-app img.on-light{"
    dark_sys = "@media (prefers-color-scheme: dark){ :root:not([data-theme=light]) .ui-app img.on-light{"
    assert light_sys + "display:inline-block}" in css and dark_sys + "display:none}" in css
    # pages ask for the files by digest, so the browser keeps them for a year
    doc = theme.versioned_static(
        ui.app_chip("Codex", client_id=OPENAI_CIMD) + ui.app_chip("Claude", client_id=CLAUDE_CIMD)
    )
    assert re.search(r"src='/static/logos/openai-black\.svg\?v=[0-9a-f]{12}'", doc)
    assert re.search(r"src='/static/logos/openai-white\.svg\?v=[0-9a-f]{12}'", doc)
    assert re.search(r"src='/static/logos/claude\.svg\?v=[0-9a-f]{12}'", doc)


async def test_the_logos_are_served_as_static_files_with_a_long_cache(stack: Stack) -> None:
    h = stack.h
    for name in SOURCES:
        r = await h.http.get(f"/static/logos/{name}")
        assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml"), name
        assert hashlib.sha256(r.content).hexdigest() == SOURCES[name]
        assert r.headers["cache-control"] == "public, max-age=300"
        assert r.headers["x-content-type-options"] == "nosniff"
        v = theme.static_version(f"logos/{name}")
        kept = await h.http.get(f"/static/logos/{name}?v={v}")
        assert kept.status_code == 200
        assert kept.headers["cache-control"] == "public, max-age=31536000, immutable"
        assert (await h.http.get(f"/static/logos/{name}?v=old")).headers[
            "cache-control"
        ] == "public, max-age=300"
    for bad in (
        "/static/logos/%2e%2e/companion.js",
        "/static/logos%2f..%2fcompanion.js",
        "/static/logos/nope.svg",
        "/static/logos/",
        "/static/logos/a/b.svg",
    ):
        assert (await h.http.get(bad)).status_code == 404, bad
    # the page's own CSP keeps img-src to the gateway itself, which the logos are
    b = Browser(h)
    await b.login()
    try:
        page = await b.http.get("/account", headers=NAV)
        assert page.status_code == 200
        assert "img-src 'self'" in page.headers.get("content-security-policy", "")
    finally:
        await b.aclose()


# --- in the browser: the right file shows, 16 px, legible on its background at 344 px ------------

pytest.importorskip("playwright")

from .test_scan_browser import _chromium_path  # noqa: E402
from .test_suggest_browser import Server  # noqa: E402
from .test_ui_kit_browser import CONTRAST_JS  # noqa: E402


class ChipServer(Server):
    """The test gateway with a page of app chips as the lists show them (/app-chips)."""

    def __init__(self, tmp_path: Path):
        super().__init__(tmp_path)

        async def chips(_request: Request):
            rows = "".join(
                f"<p class='row'>{ui.app_chip(a, client_id=c)} proposed a change</p>"
                for a, c in (
                    ("Claude", CLAUDE_CIMD),
                    ("Claude Code", CLAUDE_CIMD),
                    ("ChatGPT", OPENAI_CIMD),
                    ("Codex", OPENAI_CIMD),
                    ("browser", "browser"),
                    (None, None),
                )
            )
            return theme.render(
                "App chips", f"<div class='chips'>{rows}</div>", site="MTG Gateway", signed_in=True, csrf="x"
            )

        self.app.router.routes.insert(0, Route("/app-chips", chips))


@pytest.fixture
def chip_server(tmp_path: Path):
    s = ChipServer(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


LOGOS_JS = """
() => [...document.querySelectorAll('.ui-app img.logo')].map(img => {
  const r = img.getBoundingClientRect();
  return { src: img.getAttribute('src').split('?')[0].split('/').pop(), alt: img.getAttribute('alt'),
    shown: getComputedStyle(img).display !== 'none' && r.width > 0, w: r.width, h: r.height,
    loaded: img.complete && img.naturalWidth > 0, name: img.parentElement.querySelector('.nm').textContent };
})
"""
BG_JS = "() => getComputedStyle(document.body).backgroundColor"


def _lum(rgb: str) -> float:
    m = [int(x) for x in re.findall(r"\d+", rgb)[:3]]
    return sum(w * (c / 255) for w, c in zip((0.2126, 0.7152, 0.0722), m, strict=True))


@pytest.mark.parametrize("how", ["cookie", "system"])
@pytest.mark.parametrize("want", ["light", "dark"])
def test_the_openai_mark_follows_the_sites_theme_and_every_mark_reads_at_344_px(
    chip_server: ChipServer, want: str, how: str
) -> None:
    """The site's own toggle (the mtg_theme cookie) decides, and without a choice the system setting
    does; the chip is 16 px, the name keeps 4.5:1 and the page never scrolls sideways at 344 px."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = chip_server.sign_in()
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        # the cookie must win over a system setting that says the opposite
        scheme = want if how == "system" else ("dark" if want == "light" else "light")
        ctx = browser.new_context(viewport={"width": 344, "height": 882}, color_scheme=scheme, has_touch=True)
        cookies = [{"name": "mtg_session", "value": sid, "url": chip_server.base}]
        if how == "cookie":
            cookies.append({"name": "mtg_theme", "value": want, "url": chip_server.base})
        ctx.add_cookies(cookies)
        page = ctx.new_page()
        page.goto(f"{chip_server.base}/app-chips", wait_until="networkidle")
        logos = page.evaluate(LOGOS_JS)
        bg = page.evaluate(BG_JS)
        wide = page.evaluate(
            "() => document.documentElement.scrollWidth > document.documentElement.clientWidth"
        )
        texts = page.evaluate(CONTRAST_JS)
        browser.close()
    assert not wide
    dark_page = _lum(bg) < 0.5
    assert dark_page == (want == "dark"), (want, bg)
    shown = {(x["name"], x["src"]) for x in logos if x["shown"]}
    assert shown == {
        ("Claude", "claude.svg"),
        ("Claude Code", "claude.svg"),
        ("ChatGPT", "openai-white.svg" if want == "dark" else "openai-black.svg"),
        ("Codex", "openai-white.svg" if want == "dark" else "openai-black.svg"),
    }, shown
    for x in logos:
        assert x["alt"] == "" and x["loaded"], x
        if x["shown"]:
            assert 16 <= x["w"] <= 18 and 16 <= x["h"] <= 18, x
    # the names beside the marks keep the label contrast
    names = [c for t, c in texts if t.startswith("ui-app")]
    assert names and min(names) >= 4.5, texts
