"""OAuth Client ID Metadata Documents (CIMD): URL-shaped client ids.

An MCP client such as Claude can identify itself with an https URL instead of
registering. The URL points at a JSON document describing the client. This module
fetches and validates that document under strict server-side request forgery
(SSRF) guards, following the MCP authorization spec (2025-11-25 revision,
"Client ID Metadata Documents") and the draft it cites
(draft-ietf-oauth-client-id-metadata-document-00):

- the client_id must be an https URL with a path, and the document's own
  client_id must equal that URL exactly;
- redirect_uris, client_name are required; redirect URIs follow the same rule as
  registration (https, or http on loopback);
- the client is a public client: token_endpoint_auth_method "none" only
  (private_key_jwt is not supported here);
- documents are cached with the Cache-Control max-age, clamped to [5 min, 1 day].

SSRF guards: https on port 443 only, hostnames only (no IP literals, no
userinfo), DNS answers must all be public unicast addresses and the connection
is made to the address that was checked (the hostname is kept for SNI and
certificate verification, so a rebinding record cannot redirect the connect),
redirects are not followed, a 5 s deadline over the fetch itself (DNS, connect,
headers and body), 64 KiB cap on the bytes as sent (compressed responses are
refused, so a small gzip body cannot inflate past the cap), one fetch in flight
per site and a few overall, and a one-minute negative cache per URL so a hostile
client cannot make the gateway hammer a target.

Fairness: a caller that cannot get a slot within the deadline is told the fetcher
is busy (CimdThrottled) and nothing is remembered against its URL, and a timeout
is not negative-cached either; only an answer the URL's own server gave (bad
status, bad document, unresolvable or private host) is. So slow hostile hosts
can delay other sign-ins but cannot get a healthy client_id locked out, and since
each site gets one slot, one attacker domain holds at most one of them.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import re
import socket
import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx
from pydantic import AnyUrl

logger = logging.getLogger(__name__)

MAX_DOCUMENT_BYTES = 64 * 1024
FETCH_TIMEOUT = 5.0
MIN_TTL = 300
MAX_TTL = 86400
DEFAULT_TTL = 3600
FAILURE_TTL = 60
MAX_CONCURRENT_FETCHES = 8  # unknown client ids are unauthenticated input; keep outbound fan-out small
REQUIRED_FIELDS = ("client_id", "client_name", "redirect_uris")

Resolver = Callable[[str], Awaitable[list[str]]]


class CimdError(Exception):
    """The document could not be fetched or is not an acceptable client description."""


class CimdThrottled(CimdError):
    """The same URL failed less than a minute ago; nothing was fetched this time."""


def is_cimd_client_id(client_id: str) -> bool:
    """True when ``client_id`` has the shape of a metadata document URL."""
    # ASCII only: a Unicode (IDN) host would fail later inside httpx with a UnicodeEncodeError
    # (a server error); a client that wants an IDN host publishes its A-label (xn--) form.
    if len(client_id) > 512 or not client_id.isascii():
        return False
    try:
        p = urlparse(client_id)
        port = p.port  # raises on a malformed port, as urlparse does on a malformed IPv6 literal
    except ValueError:
        return False
    return (
        p.scheme == "https"
        and bool(p.hostname)
        and _plausible_host(p.hostname or "")
        and p.path not in ("", "/")
        and not p.username
        and not p.password
        and not p.fragment
        and (port in (None, 443))
    )


def _site(host: str) -> str:
    """The last two labels of ``host``: the unit that gets one fetch slot. Coarser than a
    registrable domain for names like example.co.uk, which only means fewer parallel fetches."""
    return ".".join(host.lower().rstrip(".").split(".")[-2:])


NAT64_PREFIXES = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48"))
# ::ffff:0:a.b.c.d, the IPv4-translated form of SIIT (RFC 2765/6145); it carries an IPv4 address.
SIIT_TRANSLATED = ipaddress.ip_network("::ffff:0:0:0/96")
# Global unicast IPv6. Anything outside it (deprecated site-local fec0::/10, ULA, link-local and
# the rest) is never a public address, whatever ``is_global`` says about it.
GLOBAL_UNICAST_V6 = ipaddress.ip_network("2000::/3")


def _embedded_ipv4(addr: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    """The IPv4 address an IPv6 transition form carries, if any (mapped, SIIT-translated,
    compatible, NAT64, 6to4)."""
    if addr.ipv4_mapped is not None:
        return addr.ipv4_mapped
    if addr in SIIT_TRANSLATED:
        return ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF)
    if int(addr) < 2**32:  # ::a.b.c.d, the deprecated IPv4-compatible form (covers ::/96)
        return ipaddress.IPv4Address(int(addr))
    if any(addr in net for net in NAT64_PREFIXES):
        return ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF)
    if addr.sixtofour is not None:
        return addr.sixtofour
    return None


def _address_is_public(ip: str) -> bool:
    addr: ipaddress.IPv4Address | ipaddress.IPv6Address = ipaddress.ip_address(ip)
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.teredo is not None:
            return False
        embedded = _embedded_ipv4(addr)
        if embedded is not None:
            addr = embedded
        elif addr not in GLOBAL_UNICAST_V6:
            return False
    return addr.is_global and not addr.is_multicast and not addr.is_private and not addr.is_reserved


async def default_resolver(host: str) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


_HOSTNAME = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*\.?$")


def _plausible_host(host: str) -> bool:
    """A DNS hostname or an IP literal: nothing else may name a redirect target. The host
    also ends up in the consent page's Content-Security-Policy, so wildcards and other
    punctuation are refused here rather than relied on being harmless there."""
    if len(host) > 253:
        return False
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return bool(_HOSTNAME.match(host))


def _is_loopback(host: str) -> bool:
    """``localhost`` or a loopback IP literal (127.0.0.0/8, ::1), judged on the parsed host."""
    if host.lower().rstrip(".") == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _same_host(a: str, b: str) -> bool:
    try:
        return ipaddress.ip_address(a) == ipaddress.ip_address(b)
    except ValueError:
        return a.lower().rstrip(".") == b.lower().rstrip(".")


def check_redirect_uri(uri: str) -> str | None:
    """Return why ``uri`` is not acceptable as a redirect URI, or None when it is.

    urllib and the WHATWG parser the OAuth models use disagree on some strings (a backslash is
    a path separator to one and an ordinary character to the other, so ``http://evil\\@localhost``
    is localhost to urllib and evil to the browser). Such strings are refused outright: no
    backslashes, whitespace or control characters, and no userinfo. The URL is then parsed again
    as the OAuth models parse it, and its host must be the one checked here.
    """
    if any(ch == "\\" or ch.isspace() or not ch.isprintable() for ch in uri):
        return f"redirect URI contains a backslash, whitespace or a control character: {uri[:80]!r}"
    try:
        parsed = urlparse(uri)
        host = parsed.hostname
        _ = parsed.port  # raises on a malformed port
    except ValueError:
        return f"redirect URI is malformed: {uri[:80]}"
    if "@" in parsed.netloc or parsed.username is not None or parsed.password is not None:
        return f"redirect URI must not contain a user name or password: {uri[:80]}"
    if not host or not _plausible_host(host):
        return f"redirect URI must name a host: {uri[:80]}"
    try:
        normalised = AnyUrl(uri)
    except ValueError:
        return f"redirect URI is malformed: {uri[:80]}"
    if not _same_host((normalised.host or "").strip("[]"), host) or normalised.scheme != parsed.scheme:
        return f"redirect URI is ambiguous: {uri[:80]}"
    if parsed.scheme == "https":
        return None
    if parsed.scheme == "http" and _is_loopback(host):
        return None
    return f"redirect URI must be https, or http on localhost: {uri[:80]}"


CLIENT_NAME_MAX = 60


RESERVED_CLIENT_NAMES = ("browser", "this browser", "web browser")


def sanitise_client_name(name: str) -> str:
    """A client name as the consent page may show it: printable characters only (which drops
    bidi overrides and other format characters) and at most CLIENT_NAME_MAX of them."""
    clean = "".join(ch for ch in name if ch.isprintable())[:CLIENT_NAME_MAX]
    # "browser" is how the gateway labels the member's own web session in activity lists, so an
    # app can't take that name.
    if clean.strip().casefold() in RESERVED_CLIENT_NAMES:
        clean = f"{clean.strip()} (app)"
    return clean


def _cache_ttl(cache_control: str | None) -> int:
    if cache_control:
        m = re.search(r"max-age=(\d+)", cache_control)
        if m:
            return max(MIN_TTL, min(MAX_TTL, int(m.group(1))))
    return DEFAULT_TTL


def validate_document(url: str, doc: Any) -> dict[str, Any]:
    """Check a fetched document and return the client record the gateway will use."""
    if not isinstance(doc, dict):
        raise CimdError("metadata document is not a JSON object")
    for field in REQUIRED_FIELDS:
        if not doc.get(field):
            raise CimdError(f"metadata document lacks {field}")
    if doc["client_id"] != url:
        raise CimdError("metadata document client_id does not match its URL")
    if not isinstance(doc["client_name"], str) or len(doc["client_name"]) > 200:
        raise CimdError("client_name must be a short string")
    # Shown on the consent page, so treated like a registered client's name: printable
    # characters only (no bidi or other control characters) and at most CLIENT_NAME_MAX of them,
    # so it cannot pass itself off as the page's own wording.
    client_name = sanitise_client_name(doc["client_name"])
    if not client_name.strip():
        raise CimdError("client_name has no printable characters")
    uris = doc["redirect_uris"]
    if not isinstance(uris, list) or not all(isinstance(u, str) for u in uris) or len(uris) > 20:
        raise CimdError("redirect_uris must be a list of strings")
    for uri in uris:
        problem = check_redirect_uri(uri)
        if problem:
            raise CimdError(problem)
    method = doc.get("token_endpoint_auth_method", "none")
    if method != "none":
        raise CimdError(f"token_endpoint_auth_method {method!r} is not supported; only public clients")
    grants = doc.get("grant_types") or ["authorization_code"]
    if "authorization_code" not in grants:
        raise CimdError("grant_types must include authorization_code")
    responses = doc.get("response_types") or ["code"]
    if "code" not in responses:
        raise CimdError("response_types must include code")
    scope = doc.get("scope")
    if scope is not None and not isinstance(scope, str):
        raise CimdError("scope must be a string")
    record: dict[str, Any] = {
        "client_id": url,
        "client_name": client_name,
        "redirect_uris": uris,
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }
    for key in ("client_uri", "logo_uri", "policy_uri", "tos_uri"):
        value = doc.get(key)
        if isinstance(value, str) and value.startswith("https://") and len(value) <= 512:
            record[key] = value
    if scope:
        record["scope"] = scope
    return record


class CimdFetcher:
    """Fetches client metadata documents with the guards described in the module docstring."""

    def __init__(
        self,
        *,
        http: httpx.AsyncClient | None = None,
        resolver: Resolver | None = None,
        allowed_hosts: list[str] | None = None,
        timeout: float = FETCH_TIMEOUT,
    ):
        self._http = http or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout), follow_redirects=False, trust_env=False
        )
        self._resolve = resolver or default_resolver
        self.timeout = timeout
        self.allowed_hosts = [h.lower() for h in allowed_hosts or []]
        self._failures: dict[str, float] = {}
        self._gate = asyncio.Semaphore(MAX_CONCURRENT_FETCHES)
        self._site_locks: dict[str, asyncio.Lock] = {}  # one fetch in flight per site
        self._site_users: dict[str, int] = {}  # callers holding or waiting on each lock

    async def aclose(self) -> None:
        await self._http.aclose()

    def _host_allowed(self, host: str) -> bool:
        if not self.allowed_hosts:
            return True
        host = host.lower()
        return any(host == h or host.endswith("." + h) for h in self.allowed_hosts)

    async def fetch(self, url: str) -> tuple[dict[str, Any], int]:
        """Return (client record, cache ttl seconds) or raise CimdError."""
        if not is_cimd_client_id(url):
            raise CimdError("client_id is not an https URL with a path")
        now = time.monotonic()
        failed_at = self._failures.get(url)
        if failed_at is not None and now - failed_at < FAILURE_TTL:
            raise CimdThrottled("metadata document fetch failed recently; not retrying yet")
        site = _site(urlparse(url).hostname or "")
        lock = self._site_locks.setdefault(site, asyncio.Lock())
        self._site_users[site] = self._site_users.get(site, 0) + 1
        try:
            try:
                # Waiting for a slot has its own deadline; running out of it says nothing about
                # this URL, so it is reported as busy and not remembered as a failure.
                async with asyncio.timeout(self.timeout):
                    await lock.acquire()
                    try:
                        await self._gate.acquire()
                    except BaseException:
                        lock.release()
                        raise
            except TimeoutError as exc:
                raise CimdThrottled("metadata document fetcher is busy; try again shortly") from exc
            try:
                # A host that drips one byte at a time must not hold a slot for longer than this.
                async with asyncio.timeout(self.timeout):
                    return await self._fetch(url)
            except TimeoutError as exc:
                # Not negative-cached: a slow network or an overloaded gateway must not lock a
                # healthy client out for a minute. The per-site slot already stops a hostile
                # client_id from keeping more than one connection to a target open.
                raise CimdError("metadata document fetch timed out") from exc
            except CimdError:
                self._note_failure(url, now)
                raise
            finally:
                self._gate.release()
                lock.release()
        finally:
            self._site_users[site] -= 1
            if not self._site_users[site]:
                del self._site_users[site]
                del self._site_locks[site]

    def _note_failure(self, url: str, now: float) -> None:
        self._failures[url] = now
        if len(self._failures) > 1000:  # bounded even under abuse
            self._failures = {u: t for u, t in self._failures.items() if now - t < FAILURE_TTL}

    async def _fetch(self, url: str) -> tuple[dict[str, Any], int]:
        host = urlparse(url).hostname or ""
        if not self._host_allowed(host):
            raise CimdError("client metadata host is not on the allowlist")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise CimdError("client_id must use a hostname, not an IP address")
        try:
            addresses = await self._resolve(host)
        except OSError as exc:
            raise CimdError(f"cannot resolve client metadata host: {exc.__class__.__name__}") from exc
        if not addresses or not all(_address_is_public(a) for a in addresses):
            raise CimdError("client metadata host does not resolve to a public address")
        # Connect to the address that was just checked, not to whatever the name resolves to a
        # moment later; the hostname still goes in SNI, the Host header and the certificate check.
        pinned = ipaddress.ip_address(addresses[0])
        netloc = f"[{pinned}]" if pinned.version == 6 else str(pinned)
        pinned_url = urlunparse(urlparse(url)._replace(netloc=netloc))
        try:
            async with self._http.stream(
                "GET",
                pinned_url,
                headers={
                    "Host": host,
                    "Accept": "application/json",
                    "Accept-Encoding": "identity",
                    "User-Agent": "mtg-assistant-gateway cimd",
                },
                extensions={"sni_hostname": host},
            ) as resp:
                if resp.status_code != 200:
                    raise CimdError(f"metadata document returned HTTP {resp.status_code}")
                # httpx would inflate a compressed body before the size check below sees it (one
                # network read of gzip can become tens of MB), so only plain bodies are read.
                if resp.headers.get("content-encoding", "").strip().lower() not in ("", "identity"):
                    raise CimdError("metadata document must not be compressed")
                ctype = resp.headers.get("content-type", "")
                if "json" not in ctype:
                    raise CimdError("metadata document is not JSON")
                body = bytearray()
                async for chunk in resp.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_DOCUMENT_BYTES:
                        raise CimdError("metadata document is too large")
                ttl = _cache_ttl(resp.headers.get("cache-control"))
        except (httpx.HTTPError, ValueError) as exc:
            # ValueError covers httpx's own URL and header encoding errors (UnicodeEncodeError
            # among them), which would otherwise surface as a 500 and skip the negative cache.
            raise CimdError(f"cannot fetch metadata document: {exc.__class__.__name__}") from exc
        try:
            doc = json.loads(bytes(body).decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise CimdError("metadata document is not valid JSON") from exc
        return validate_document(url, doc), ttl
