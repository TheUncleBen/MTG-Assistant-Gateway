"""An uploaded Authentik picture reaches the account icon through the optional scope mapping in
docs/IDP-AUTHENTIK.md ("Profile pictures"), which authentik_setup.py installs exactly as printed."""

from __future__ import annotations

import asyncio

from .conftest import GEN, PUBLIC_URL, Env, browser_page, gateway_browser_sign_in


async def test_the_uploaded_picture_is_shown(env: Env):
    expected = (GEN / "avatar.png").read_bytes()
    async with browser_page() as page:
        await gateway_browser_sign_in(page, env, "alice-test", "/account")
        assert await page.locator("img.av").count() == 1
        body = b""
        for _ in range(20):  # fetched in the background after sign-in
            r = await page.request.get(f"{PUBLIC_URL}/account/avatar")
            body = await r.body()
            if body == expected:
                break
            await asyncio.sleep(0.5)
        assert body == expected, body[:80]
        assert r.headers["content-type"].startswith("image/png")


async def test_a_member_without_one_gets_initials(env: Env):
    async with browser_page() as page:
        await gateway_browser_sign_in(page, env, "bob-test", "/account")
        r = await page.request.get(f"{PUBLIC_URL}/account/avatar")
        assert r.headers["content-type"].startswith("image/svg+xml")
        assert b">BT<" in await r.body()
