"""A picture upload sent without a Content-Length (chunked) stops being read at the size limit
instead of being buffered whole (security review 0.7.44, S-02)."""

# ruff: noqa: F811 - the fixtures are imported and then named as parameters
from __future__ import annotations

import pytest

from .test_profile_page import member, stack_options  # noqa: F401

BOUNDARY = "xyzBOUNDARY"


def _chunked(csrf: str, field: str, sent: list[int], size: int = 30 * 1024 * 1024):
    async def body():
        head = (
            f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="csrf"\r\n\r\n{csrf}\r\n'
            f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="{field}"; filename="a.png"\r\n'
            "Content-Type: image/png\r\n\r\n"
        ).encode()
        sent[0] += len(head)
        yield head
        chunk = b"\0" * (256 * 1024)
        for _ in range(size // len(chunk)):
            sent[0] += len(chunk)
            yield chunk
        yield f"\r\n--{BOUNDARY}--\r\n".encode()

    return body()


@pytest.mark.parametrize(
    ("path", "field", "page"),
    [("/account/picture", "picture", "/account"), ("/lists/custom/picture", "picture", "/lists/custom")],
)
async def test_a_chunked_upload_stops_at_the_limit(member, path: str, field: str, page: str) -> None:
    csrf = await member.csrf(page)
    sent = [0]
    r = await member.http.post(
        path,
        content=_chunked(csrf, field, sent),
        headers={"content-type": f"multipart/form-data; boundary={BOUNDARY}"},
    )
    assert r.status_code in (200, 303, 400, 413), r.status_code
    assert "larger than" in r.text or r.status_code in (303, 413)
    assert sent[0] < 4 * 1024 * 1024, sent[0]  # read up to the limit, not the whole 30 MB
