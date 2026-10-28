"""Decision C8 (0.7.46), the forum part: a member without a linked Archidekt account reads the
forum (Archidekt's forum pages and comment threads answer anonymous readers, as the deck
comments already did) and is offered a Link Archidekt button where a reply would go; nobody sees
an HTTP 400 with the assistant's "Sign in at http://…" sentence."""

from __future__ import annotations

import pytest

from .conftest import Stack, stack_with
from .test_approval_modes import SHIPPED
from .test_decks_and_proxy import Browser, linked_user

stack_options = stack_with(SHIPPED)
SECTION = "/forum/Archidekt/General"
LINK = "<a class='btn btn-primary' href='/account/details#archidekt'>"


@pytest.fixture
async def unlinked(stack: Stack):
    """A signed-in member who never linked Archidekt."""
    b = Browser(stack.h)
    await b.login("/forum")
    try:
        yield b
    finally:
        await b.aclose()


async def test_an_unlinked_member_reads_the_forum_and_is_offered_the_link(unlinked: Browser) -> None:
    home = await unlinked.http.get("/forum")
    assert home.status_code == 200, home.text
    assert "No Archidekt account is linked" not in home.text and "Sign in at http" not in home.text
    assert "href='/forum/Archidekt/General'" in home.text  # the sections, read without a session
    assert "Link Archidekt to reply" in home.text and LINK in home.text
    assert home.text.count("btn-primary") == 1  # the one orange button

    section = await unlinked.http.get(SECTION)
    assert section.status_code == 200, section.text
    assert "Welcome to the forum" in section.text and LINK in section.text

    # A thread is a comment thread. The fake, like the code's own notes, has not seen Archidekt
    # answer a comment read without a session, so it refuses one: the member gets a plain page
    # with the Link button and the way to Archidekt's forum, never a 400 with a raw address.
    thread = await unlinked.http.get("/forum/thread/700001")
    assert thread.status_code == 200, thread.text
    assert "<form class='newcomment'" not in thread.text  # no reply box: nothing to post with
    assert "Archidekt shows this part of the forum to signed-in readers only." in thread.text
    assert LINK in thread.text and "Link Archidekt to reply" in thread.text
    assert "href='https://archidekt.com/forum'" in thread.text
    assert "Sign in at http" not in thread.text and "asked for a sign-in" not in thread.text


async def test_an_unlinked_member_reads_a_thread_when_archidekt_answers_without_a_session(
    unlinked: Browser, stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Should Archidekt answer the comment read anonymously (as it does for public decks), the
    thread shows with the Link strip where the reply box would be."""
    ark = stack.ark
    real = ark.handle

    def open_comments(request):
        if request.url.path == "/api/comments/700001/" and request.method == "GET":
            request.headers["Authorization"] = f"JWT {ark.issue_access('amy')}"
        return real(request)

    monkeypatch.setattr(ark.transport, "handler", open_comments)  # the fake's transport holds handle
    thread = await unlinked.http.get("/forum/thread/700001")
    assert thread.status_code == 200, thread.text
    assert "First post of Welcome to the forum" in thread.text
    assert "<form class='newcomment'" not in thread.text
    assert (
        "data-writes='off'" in thread.text
        and LINK in thread.text
        and "Link Archidekt to reply" in thread.text
    )


async def test_a_linked_member_still_gets_the_reply_box(stack: Stack) -> None:
    await linked_user(stack)
    b = Browser(stack.h)
    await b.login("/forum")
    try:
        thread = (await b.http.get("/forum/thread/700001")).text
        assert "<form class='newcomment'" in thread and "Link Archidekt to reply" not in thread
        home = (await b.http.get("/forum")).text
        assert "Link Archidekt to reply" not in home
    finally:
        await b.aclose()
