"""Release 0.7.29, approvals: the admin overview's approval-mode lines read the live settings,
so a change on the Settings page shows there at once, without a restart."""

from __future__ import annotations

from .conftest import Harness
from .test_admin import NAVIGATE, admin_browser, gw  # noqa: F401  (gw is the fixture)


async def test_the_overview_shows_the_live_approval_modes(gw: Harness) -> None:  # noqa: F811
    b = await admin_browser(gw)
    try:
        live = gw.app.state.gateway.decks.live
        live.set("approval_mode_default", "semi", by="admin-test")
        live.set("approval_mode_max", "semi", by="admin-test")
        page = (await b.http.get("/admin", headers=NAVIGATE)).text
        row = "<div class='ui-row'><p class='lab'>{}</p><div class='ctl'>{}</div></div>"
        assert row.format("Default approval mode", "Semi-auto (semi)") in page
        assert row.format("Highest approval mode allowed", "Semi-auto (semi)") in page
        live.set("approval_mode_max", "auto", by="admin-test")
        page = (await b.http.get("/admin", headers=NAVIGATE)).text
        assert row.format("Highest approval mode allowed", "Full auto (auto)") in page
    finally:
        await b.aclose()
