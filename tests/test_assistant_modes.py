"""Each connected assistant's own approval mode (assistants.py; owner decision 2026-10-28, built as
the approved mockups show): Claude on any surface is one assistant, ChatGPT another, an app on
localhost or any other host its own; a new one starts at the member's default; the mode that counts
is the proposing assistant's, always under the admin's cap; only the member's browser sets it."""

from __future__ import annotations

import html
import sqlite3

import pytest

from mtg_gateway import assistants, modes

from .conftest import Harness, Stack, StackOptions, stack_with
from .test_decks_and_proxy import Browser, call, linked_user, structured

CLAUDE_CB = "https://claude.ai/api/mcp/auth_callback"
GPT_CB = "https://chatgpt.com/connector_platform_oauth_redirect"
LOCAL_CB = "http://localhost:33418/callback"
OTHER_CB = "https://client.test/cb"
AUTO, SEMI, MANUAL = modes.MODE_LABELS["auto"], modes.MODE_LABELS["semi"], modes.MODE_LABELS["manual"]

stack_options = stack_with(StackOptions(settings={"writes_enabled": True}))


async def connect(h: Harness, redirect: str, name: str) -> tuple[str, str]:
    """Register an app that sends the member back to ``redirect`` and sign it in: (token, client id)."""
    client = await h.register(redirect_uri=redirect, client_name=name)
    code, verifier = await h.full_login(client, redirect_uri=redirect)
    r = await h.token(
        client, grant_type="authorization_code", code=code, code_verifier=verifier, redirect_uri=redirect
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"], client["client_id"]


async def linked_member(stack: Stack) -> None:
    """Alice, signed in on the website with Archidekt linked, and no assistant connected yet."""
    b = Browser(stack.h)
    await b.login()
    assert (await b.link("alice", "pw-alice")).status_code == 303
    await b.aclose()


async def browser(h: Harness) -> Browser:
    b = Browser(h)
    await b.login()
    return b


async def set_app_mode(b: Browser, slug: str, mode: str, *, confirm: bool = True):
    data = {"csrf": await b.csrf("/account/assistants"), "action": "mode", "mode": mode}
    if confirm:
        data["confirm"] = "yes"
    return await b.http.post(f"/account/assistants/{slug}", data=data)


async def add_one(h: Harness, token: str, name: str = "Sol Ring") -> dict:
    changes = [{"action": "add", "card_name": name}]
    return structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": changes}))


# -- who is one assistant ------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("client_id", "redirects", "key"),
    [
        ("c1", [CLAUDE_CB], "claude"),
        ("c2", [CLAUDE_CB.replace("claude.ai", "claude.com")], "claude"),  # Claude's other callback
        ("c3", [CLAUDE_CB, CLAUDE_CB.replace("claude.ai", "claude.com")], "claude"),
        ("c4", [CLAUDE_CB.replace("claude.ai", "CLAUDE.AI")], "claude"),  # a host's case is no matter
        # security review 3, R3-02: only the exact known callbacks count, never any address on the
        # brand's hosts (an open redirect there would hand the code on)
        ("o1", ["https://claude.ai/other/cb"], "app:o1"),
        ("o2", ["https://api.claude.ai/api/mcp/auth_callback"], "app:o2"),
        ("o3", [CLAUDE_CB + "?to=https://evil.test"], "app:o3"),
        ("o4", [CLAUDE_CB + "#x"], "app:o4"),
        ("o5", ["https://claude.ai:8443/api/mcp/auth_callback"], "app:o5"),
        ("o6", ["https://u@claude.ai/api/mcp/auth_callback"], "app:o6"),
        ("o7", ["http://claude.ai/api/mcp/auth_callback"], "app:o7"),
        ("o8", ["https://chatgpt.com/some/redirector?to=https://evil.test"], "app:o8"),
        ("o9", ["https://anthropic.com/api/mcp/auth_callback"], "app:o9"),
        ("o10", [CLAUDE_CB, GPT_CB], "app:o10"),  # two brands: neither
        ("o11", [CLAUDE_CB + "/"], "app:o11"),
        # a metadata document on a brand's host is that brand only by the callbacks it names
        (
            "https://anything.claude.ai/u/x.json",
            ["https://evil.test/cb"],
            "app:https://anything.claude.ai/u/x.json",
        ),
        ("https://community.openai.com/x.json", [], "app:https://community.openai.com/x.json"),
        ("https://claude.ai/oauth/client.json", [CLAUDE_CB], "claude"),
        ("g1", [GPT_CB], "openai"),
        ("l1", [LOCAL_CB], "app:l1"),  # Claude Code: its own
        ("l2", ["http://127.0.0.1:9/cb"], "app:l2"),
        ("https://claude.ai/code/client.json", [LOCAL_CB], "app:https://claude.ai/code/client.json"),
        ("x1", [OTHER_CB], "app:x1"),  # an unknown host: its own
        ("x2", [CLAUDE_CB, "https://evil.example/cb"], "app:x2"),  # not only claude.ai
        ("n1", [], "app:n1"),
    ],
)
def test_an_assistant_is_its_checked_host_or_its_own_app(client_id: str, redirects: list, key: str) -> None:
    assert assistants.key_of(client_id, redirects) == key


def test_the_page_address_never_holds_a_client_id() -> None:
    assert assistants.slug_of("claude") == "claude" and assistants.slug_of("openai") == "chatgpt"
    slug = assistants.slug_of("app:secret-client-id")
    assert slug.startswith("app-") and "secret" not in slug


# -- the mode that counts is the proposing assistant's ---------------------------------------------
async def test_two_assistants_with_different_modes_on_the_same_deck(stack: Stack) -> None:
    h = stack.h
    await linked_member(stack)
    claude, _ = await connect(h, CLAUDE_CB, "Claude")
    gpt, _ = await connect(h, GPT_CB, "ChatGPT")
    b = await browser(h)
    r = await set_app_mode(b, "claude", "semi")
    assert r.status_code == 303 and r.headers["location"] == "/account/assistants/claude?ok=mode_semi"
    await b.aclose()

    assert structured(await call(h, claude, "account_status"))["approval_mode"] == "semi"
    assert structured(await call(h, gpt, "account_status"))["approval_mode"] == "manual"
    pc = await add_one(h, claude, "Sol Ring")
    pg = await add_one(h, gpt, "Arcane Signet")
    assert pc["assistant_may_apply"] is True and pc["approval_mode"] == "semi"
    assert pg["assistant_may_apply"] is False and pg["approval_mode"] == "manual"
    assert "call apply_proposal now" in pc["next_step"]
    assert "call apply_proposal now" not in pg["next_step"]

    refused = await call(h, gpt, "apply_proposal", {"proposal_id": pg["proposal_id"]})
    assert structured(refused)["error"] == "browser_required"
    done = structured(await call(h, claude, "apply_proposal", {"proposal_id": pc["proposal_id"]}))
    assert done["ok"] and done["state"] == "applied"
    # the deck's waiting list still counts ChatGPT's change for the member's press
    assert h.app.state.gateway.decks.waiting_by_deck("user-1")["42"][1] == 1


async def test_an_assistant_that_connects_again_keeps_its_mode(stack: Stack) -> None:
    """A new client id on the same checked host (Claude reconnected, or on another device) is the
    same assistant: one row on the page, the mode the member chose for it."""
    h = stack.h
    await linked_member(stack)
    await connect(h, CLAUDE_CB, "Claude")
    b = await browser(h)
    assert (await set_app_mode(b, "claude", "auto")).status_code == 303
    again, cid = await connect(h, CLAUDE_CB, "Claude")
    assert structured(await call(h, again, "account_status"))["approval_mode"] == "auto"
    page = (await b.http.get("/account/assistants")).text
    assert page.count("href='/account/assistants/claude'") == 1 and cid not in page
    await b.aclose()


async def test_an_unknown_host_and_a_localhost_app_are_each_their_own(stack: Stack) -> None:
    h = stack.h
    await linked_member(stack)
    await connect(h, CLAUDE_CB, "Claude")
    other, oid = await connect(h, OTHER_CB, "Claude")  # a name alone earns nothing
    local, lid = await connect(h, LOCAL_CB, "Claude Code")
    b = await browser(h)
    assert (await set_app_mode(b, "claude", "auto")).status_code == 303
    for token in (other, local):
        assert structured(await call(h, token, "account_status"))["approval_mode"] == "manual"
    page = (await b.http.get("/account/assistants")).text
    await b.aclose()
    assert page.count("class='yrow app'") == 3
    assert "client.test" in page and assistants.LOCAL_WORDS in page
    # only the checked one carries Claude's mark; the localhost one a terminal, the other the neutral icon
    assert page.count("/static/logos/claude.svg") == 1
    assert "alogo term" in page and oid not in page and lid not in page


async def test_a_new_assistant_starts_at_the_default_and_follows_it(stack: Stack) -> None:
    h = stack.h
    await linked_member(stack)
    gpt, _ = await connect(h, GPT_CB, "ChatGPT")
    b = await browser(h)
    r = await b.http.post(
        "/account", data={"csrf": await b.csrf(), "action": "approval_mode", "mode": "semi"}
    )
    assert r.headers["location"] == "/account/assistants/default?ok=mode_semi"
    # ChatGPT follows the default (no choice of its own) ...
    assert structured(await call(h, gpt, "account_status"))["approval_mode"] == "semi"
    # ... until the member picks one for it; "Same as my default" goes back
    assert (await set_app_mode(b, "chatgpt", "manual")).status_code == 303
    assert structured(await call(h, gpt, "account_status"))["approval_mode"] == "manual"
    assert (await set_app_mode(b, "chatgpt", "default")).status_code == 303
    assert h.db.assistant_mode("user-1", "openai") is None
    # a newly connected one starts at the default, never at another assistant's mode
    assert (await set_app_mode(b, "chatgpt", "auto")).status_code == 303
    claude, _ = await connect(h, CLAUDE_CB, "Claude")
    assert structured(await call(h, claude, "account_status"))["approval_mode"] == "semi"
    await b.aclose()


async def test_the_admin_cap_still_applies_to_every_assistant(stack: Stack) -> None:
    h = stack.h
    await linked_member(stack)
    claude, _ = await connect(h, CLAUDE_CB, "Claude")
    b = await browser(h)
    assert (await set_app_mode(b, "claude", "auto")).status_code == 303
    h.app.state.gateway.live.set("approval_mode_max", "semi", by="admin-1")
    assert structured(await call(h, claude, "account_status"))["approval_mode"] == "semi"
    r = await set_app_mode(b, "claude", "auto")
    assert r.headers["location"] == "/account/assistants/claude?err=mode_invalid"
    page = (await b.http.get("/account/assistants/claude")).text
    auto = page[page.index("value='auto'") :]
    assert auto.index("disabled") < auto.index("</button>") and "Not allowed on this gateway" in page
    await b.aclose()


# -- the pages ----------------------------------------------------------------------------------
async def test_a_member_with_no_assistant_yet(stack: Stack) -> None:
    h = stack.h
    await linked_member(stack)
    b = await browser(h)
    details = (await b.http.get("/account/details")).text
    assert "href='/account/assistants'" in details and "Not connected yet" in details
    assert "<div class='card' id='mode'>" not in details  # one Assistants row instead of two cards
    page = (await b.http.get("/account/assistants")).text
    await b.aclose()
    assert "Default for new assistants" in page and MANUAL in page
    assert "No assistant is connected to your account yet." in page and "href='/install'" in page
    assert "Disconnect all" not in page


async def test_the_assistants_page_as_the_mockup_shows_it(stack: Stack) -> None:
    h = stack.h
    await linked_member(stack)
    await connect(h, CLAUDE_CB, "Claude")
    await connect(h, GPT_CB, "ChatGPT")
    b = await browser(h)
    assert (await set_app_mode(b, "claude", "semi")).status_code == 303
    details = (await b.http.get("/account/details")).text
    assert "2 connected" in details
    page = (await b.http.get("/account/assistants")).text
    assert "A newly connected assistant starts with this; each one below can have its own." in page
    assert "How much each assistant may change on Archidekt without asking you first." in page
    claude = page[page.index("href='/account/assistants/claude'") :]
    assert "claude.ai · last used just now" in claude[:600] and html.escape(SEMI) in claude[:800]
    gpt = page[page.index("href='/account/assistants/chatgpt'") :]
    assert "chatgpt.com" in gpt[:600] and MANUAL in gpt[:800] and ">New<" in gpt[:600]
    one = (await b.http.get("/account/assistants/chatgpt")).text
    await b.aclose()
    assert "What ChatGPT may do" in one and f"Same as my default ({MANUAL})" in one
    for label in modes.MODE_LABELS.values():
        assert html.escape(label) in one
    assert (
        "Only changes proposed by “ChatGPT”. A tighter choice also covers its changes already waiting." in one
    )
    assert "Identified by" in one and "Disconnect “ChatGPT”" in one
    # the warning line stays hidden (owner rule): the mode's cost is said in the confirm box only
    assert html.escape(modes.AUTO_WARNING) not in one


async def test_account_details_names_the_default_and_says_which_assistant_differs(stack: Stack) -> None:
    """Independent review 2, M4: Account details said "Approval mode · Ask me every time" while
    Claude applied low-risk changes by itself. The row is the default for new assistants, so it is
    called that (the name its own page has), and it says when an assistant has a mode of its own."""
    import re

    h = stack.h
    await linked_member(stack)
    await connect(h, CLAUDE_CB, "Claude")
    await connect(h, GPT_CB, "ChatGPT")
    b = await browser(h)

    def mode_row(page: str) -> str:
        m = re.search(r"<a class='yrow[^']*' href='/account/assistants/default'>(.*?)</a>", page, re.S)
        assert m, page
        return re.sub(r"<[^>]+>", " ", m.group(1))

    row = mode_row((await b.http.get("/account/details")).text)
    assert "Default for new assistants" in row and MANUAL in row and "own" not in row, row
    assert "Approval mode" not in row
    assert (await set_app_mode(b, "claude", "semi")).status_code == 303
    row = mode_row((await b.http.get("/account/details")).text)
    assert "Default for new assistants" in row and MANUAL in row
    assert "Claude has its own" in row, row
    assert (await set_app_mode(b, "chatgpt", "semi")).status_code == 303
    row = mode_row((await b.http.get("/account/details")).text)
    await b.aclose()
    assert "2 assistants have their own" in row, row


async def test_loosening_asks_first_and_names_the_app(stack: Stack) -> None:
    h = stack.h
    await linked_member(stack)
    gpt, _ = await connect(h, GPT_CB, "ChatGPT")
    await add_one(h, gpt)
    b = await browser(h)
    r = await set_app_mode(b, "chatgpt", "auto", confirm=False)
    assert r.headers["location"] == "/account/assistants/chatgpt?confirm=auto"
    assert h.db.assistant_mode("user-1", "openai") is None  # nothing changed yet
    page = (await b.http.get(r.headers["location"])).text
    box = page[page.index("id='pmode'") :]
    assert "Let “ChatGPT” apply changes without asking?" in box
    assert "“ChatGPT” (chatgpt.com) could then apply the changes it proposes" in box
    assert "Its 1 waiting change may then be applied by it." in box
    assert "Allow for “ChatGPT”" in box and "Cancel" in box
    assert (await set_app_mode(b, "chatgpt", "auto")).status_code == 303
    assert h.db.assistant_mode("user-1", "openai") == "auto"
    # tightening takes effect at once, without a box, for the change already waiting too
    r = await set_app_mode(b, "chatgpt", "manual", confirm=False)
    assert r.headers["location"] == "/account/assistants/chatgpt?ok=mode_manual"
    await b.aclose()
    listed = structured(await call(h, gpt, "list_my_proposals"))["proposals"][0]
    p = structured(await call(h, gpt, "get_proposal", {"proposal_id": listed["id"]}))
    assert p["assistant_may_apply"] is False


async def test_disconnecting_forgets_the_mode(stack: Stack) -> None:
    h = stack.h
    await linked_member(stack)
    await connect(h, CLAUDE_CB, "Claude")
    await connect(h, CLAUDE_CB, "Claude")  # two apps, one assistant
    b = await browser(h)
    assert (await set_app_mode(b, "claude", "auto")).status_code == 303
    r = await b.http.post(
        "/account/assistants/claude",
        data={"csrf": await b.csrf("/account/assistants"), "action": "disconnect"},
    )
    assert (
        r.status_code == 303
        and r.headers["location"] == "/account/assistants?ok=disconnected_assistant&name=Claude"
    )
    assert h.db.user_clients("user-1") == [] and h.db.assistant_mode("user-1", "claude") is None
    page = (await b.http.get(r.headers["location"])).text
    await b.aclose()
    assert "Disconnected “Claude”." in page
    again, _ = await connect(h, CLAUDE_CB, "Claude")
    assert structured(await call(h, again, "account_status"))["approval_mode"] == "manual"


async def test_the_older_one_app_disconnect_forgets_the_mode_with_the_last_app(stack: Stack) -> None:
    """Security review 3, R3-03: the module docstring says disconnecting an assistant forgets its
    mode, but POST /account's one-app Disconnect kept a brand's. The member's own Disconnect, by
    either route, forgets the mode once none of the assistant's apps is connected; while another
    of its apps still is (Claude on a second device), the assistant is still there and keeps it."""
    h = stack.h
    await linked_member(stack)
    _t1, first = await connect(h, CLAUDE_CB, "Claude")
    _t2, second = await connect(h, CLAUDE_CB, "Claude")
    b = await browser(h)
    assert (await set_app_mode(b, "claude", "auto")).status_code == 303

    async def disconnect(cid: str) -> None:
        data = {"csrf": await b.csrf("/account/details"), "action": "disconnect", "client_id": cid}
        assert (await b.http.post("/account", data=data)).status_code == 303

    await disconnect(first)
    assert h.db.assistant_mode("user-1", "claude") == "auto"  # Claude is still connected
    await disconnect(second)
    assert h.db.assistant_mode("user-1", "claude") is None
    await b.aclose()
    again, _ = await connect(h, CLAUDE_CB, "Claude")
    assert structured(await call(h, again, "account_status"))["approval_mode"] == "manual"


async def test_a_lapsed_sign_in_keeps_the_mode(stack: Stack) -> None:
    """What the member did not do forgets nothing: an assistant whose tokens lapsed (or that an
    administrator disconnected) gets the mode the member chose back when it connects again."""
    h = stack.h
    await linked_member(stack)
    _t, cid = await connect(h, CLAUDE_CB, "Claude")
    b = await browser(h)
    assert (await set_app_mode(b, "claude", "semi")).status_code == 303
    await b.aclose()
    h.db.revoke_client_for_user("user-1", cid)  # the tokens are gone, not by the member's press
    assert h.db.user_clients("user-1") == []
    again, _ = await connect(h, CLAUDE_CB, "Claude")
    assert structured(await call(h, again, "account_status"))["approval_mode"] == "semi"


async def test_an_assistant_cannot_set_a_mode(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    r = await h.http.post(
        "/account/assistants/claude",
        data={"action": "mode", "mode": "auto", "confirm": "yes"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code in (302, 303) and "/login" in r.headers.get("location", "")
    assert h.db.assistant_modes("user-1").get("claude", {}).get("mode") is None
    tools = (await h.mcp(token, "tools/list")).text
    assert "set_assistant_mode" not in tools


async def test_home_says_once_that_an_assistant_connected(stack: Stack) -> None:
    h = stack.h
    await linked_member(stack)
    b = await browser(h)
    assert "class='justin'" not in (await b.http.get("/")).text
    await connect(h, GPT_CB, "ChatGPT")
    home = (await b.http.get("/")).text
    notice = home[home.index("class='justin'") :]
    assert "“ChatGPT” connected just now.</b> It uses your default: Ask me every time." in notice
    assert "href='/account/assistants/chatgpt'>Change for “ChatGPT”</a>" in notice
    (await b.http.get("/account/assistants/chatgpt")).raise_for_status()  # seen: no longer new
    assert "class='justin'" not in (await b.http.get("/")).text
    assert ">New<" not in (await b.http.get("/account/assistants")).text
    await b.aclose()


async def test_an_app_connected_before_the_upgrade_is_not_new(stack: Stack) -> None:
    """Upgrading changes nothing: an app that held a token before schema 20 follows the default, as
    it did, and gets no "connected just now" notice when it next signs in."""
    h = stack.h
    await linked_member(stack)
    await connect(h, GPT_CB, "ChatGPT")
    h.db._conn.execute("DELETE FROM assistant_modes")  # as before the step that added the table
    b = await browser(h)
    assert "class='justin'" not in (await b.http.get("/")).text
    assert ">New<" not in (await b.http.get("/account/assistants")).text
    await b.aclose()


def test_schema_20_adds_the_table_and_changes_no_mode(tmp_path) -> None:
    from mtg_gateway import db as db_module

    path = tmp_path / "old.db"
    db = db_module.Database(str(path))
    db.upsert_user("u1", email="a@b", name="A", preferred_username="a", groups=[])
    db.set_approval_mode("u1", "semi")
    db.close()
    raw = sqlite3.connect(path)
    raw.execute("DROP TABLE assistant_modes")
    raw.execute("PRAGMA user_version = 19")
    raw.commit()
    raw.close()
    again = db_module.Database(str(path), upgrade_dir=tmp_path / "before-upgrade")
    try:
        assert again.schema_version == db_module.SCHEMA_VERSION == 23
        assert again.get_user("u1")["approval_mode"] == "semi"
        assert again.assistant_modes("u1") == {}
        again.set_assistant_mode("u1", "claude", "auto")
        assert again.assistant_mode("u1", "claude") == "auto"
        again.delete_member_data("u1")
        assert again.assistant_modes("u1") == {}
    finally:
        again.close()


async def test_the_mode_is_read_live_for_each_proposal(stack: Stack) -> None:
    """A proposal waits under its maker's mode as it is now: loosening Claude lets Claude apply its
    waiting change; ChatGPT's change made under the same default keeps waiting."""
    h = stack.h
    await linked_member(stack)
    claude, _ = await connect(h, CLAUDE_CB, "Claude")
    gpt, _ = await connect(h, GPT_CB, "ChatGPT")
    pc = await add_one(h, claude, "Sol Ring")
    pg = await add_one(h, gpt, "Arcane Signet")
    assert not pc["assistant_may_apply"] and not pg["assistant_may_apply"]
    b = await browser(h)
    assert (await set_app_mode(b, "claude", "semi")).status_code == 303
    await b.aclose()
    now_c = structured(await call(h, claude, "get_proposal", {"proposal_id": pc["proposal_id"]}))
    now_g = structured(await call(h, gpt, "get_proposal", {"proposal_id": pg["proposal_id"]}))
    assert now_c["assistant_may_apply"] is True and now_g["assistant_may_apply"] is False
