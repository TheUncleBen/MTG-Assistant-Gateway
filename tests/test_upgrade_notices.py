"""A stack file that doesn't attach the Authentik secret (one from before 0.7.37, whose secret
lines were commented out) leaves the removed-member clean-up off. The admin overview says so in a
notice, not only in its System list and one start-up warning."""

from __future__ import annotations

import html
from pathlib import Path

from .conftest import FakeIdP, Harness, make_settings, running
from .test_admin import NAVIGATE, _with_admin, admin_browser

NOTICE = (
    "The removed-member clean-up is off: its Authentik API token can't be read. Check that your "
    "stack file or docker-compose.yml is the current one, which attaches the "
    "mtg_authentik_api_token secret to the gateway, then redeploy."
)


async def _overview(tmp_path: Path, idp: FakeIdP, **over: object) -> str:
    settings = make_settings(tmp_path, required_group="mtg-users", admin_group="mtg-admins", **over)
    async with running(_with_admin(Harness(settings, idp))) as h:
        b = await admin_browser(h)
        try:
            return html.unescape((await b.http.get("/admin", headers=NAVIGATE)).text)
        finally:
            await b.aclose()


async def test_an_unreadable_clean_up_token_is_a_notice_on_the_admin_page(
    tmp_path: Path, idp: FakeIdP
) -> None:
    problem = "cannot read secret file for MTG_AUTHENTIK_API_TOKEN_FILE: [Errno 2] No such file"
    page = await _overview(tmp_path, idp, authentik_api_token_problem=problem)
    assert f"<div class='notice warn'>{NOTICE}</div>" in page


async def test_no_notice_when_the_clean_up_was_never_set_up(tmp_path: Path, idp: FakeIdP) -> None:
    page = await _overview(tmp_path, idp)
    assert "removed-member clean-up is off: its Authentik" not in page
