"""The request log line (timing.py) names the route, never the request: deck numbers, Archidekt
usernames, signed-link tokens and member IDs stay out of the gateway's own log, as the Archidekt
link disclosure and OPERATIONS.md promise."""

from __future__ import annotations

import logging

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Mount, Route
from starlette.testclient import TestClient

from mtg_gateway.timing import TimingMiddleware, logged_path

from .conftest import Harness


def test_logged_path_names_the_route_template() -> None:
    async def ok(_request):  # noqa: ANN001
        return PlainTextResponse("ok")

    async def raw(scope, receive, send):  # noqa: ANN001
        await PlainTextResponse("raw")(scope, receive, send)

    seen: list[str] = []

    class Spy:
        def __init__(self, app):  # noqa: ANN001
            self.app = app

        async def __call__(self, scope, receive, send):  # noqa: ANN001
            await self.app(scope, receive, send)
            seen.append(logged_path(scope))

    inner = Starlette(routes=[Route("/decks/{id}", ok)])
    app = Starlette(
        routes=[
            Route("/decks/{deck_id}/edit", ok),
            Route("/cards/data/{token}", ok),
            Route("/admin/users/{sub:path}", ok),
            Mount("/api/v1", app=inner),
            Mount("/mcp", app=raw),
        ]
    )
    app.add_middleware(Spy)
    c = TestClient(app, raise_server_exceptions=False)
    for url in (
        "/decks/4242424/edit",
        "/cards/data/SECRET-TOKEN",
        "/admin/users/member%40idp/with/slashes",
        "/api/v1/decks/55",
        "/api/v1/missing",
        "/mcp/anything/here",
        "/nothing/to/see?q=1",
    ):
        c.get(url)
    assert seen == [
        "/decks/{deck_id}/edit",
        "/cards/data/{token}",
        "/admin/users/{sub:path}",
        "/api/v1/decks/{id}",
        "/api/v1/…",
        "/mcp/…",
        "/nothing/…",
    ]
    joined = " ".join(seen)
    for secret in ("4242424", "SECRET", "member", "slashes", "55", "anything", "see"):
        assert secret not in joined
    # a control character in an unrouted path is replaced, never written as is
    assert logged_path({"path": "/\x01odd/x", "root_path": ""}) == "/?odd/…"


@pytest.mark.anyio
async def test_gateway_log_holds_no_deck_number_username_or_token(
    gw: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    h = gw
    assert any(isinstance(m, TimingMiddleware) for m in _layers(h)), "TimingMiddleware is the outer layer"
    caplog.set_level(logging.INFO, logger="mtg_gateway.requests")
    for path in (
        "/decks/4242424",
        "/users/somebody",
        "/cards/data/SECRET-TOKEN",
        "/admin/users/member-sub-1",
        "/proposals/prop-9",
        "/no-such/page",
    ):
        await h.http.get(path)
    lines = [r.getMessage() for r in caplog.records if r.name == "mtg_gateway.requests"]
    assert len(lines) == 6, lines
    assert lines[0].startswith("GET /decks/{deck_id} ")
    assert lines[1].startswith("GET /users/{username} ")
    assert lines[2].startswith("GET /cards/data/{token} ")
    assert lines[3].startswith("GET /admin/users/{sub:path} ")
    assert lines[4].startswith("GET /proposals/{pid} ")
    assert lines[5].startswith("GET /no-such/… ")
    joined = "\n".join(lines)
    for secret in ("4242424", "somebody", "SECRET", "member-sub", "prop-9", "page"):
        assert secret not in joined, joined


def _layers(h: Harness) -> list[object]:
    app = h.app.middleware_stack if h.app.middleware_stack is not None else h.app.build_middleware_stack()
    out = []
    while app is not None:
        out.append(app)
        app = getattr(app, "app", None)
    return out


def test_a_slow_request_logs_a_warning_with_its_time_split(caplog: pytest.LogCaptureFixture) -> None:
    """A request over SLOW_REQUEST_SECONDS is logged once more at WARNING, with where its time
    went (total, Archidekt, identity provider), so a slow page shows in a plain log."""
    from mtg_gateway import timing

    clock = [100.0]

    async def slow(_request):  # noqa: ANN001
        timing.add_time(timing.archidekt_time, 2.5)
        timing.add_time(timing.idp_time, 0.25)
        clock[0] += 3.2
        return PlainTextResponse("ok")

    async def quick(_request):  # noqa: ANN001
        clock[0] += 0.4
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/decks/{deck_id}", slow), Route("/home", quick)])
    app.add_middleware(TimingMiddleware)
    c = TestClient(app)
    with pytest.MonkeyPatch.context() as mp, caplog.at_level(logging.INFO, logger="mtg_gateway.requests"):
        mp.setattr(timing.time, "perf_counter", lambda: clock[0])
        c.get("/decks/4242")
        c.get("/home")
    warned = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warned) == 1, [r.getMessage() for r in caplog.records]
    text = warned[0].getMessage()
    assert "/decks/{deck_id}" in text and "4242" not in text
    assert "total=3200ms" in text and "archidekt=2500ms" in text and "idp=250ms" in text
