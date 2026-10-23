"""The member's Archidekt profile and account settings (parity release 2, REQUESTS.md section 9):
each a ``PATCH /users/{id}/private/`` with one group, as the site sends them, proposed as an
account action (actions.py) that only the member's own press applies. Also the reads: the profile
as it is now and the notification list (no mark-as-read request was seen in Archidekt's scripts, so
the gateway only shows them).

Unknown, and so held to the gateway's own limits (said in the tool's description): the bio's
length on Archidekt (BIO_MAX here) and its username rules (Django's default pattern here; Archidekt
answers a name it refuses, and the review says so)."""

from __future__ import annotations

import asyncio
import json
import re
import unicodedata
from typing import TYPE_CHECKING, Any

from .archidekt import plain_text
from .decklist import clean_text

if TYPE_CHECKING:
    from .decks import DeckService

BIO_MAX = 1000
USERNAME_RE = re.compile(r"[\w.@+-]{1,150}")
EMAIL_SWITCHES = {
    "contactable": ("profile", "contactable", "Other members may email you"),
    "newsletter": ("profile", "newsletterable", "Archidekt's newsletter"),
    "comment_emails": ("profile", "comment_notification_emailable", "Emails about comments on your decks"),
    "site_notifications": ("accountSettings", "noArchidektNotifications", "Archidekt's notifications"),
}
# New Device Default Settings: the choices the site's controls offer (REQUESTS.md section 9)
DECK_VIEWS = {"card": 0, "text": 1, "grid": 2, "scroll": 3, "table": 4, "brewer": 5}
GAMES = {"paper": 1, "mtgo": 2, "arena": 3}
PREFERENCES = {
    "deck_view": ("deckView", "Deck view", DECK_VIEWS),
    "search_game": ("searchGame", "Card search shows", GAMES),
    "unique_search": ("searchUnique", "Card search shows one printing per card", {"on": 1, "off": 0}),
    "use_default_categories": (
        "noUseDefaultCategories",
        "Use my default categories when adding cards",
        {"on": False, "off": True},
    ),
    "show_custom_uploads": ("show_custom_uploads", "Show custom card uploads", {"on": True, "off": False}),
}
FIELDS = ("bio", "username", "email", "preference", "collection_public")


def _err(text: str) -> Exception:
    from .decks import DeckError

    return DeckError("invalid", text)


def _bio_text(value: Any) -> str:
    """The bio as plain lines: a Quill document reduced to its words, control and format
    characters (bidi overrides among them) dropped, line breaks kept."""
    text = plain_text(str(value or ""))
    text = "".join(
        ch for ch in text if ch == "\n" or unicodedata.category(ch) not in ("Cc", "Cf", "Zl", "Zp")
    )
    return "\n".join(line.rstrip() for line in text.strip().splitlines())


def bio_doc(text: str) -> str:
    """Plain words as the Quill document string Archidekt keeps a bio in."""
    return json.dumps({"ops": [{"insert": (text + "\n") if text else "\n"}]})


def bio_words(doc: Any) -> str:
    try:
        ops = json.loads(doc).get("ops") if isinstance(doc, str) else None
    except (ValueError, AttributeError):
        return ""
    return "".join(
        str(o.get("insert")) for o in ops or [] if isinstance(o, dict) and isinstance(o.get("insert"), str)
    ).strip()


def _onoff(value: Any, what: str) -> bool:
    if not isinstance(value, bool):
        raise _err(f"{what} takes true or false")
    return value


def plan(field: Any, params: dict[str, Any], now: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """(the PATCH body, the review row's words) for one change, checked against the profile as it
    is now (``now``: what /users/{id}/private/ answers)."""
    prof = now.get("profile") or {}
    sett = now.get("accountSettings") or {}
    if field == "bio":
        text = _bio_text(params.get("bio"))
        if len(text) > BIO_MAX:
            raise _err(f"A bio is at most {BIO_MAX} characters here.")
        before = bio_words(prof.get("bio"))
        return {"profile": {"bio": bio_doc(text)}}, {
            "text": "Change your public Archidekt bio" if text else "Clear your public Archidekt bio",
            "before_text": before or "(empty)",
            "after_text": text or "(empty)",
            "same": before == text,
        }
    if field == "username":
        name = clean_text(str(params.get("username") or ""))
        if not USERNAME_RE.fullmatch(name):
            raise _err("A username is 1 to 150 letters, digits and . @ + - _ (Archidekt may refuse some).")
        return {"username": name}, {
            "text": f"Change your Archidekt username from {now.get('username')} to {name}",
            "note": "Your profile and deck addresses on Archidekt use your username, so old links to "
            "your profile stop working. The gateway keeps your link.",
            "same": name == now.get("username"),
        }
    if field == "email":
        switch = params.get("switch")
        if switch not in EMAIL_SWITCHES:
            raise _err(f"switch must be one of: {', '.join(EMAIL_SWITCHES)}")
        group, key, label = EMAIL_SWITCHES[switch]
        on = _onoff(params.get("on"), "on")
        value = (not on) if key == "noArchidektNotifications" else on
        was = (sett if group == "accountSettings" else prof).get(key)
        return {group: {key: value}}, {"text": f"{label}: {'on' if on else 'off'}", "same": was == value}
    if field == "preference":
        pref = params.get("preference")
        if pref not in PREFERENCES:
            raise _err(f"preference must be one of: {', '.join(PREFERENCES)}")
        key, label, choices = PREFERENCES[pref]
        pick = str(params.get("value") or "").strip().lower()
        if pick not in choices:
            raise _err(f"{pref} takes one of: {', '.join(choices)}")
        value = choices[pick]
        return {"accountSettings": {key: value}}, {
            "text": f"{label}: {pick} (Archidekt's New Device Default Settings)",
            "same": sett.get(key) == value,
        }
    if field == "collection_public":
        on = _onoff(params.get("on"), "on")
        return {"profile": {"is_collection_public": on}}, {
            "text": "Make your Archidekt collection public: anyone can see it"
            if on
            else "Make your Archidekt collection private",
            "same": prof.get("is_collection_public") == on,
        }
    raise _err(f"field must be one of: {', '.join(FIELDS)}")


def landed(body: dict[str, Any], now: dict[str, Any]) -> bool:
    """Whether a re-read profile has what ``body`` sent."""
    if "username" in body and now.get("username") != body["username"]:
        return False
    for group in ("profile", "accountSettings"):
        for k, v in (body.get(group) or {}).items():
            got = (now.get(group) or {}).get(k)
            if k == "bio":
                if bio_words(got) != bio_words(v):
                    return False
            elif got != v:
                return False
    return True


def user_id(decks: DeckService, sub: str) -> int:
    _token, link = decks._token(sub)
    uid = str(link.get("archidekt_user_id") or "")
    if not uid.isdigit():
        from .decks import DeckError

        raise DeckError(
            "unavailable",
            "The gateway doesn't know your Archidekt account's number yet. Link Archidekt again on "
            "the Account page, then try again.",
        )
    return int(uid)


async def read(decks: DeckService, sub: str) -> dict[str, Any]:
    uid = user_id(decks, sub)
    return await decks._call(sub, lambda token, *_: decks.client.user_private(token, uid))


async def send(decks: DeckService, sub: str, body: dict[str, Any]) -> dict[str, Any]:
    """Send one stored change and read the profile back. A new username is stored on the link."""
    decks._writes_switch()
    uid = user_id(decks, sub)
    client = decks.client
    await decks._call_unslotted(
        sub,
        lambda token, *_: client.patch_user_private(
            token,
            uid,
            profile=body.get("profile"),
            settings=body.get("accountSettings"),
            username=body.get("username"),
        ),
    )
    back = await decks._call_unslotted(sub, lambda token, *_: client.user_private(token, uid))
    ok = landed(body, back)
    if ok and "username" in body:
        decks.db.set_link_username(sub, str(body["username"]))
    return {"verified": ok}


async def notifications(decks: DeckService, sub: str) -> dict[str, Any]:
    uid = user_id(decks, sub)
    rows = await decks._call(sub, lambda token, *_: decks.client.notifications(token, uid))
    out = []
    for n in rows[:100]:
        if not isinstance(n, dict):
            continue
        out.append(
            {
                "kind": str(n.get("notificationType") or ""),
                "text": clean_text(str(n.get("text") or ""))[:500],
                "seen": bool(n.get("seen")),
                "url": notification_url(n),
            }
        )
    return {"notifications": out, "unseen": sum(1 for n in out if not n["seen"])}


def notification_url(n: dict[str, Any]) -> str | None:
    """Where the site's bell sends each kind (REQUESTS.md section 10); None when unknown."""
    kind = str(n.get("notificationType") or "")
    link = n.get("linkId")
    own = n.get("selfId") if n.get("selfId") is not None else link
    if kind in ("ForumThread", "Global", "Sticky", "Features") and str(own).isdigit():
        return f"https://archidekt.com/forum/thread/{own}"
    if kind in ("Deck", "DeckThread", "DeckUserEditorLink") and str(link).isdigit():
        return f"https://archidekt.com/decks/{link}"
    if kind == "News" and str(link).isdigit():
        return f"https://archidekt.com/news/{link}"
    if kind == "Profile" and isinstance(link, str | int) and str(link).strip():
        return f"https://archidekt.com/user/{link}"
    if kind == "Contest" and str(link).isdigit():
        return f"https://archidekt.com/contest/discussion/{link}"
    return None


def summary(now: dict[str, Any]) -> dict[str, Any]:
    """The profile in the gateway's words (the names propose_profile_change takes)."""
    prof = now.get("profile") or {}
    sett = now.get("accountSettings") or {}
    email = {}
    for name, (group, key, _label) in EMAIL_SWITCHES.items():
        got = (sett if group == "accountSettings" else prof).get(key)
        if isinstance(got, bool):
            email[name] = (not got) if key == "noArchidektNotifications" else got
    prefs = {}
    for name, (key, _label, choices) in PREFERENCES.items():
        prefs[name] = next((k for k, v in choices.items() if v == sett.get(key)), None)
    return {
        "username": str(now.get("username") or ""),
        "bio": bio_words(prof.get("bio")),
        "collection_public": prof.get("is_collection_public"),
        "email": email,
        "preferences": prefs,
    }


AVATAR_MAX_BYTES = 1_000_000  # Archidekt's own upload box: JPEG or PNG up to 1 MB
AVATAR_FORMATS = {"JPEG": "image/jpeg", "PNG": "image/png"}
AVATAR_SIDE = 1024  # a bigger picture is saved at most this many pixels across


def clean_avatar(raw: bytes) -> tuple[bytes, str]:
    """The member's profile picture checked as Archidekt's box checks it (JPEG or PNG, 1 MB at
    most) and saved again in its own format, at most AVATAR_SIDE pixels across, from a fresh copy
    of its pixels, so nothing but the picture leaves the gateway (no location, camera details or
    comments). Decoded one at a time (account_lists.DECODE_LOCK), and never to more than
    account_lists.PICTURE_MAX_PIXELS. (picture, file name)."""
    from .account_lists import DECODE_LOCK

    with DECODE_LOCK:
        return _clean_avatar(raw)


async def clean_avatar_async(raw: bytes) -> tuple[bytes, str]:
    """clean_avatar off the event loop, waiting for its turn there (account_lists.decode_turn)."""
    from .account_lists import decode_turn

    async with decode_turn():
        return await asyncio.to_thread(clean_avatar, raw)


def _clean_avatar(raw: bytes) -> tuple[bytes, str]:
    import io

    from PIL import Image, UnidentifiedImageError

    from .account_lists import PICTURE_MAX_PIXELS, jpeg_scale

    if not raw:
        raise _err("Choose a picture first.")
    if len(raw) > AVATAR_MAX_BYTES:
        raise _err("The picture is larger than 1 MB, Archidekt's limit. Choose a smaller one.")
    try:
        with Image.open(io.BytesIO(raw)) as probe:
            fmt = probe.format
            w, h = probe.size
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise _err("That file is not a picture the gateway can read. Use JPEG or PNG.") from exc
    if fmt not in AVATAR_FORMATS:
        raise _err("Use a JPEG or PNG picture.")
    with Image.open(io.BytesIO(raw)) as img:
        ratio = min(1.0, AVATAR_SIDE / max(w, h))
        jpeg_scale(img, (round(w * ratio), round(h * ratio)))
        if img.size[0] * img.size[1] > PICTURE_MAX_PIXELS:
            raise _err("The picture has too many pixels. Choose a smaller one.")
        img.load()
        if img.mode not in ("1", "P"):  # shrunk first, so only the small picture is converted
            img.thumbnail((AVATAR_SIDE, AVATAR_SIDE), Image.LANCZOS)
        pic = img.convert("RGB") if fmt == "JPEG" else img.convert("RGBA")
    pic.thumbnail((AVATAR_SIDE, AVATAR_SIDE), Image.LANCZOS)
    clean = Image.new(pic.mode, pic.size)
    clean.paste(pic)
    out = io.BytesIO()
    if fmt == "JPEG":
        clean.save(out, format="JPEG", quality=92)
    else:
        clean.save(out, format="PNG", optimize=True)
    data = out.getvalue()
    if len(data) > AVATAR_MAX_BYTES:
        raise _err("The picture is larger than 1 MB once saved again. Choose a smaller one.")
    return data, "avatar.jpg" if fmt == "JPEG" else "avatar.png"


async def set_avatar(decks: DeckService, sub: str, raw: bytes) -> dict[str, Any]:
    """The member's own press on the Account page: the picture checked, saved again and sent as
    Archidekt's box sends it (PUT /users/uploadAvatar/{id}/), then the profile read back. Never
    from an assistant."""
    data, name = await clean_avatar_async(raw)
    decks._writes_switch()
    uid = user_id(decks, sub)
    client = decks.client
    url = await decks._call(sub, lambda token, *_: client.upload_avatar(token, uid, data, name))
    decks._audit("profile_picture_uploaded", sub=sub)
    back = await read(decks, sub)
    got = (back.get("profile") or {}).get("avatar")
    return {"avatar": got, "verified": isinstance(url, str) and got == url}
