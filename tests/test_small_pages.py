"""The small pages share one look (smallpage.py): a centred panel with the title, one or two
plain sentences and the way forward as buttons, one of them orange. Sign-in failures read plainly
(no setting names, no raw codes, never "verify"), the expired-link pages tell website and
assistant sign-ins apart, the data-deleted list matches the Account card word for word, and the
form-expired, too-large, 404, 500 and 503 answers all get a button."""

from __future__ import annotations

import html
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from mtg_gateway import pages, smallpage
from mtg_gateway.app_signin import challenge_of

from .conftest import GATEWAY, FakeIdP, Harness, consent_stamp, make_settings, running
from .test_decks_and_proxy import Browser

NAV = {"Sec-Fetch-Mode": "navigate", "Accept": "text/html"}
APP_UA = {"User-Agent": "Mozilla/5.0 (Linux; Android 14) MTGAssistant/0.7.30"}


def panel(text: str, title: str) -> str:
    """The one panel of a small page, with its title inside it (the page's h1)."""
    assert text.count("<section class='card sio-p'>") == 1, text[:400]
    assert f"<section class='card sio-p'><h1>{html.escape(title)}</h1>" in text, text[:600]
    return text.split("<section class='card sio-p'>", 1)[1].split("</section>", 1)[0]


def orange(text: str) -> int:
    return len(re.findall(r"class='btn btn-primary'|class='primary'|class='btn-primary'", text))


def has_button(part: str, text: str, href: str, *, primary: bool = False) -> bool:
    cls = "btn btn-primary" if primary else "btn btn-plain"
    return f"<a class='{cls}' href='{html.escape(href)}'" in part and f">{text}</a>" in part


async def test_signed_out_and_data_deleted_share_the_panel(gw: Harness) -> None:
    out = await gw.http.get("/signed-out", headers=NAV)
    body = panel(out.text, "Signed out")
    assert "<p>You are signed out of this gateway.</p>" in body
    assert has_button(body, "Sign in again", "/login?next=/", primary=True) and orange(body) == 1
    assert "<style>" in out.text and ".sio{" in out.text and "/static/pages.js" in out.text

    gone = await gw.http.get("/data-deleted", headers=NAV)
    body = panel(gone.text, "Your data was deleted")
    # the list of what was deleted is the Account card's list, word for word
    assert f"deleted from its database: {pages.DELETED_LIST}. You are signed out.</p>" in body
    assert "your simulator card models" in body and "deck covers" in body and "your watchlist" in body
    # what was not touched sits in a tinted strip; backups and the security log stay small print
    assert (
        "<div class='notice'><strong>Not touched:</strong> your decks on Archidekt, including any backup "
        "copies the gateway made there.</div>" in body
    )
    assert "security log keeps a record" in body.split("<p class='muted small'>", 1)[1]
    assert has_button(body, "Sign in again", "/login?next=/", primary=True) and orange(body) == 1


async def test_the_account_card_and_the_deleted_page_list_the_same_things() -> None:
    import inspect

    from mtg_gateway import db as dbmod

    # the code still deletes each thing the list names (deck covers go by owner, after the tables)
    deletes = " ".join(table for table, _column in dbmod.MEMBER_TABLES) + inspect.getsource(
        dbmod.delete_member_rows
    )
    for word in ("card_model", "watch", "deck_covers", "scan", "report", "snapshot", "proposal"):
        assert word in deletes, word
    for named in ("simulator card models", "watchlist", "deck covers", "scan sessions", "test reports"):
        assert named in pages.DELETED_LIST, named


async def test_an_invalid_app_sign_in_link_is_the_plain_failed_page(gw: Harness) -> None:
    r = await gw.http.get(
        "/login", params={"next": "/decks", "app_challenge": "not-a-challenge"}, headers=NAV
    )
    assert r.status_code == 400
    body = panel(r.text, "Sign-in failed")
    assert f"<p>{html.escape(smallpage.SIGNIN_FAILED)}</p>" in body
    assert has_button(body, "Try again", "/login?next=/decks", primary=True) and has_button(body, "Home", "/")
    assert "not valid" not in body and "verify" not in r.text


async def test_canceling_at_the_identity_provider_offers_sign_in_again(gw: Harness) -> None:
    r = await gw.http.get("/login", params={"next": "/decks"})
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    back = await gw.http.get("/auth/callback", params={"state": state, "error": "access_denied"}, headers=NAV)
    assert back.status_code == 200
    body = panel(back.text, "Sign-in canceled")
    assert f"<p>{smallpage.CANCELED}</p>" in body and "identity provider" not in body
    assert has_button(body, "Sign in again", "/login?next=/decks", primary=True) and has_button(
        body, "Home", "/"
    )


async def test_a_website_sign_in_link_that_expired_or_moved_browsers(gw: Harness) -> None:
    # expired or already used: no login session is left to say which flow it was, so the page
    # gives the website's words (Sign in again)
    r = await gw.http.get("/auth/callback", params={"state": "no-such-login", "code": "x"}, headers=NAV)
    assert r.status_code == 400
    body = panel(r.text, "Sign-in link expired")
    assert f"<p>{smallpage.EXPIRED_WEB}</p>" in body and "AI client" not in body
    assert has_button(body, "Sign in again", "/login", primary=True) and orange(body) == 1
    # started in a different browser: the same page (the detail is the log's), as a 403
    start = await gw.http.get("/login", params={"next": "/decks"})
    cb = await gw.idp_leg(start)
    other = await gw.callback(cb, with_cookie=False)
    assert other.status_code == 403
    body = panel(other.text, "Sign-in link expired")
    assert f"<p>{smallpage.EXPIRED_WEB}</p>" in body and "different browser. Start again" not in body
    # a callback with no state at all: the plain failed page
    none = await gw.http.get("/auth/callback", headers=NAV)
    assert none.status_code == 400 and html.escape(smallpage.SIGNIN_FAILED) in none.text
    assert "Missing state" not in none.text


async def test_an_assistant_sign_in_link_that_expired_goes_back_to_the_app(gw: Harness) -> None:
    # the consent page without a live login
    r = await gw.http.get("/authorize/confirm", params={"state": "gone"}, headers=NAV)
    assert r.status_code == 400
    body = panel(r.text, "Sign-in link expired")
    assert f"<p>{smallpage.EXPIRED_APP}</p><p>{smallpage.EXPIRED_APP_BACK}</p>" in body
    assert "Claude, ChatGPT" in body and "AI client" not in body
    assert has_button(body, "Home", "/") and orange(body) == 0
    # an expired consent form (no form token): the same page, 403
    post = await gw.http.post("/authorize/confirm", data={"state": "gone", "action": "approve"}, headers=NAV)
    assert post.status_code == 403
    assert f"<p>{smallpage.EXPIRED_APP}</p>" in panel(post.text, "Sign-in link expired")
    assert "This form expired. Go back" not in post.text
    # an assistant's login whose consent was already answered: Deny again from the stale page
    client = await gw.register(client_name="Claude")
    start, _v = await gw.start_login(client)
    login_id = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    page = await gw.http.get("/authorize/confirm", params={"state": login_id})
    csrf = re.search(r"name='csrf' value='([0-9a-f]+)'", page.text).group(1)
    form = {"state": login_id, "csrf": csrf, "action": "deny", "shown": consent_stamp(gw.settings, login_id)}
    assert (await gw.http.post("/authorize/confirm", data=form)).status_code == 303
    again = await gw.http.post("/authorize/confirm", data=form, headers=NAV)
    assert again.status_code in (400, 403)
    assert f"<p>{smallpage.EXPIRED_APP}</p>" in panel(again.text, "Sign-in link expired")


async def test_the_gateways_own_refusals_keep_their_words_with_home(tmp_path: Path, idp: FakeIdP) -> None:
    settings = make_settings(tmp_path, required_group="mtg-users")
    async with running(Harness(settings, idp)) as h:
        idp.user = {**idp.user, "groups": ["other"]}
        r = await h.http.get("/login", params={"next": "/decks"})
        cb = await h.idp_leg(r)
        refused = await h.callback(cb)
        assert refused.status_code == 403
        body = panel(refused.text, "Sign-in failed")
        assert "<p>Your account is not in the group that may use this service.</p>" in body
        assert has_button(body, "Home", "/") and "Try again" not in body


async def test_consent_page_is_the_panel_with_the_app_in_a_strip(gw: Harness) -> None:
    client = await gw.register(redirect_uri="http://localhost:5555/cb", client_name="Claude Code")
    r, _v = await gw.start_login(client, redirect_uri="http://localhost:5555/cb")
    login_id = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    page = await gw.http.get("/authorize/confirm", params={"state": login_id}, headers=NAV)
    assert page.status_code == 200
    body = panel(page.text, "Connect an application")
    assert "<p>An application wants to connect to your account.</p>" in body
    # the app's name in a tinted strip (no box of its own), then the two facts in the shared meta list
    assert (
        "<div class='notice sio-who'><span class='app'>Claude Code</span>"
        "<span class='host'>a name the application chose for itself</span></div>" in body
    )
    assert "<dl class='ui-meta'><dt>It will be able to</dt>" in body
    assert "<dt>After sign-in you go to</dt><dd><code>localhost</code></dd></dl>" in body
    # every sentence at normal size; the click-guard hint is the only small print
    assert "<p>Approve only if you started connecting this application yourself" in body
    assert "<p>Approving opens your identity provider's sign-in page." in body
    assert body.count("class='muted small") == 1 and "guard-hint" in body
    assert body.index("Approving opens") < body.index("<form method='post'")
    # the loopback warning as a tinted warning strip, with its heading line
    assert (
        "<div class='notice warn' role='alert'><strong>Warning: this application runs on this computer"
        in body
    )
    assert "class='consent'" not in page.text and "class='who'" not in page.text
    # Approve is the page's one orange button (the no-script copy repeats it); Deny stays plain
    assert "value='approve' class='primary'" in body and "value='deny' class='btn-plain'" in body
    assert body.count("class='primary'") == 2  # the guarded form and its noscript twin
    assert ".sio{" in page.text  # the shared small-page style


async def test_a_refused_app_request_keeps_its_code_for_the_log(
    gw: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    client = await gw.register(client_name="Claude")
    with caplog.at_level(logging.WARNING):
        r = await gw.http.get(
            "/authorize",
            params={
                "client_id": client["client_id"],
                "redirect_uri": "https://client.test/cb",
                "response_type": "code",
                "state": "cs1",
                # no PKCE challenge: refused before the consent page
            },
            headers=NAV,
        )
    assert r.status_code == 400
    body = panel(r.text, "Sign-in failed")
    assert f"<p>{html.escape(smallpage.REFUSED)}</p>" in body
    assert "(invalid_request)" not in body and "refused (" not in body
    assert (
        "<div class='notice warn'>The request came from an app that returns to <code>client.test</code>. "
        "<a href=" in body
    )
    assert "Go back to it</a> only if you expected that address.</div>" in body
    assert has_button(body, "Home", "/")
    assert "app sign-in request refused (invalid_request)" in caplog.text


async def test_form_expired_pages_say_what_was_not_done_and_offer_reload_and_back(gw: Harness) -> None:
    b = Browser(gw)
    try:
        await b.login()
        for data, title, reload_text, reload_href, back, nothing in (
            (
                {"action": "apply", "pick": "p1"},
                "/proposals/select",
                "Reload Proposals",
                "/proposals",
                "/proposals",
                "Nothing was applied.",
            ),
            (
                {"action": "reject", "pick": "p1", "back": "/decks/42"},
                "/proposals/select",
                "Reload Proposals",
                "/proposals",
                "/decks/42",
                "Nothing was rejected.",
            ),
            (
                {"action": "apply", "picks": "p1"},
                "/proposals/apply",
                "Reload Proposals",
                "/proposals",
                "/proposals",
                "Nothing was applied.",
            ),
            (
                {"action": "undo", "applied": "p7", "snapshot": "s1"},
                "/proposals/undo",
                "Reload the change",
                "/proposals/p7",
                "/proposals",
                "Nothing was undone.",
            ),
            (
                {"action": "preview", "applied": "p7,p8"},
                "/proposals/undo",
                "Reload the change",
                "/proposals",
                "/proposals",
                "Nothing was undone.",
            ),
            (
                {"action": "unlink"},
                "/account",
                "Reload Account",
                "/account",
                "/account",
                "Nothing was changed.",
            ),
        ):
            r = await b.http.post(title, data={**data, "csrf": "stale"}, headers=NAV)
            assert r.status_code == 403, (title, r.status_code)
            body = panel(r.text, "Form expired")
            assert f"<p>{smallpage.FORM_EXPIRED}</p>" in body, title
            assert f"<p class='muted small' style='margin:0'>{nothing}</p>" in body, title
            assert has_button(body, reload_text, reload_href, primary=True), (title, body)
            assert f"<a class='btn btn-plain' href='{back}' data-history-back>Back</a>" in body, title
            assert orange(body) == 1
    finally:
        await b.aclose()


async def test_too_large_gets_a_back_button(gw: Harness) -> None:
    b = Browser(gw)
    try:
        await b.login()
        r = await b.http.post(
            "/account",
            content=b"x=" + b"a" * (pages.MAX_FORM + 10),
            headers={**NAV, "content-type": "application/x-www-form-urlencoded"},
        )
        assert r.status_code == 413
        body = panel(r.text, "Too large")
        assert f"<p>{smallpage.TOO_LARGE}</p>" in body
        assert "<a class='btn btn-plain' href='/account' data-history-back>Back</a>" in body
    finally:
        await b.aclose()


async def test_the_app_only_finish_and_the_in_app_pages(gw: Harness) -> None:
    # a normal browser posting the app's finish: the panel with Home
    r = await gw.http.post("/login/app", data={"code": "x", "verifier": "y"}, headers=NAV)
    assert r.status_code == 403
    body = panel(r.text, "Sign-in failed")
    assert f"<p>{smallpage.APP_ONLY}</p>" in body and has_button(body, "Home", "/")
    # inside the app: a code that is gone
    gone = await gw.http.post(
        "/login/app",
        data={"code": "a" * 43, "verifier": "b" * 43},
        headers={**NAV, **APP_UA, "Sec-Fetch-Site": "none"},
    )
    assert gone.status_code == 400
    body = panel(gone.text, "Sign-in failed")
    assert f"<p>{html.escape(smallpage.APP_EXPIRED)}</p>" in body and "You were not signed in." in body
    assert "Nothing was signed in" not in body
    assert has_button(body, "Sign in again", "/login?next=/", primary=True)
    # the in-app hand-off page: the panel titled Sign in, its one orange button, the small-print link
    hand = await gw.http.get("/login", params={"next": "/decks"}, headers={**NAV, **APP_UA})
    assert hand.status_code == 200
    body = panel(hand.text, "Sign in")
    assert (
        "<button class='primary' type='button' id='app-signin-go'>Sign in with the browser</button>" in body
    )
    assert "id='app-signin' data-next='/decks'" in hand.text and orange(body) == 1
    # the browser's return page: the panel titled Signed in
    verifier = "v" * 50
    start = await gw.http.get("/login", params={"next": "/decks", "app_challenge": challenge_of(verifier)})
    cb = await gw.idp_leg(start)
    handed = await gw.http.get(cb[len(GATEWAY) :], headers=NAV)
    assert handed.status_code == 303  # to the gateway's own return address (app_signin.RETURN_PATH)
    back = await gw.http.get(handed.headers["location"], headers=NAV)
    assert back.status_code == 200
    body = panel(back.text, "Signed in")
    assert "<p>You're signed in. Go back to the MTG Assistant Gateway app to finish.</p>" in body
    assert "id='app-return'" in body and orange(body) == 1


async def test_404_500_and_503_pages_are_panels_with_a_way_forward(
    tmp_path: Path, idp: FakeIdP, monkeypatch
) -> None:
    from mtg_gateway.app import Membership

    h = Harness(make_settings(tmp_path, required_group="mtg-users", membership_check_ttl=0), idp)
    async with running(h):
        missing = await h.http.get("/no-such-page", headers=NAV)
        assert missing.status_code == 404
        body = panel(missing.text, "Page not found")
        assert has_button(body, "Go to my decks", "/decks", primary=True) and has_button(body, "Home", "/")
        b = Browser(h)
        try:
            await b.login()
            signed = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=h.app, raise_app_exceptions=False),
                base_url=b.http.base_url,
                cookies=b.http.cookies,
            )
            await b.http.aclose()
            b.http = signed

            def boom(*_a: object, **_k: object) -> None:
                raise RuntimeError("secret")

            monkeypatch.setattr(h.db, "list_proposals", boom)
            monkeypatch.setattr(h.db, "proposal_index", boom)
            broke = await b.http.get("/proposals?x=1", headers=NAV)
            assert broke.status_code == 500
            body = panel(broke.text, "Something went wrong")
            assert has_button(body, "Try again", "/proposals?x=1", primary=True)
            assert has_button(body, "Go to my decks", "/decks") and "secret" not in broke.text
            # 503: the sign-in service cannot confirm the member; Try again loads the same page
            checker = h.app.state.gateway.membership

            async def unavailable(_sub: str) -> str:
                return Membership.UNAVAILABLE

            assert checker is not None
            monkeypatch.setattr(checker, "check", unavailable)
            busy = await b.http.get("/decks?p=2", headers=NAV)
            assert busy.status_code == 503
            body = panel(busy.text, "Try again later")
            assert f"<p>{html.escape(smallpage.IDP_UNREACHABLE)}</p>" in body
            assert has_button(body, "Try again", "/decks?p=2", primary=True)
        finally:
            await b.http.aclose()


def test_login_error_kinds_pick_the_page() -> None:
    assert "Sign-in link expired" in smallpage.login_error("expired", "x", assistant=False)
    assert smallpage.EXPIRED_APP_BACK in smallpage.login_error("expired", "x", assistant=True)
    assert "Sign-in unavailable" in smallpage.login_error("unavailable", "x", assistant=False)
    assert "<p>my words</p>" in smallpage.login_error("refused", "my words", assistant=False)
    assert smallpage.SIGNIN_FAILED in html.unescape(smallpage.login_error("failed", "x", assistant=False))
    assert smallpage.login_error_title("expired") == "Sign-in link expired"
    assert smallpage.login_error_title("refused") == "Sign-in failed"
