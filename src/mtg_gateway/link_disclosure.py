"""What linking an Archidekt account gives this gateway and the person who runs it.

Shown above the link form, where a required tick acknowledges it before anything is sent to
Archidekt, and kept readable afterwards on the Account page. A short summary is always open; the
full detail is folded, and the tick box stays disabled until the member opens it
(``static/disclosure.js``; without JavaScript the detail shows open and the box works). The
server refuses a link without the tick either way. It is always shown: no setting turns it off,
because it is there to protect the member, not the operator.

Every sentence has to stay true of the code; the source of each claim is noted beside it:

- password sent once, never stored: ``ArchidektClient.login``, ``DeckService.link`` (the stored
  blob holds the two tokens and the member's subject only);
- what the session is used for: the ``ArchidektClient`` methods reached through
  ``DeckService._call`` (decks, folders, tags, collection, deletes, backup copies when
  ``MTG_ARCHIDEKT_BACKUPS``), ``DeckService.get_any_deck`` (other people's decks read as the
  member), card lookups while building or applying changes (``/cards/v2/`` in ``_resolve_adds``,
  ``_printing_entries``, ``_apply_create``, ``collection.py``, sent with the member's session)
  and ``social.py`` (comment threads, the following list, browser-only clicks);
- what the operator can read: every table in ``db.py`` keyed by the member (``users.sub`` is the
  sign-in service's user ID), the pictures under
  ``<data dir>/avatars`` (``avatars.py``) and ``idp_grants`` (Fernet, same key);
- what admins see: ``admin.py`` user detail (name, email, subject, groups, sign-in times,
  Archidekt username, apps, token and session counts, disabled date) and the activity log;
- access token about an hour, refresh token about 40 days, refresh token not rotated: Archidekt's
  own token claims, measured on a live account on 2026-10-05 (``ArchidektClient.refresh``); the
  date shown on the Account page is read from the member's own stored token
  (``DeckService._expiry``, ``link_expires_at``);
- admins: ``admin.py`` shows the Archidekt username, the activity log and the unlink, disable,
  enable, revoke and delete actions, and calls nothing that uses a member's session;
- logs: no logger is given the password or a token (``tests/test_link_disclosure.py`` checks the
  DEBUG output of a link, refresh, use and unlink); httpx's request lines (Archidekt URLs) are
  logged at DEBUG only, httpcore (response headers) never (``__main__.py``); the Archidekt client
  keeps no cookies (``ArchidektClient.__init__``);
- who else: any request signed in as the member (browser session, connected app) acts with the
  member's link; the identity provider decides who can sign in as whom;
- kept after unlink: ``Database.revoke_link`` blanks only the session; ``delete_member_data``
  removes the row; the audit log is kept for ``AUDIT_RETENTION_SECONDS`` (a year);
- backups: ``Database.backup_to`` blanks every stored session before the copy is written;
- ending it: unlink, delete my data, admin unlink or disable blank the stored session at once;
  the optional hourly clean-up (``idp_sweep.py``) does for members Authentik no longer lists;
  removal from the required group does so only at the member's next request (``membership.py``),
  and the hourly purge does once the stored refresh token has expired
  (``DeckService.purge_expired_links``).

Not known, so not claimed: whether Archidekt ends sessions it already issued when the password
changes, and whether Archidekt has any way to sign a session out early.
"""

from __future__ import annotations

import html

# Shown first, always open: the few things a member most needs to know.
SUMMARY: tuple[str, ...] = (
    "The gateway sends your Archidekt password to Archidekt once and never stores it. It keeps "
    "the sign-in session Archidekt returns, encrypted. Assume that session can do anything your "
    "Archidekt sign-in can.",
    "The person who runs this server holds the key, so they could use the session to act as you "
    "on Archidekt until it expires (about 40 days after you link), and they can read everything "
    "else the gateway keeps about you. Link only if you trust them.",
    "Admins can see your account details and activity log, but not your password or session, and "
    "nothing on the admin pages uses your link. An admin who also has access to the server can do "
    "what the person who runs it can.",
    "You can unlink at any time. Whether Archidekt itself ends the session when you unlink or "
    "change your password is not known.",
)

# (heading, [lines]); each line is plain text, escaped when rendered.
SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "How linking works",
        (
            "Your Archidekt username or email and password go from this server to Archidekt once, "
            "to sign you in. The gateway never stores your password.",
            "Archidekt returns a sign-in session, the same kind archidekt.com gets. We know of no "
            "limited version, so assume it can do whatever your Archidekt sign-in can. The gateway "
            "keeps it so it doesn't need your password again.",
        ),
    ),
    (
        "What it uses the session for",
        (
            "Only when you, or an assistant you connected, ask for something. A big change you "
            "approved may finish in the background.",
            "Reading your decks (private ones included), folders, tags and collection.",
            "Reading, as you, other people's decks and comment threads you or your assistant look "
            "at and the list of people you follow, and looking up the cards in the changes you make.",
            "Making the deck, folder, tag and collection changes you make yourself, approve, or let "
            "your assistant make through your approval mode, including deleting a deck when you "
            "ask. It may first save a copy of the deck in a backup folder on your Archidekt account "
            "(the person who runs the server can turn that off).",
            "The likes, bookmarks, follows and comments you make yourself on these pages. No "
            "assistant can do those.",
        ),
    ),
    (
        "What is stored",
        (
            "Encrypted: the session (an access token and the refresh token that renews it).",
            "Plain text: your Archidekt username and user number (if Archidekt sends no username, "
            "what you typed to sign in, which may be your email); when you linked and when the "
            "session was last renewed and used; activity-log entries for each link, unlink, "
            "renewal and failed attempt.",
            "Never stored: your password.",
            "After an unlink, your Archidekt username and user number stay until you or an admin "
            "delete your data. Activity-log entries, including the Archidekt username noted when "
            "you linked, are kept for a year, even after your data is deleted.",
        ),
    ),
    (
        "What the person who runs this server can do",
        (
            "The key that opens the session is on the same server. Anyone with access to the "
            "server, including the person who runs it, can use the session to act as you on "
            "Archidekt until it expires: read, change or "
            "delete your decks and collection, or anything else your sign-in allows. If Archidekt "
            "lets a session change your email or password (not known), they could use that to "
            "keep your account. They cannot get your password from the session.",
            "They can read everything else the gateway keeps about you, unencrypted: your name, "
            "email, username, user ID and groups at the sign-in service, profile picture (if "
            "sent), sign-in and last-seen times, approval mode, Archidekt username and user "
            "number, proposed and applied deck and collection changes, deck snapshots, reports and "
            "covers, scans, connected apps, usage counts and the activity log. The sign-in "
            "service's tokens the gateway keeps to check your groups use the same key, so they can "
            "open those too.",
            "They control the code this server runs. This gateway's code never keeps your "
            "password, but changed code could.",
        ),
    ),
    (
        "What admins on this site can do",
        (
            "On the admin pages they see your name, email, username, groups and user ID at the "
            "sign-in service, your Archidekt username, first sign-in and last seen, connected "
            "apps, how many app tokens and browser sessions you have open, whether and when you "
            "were disabled, and the activity log: what you did, when and with which app (links, "
            "unlinks, proposals, deck and collection changes, scans, likes, bookmarks, follows and "
            "comments, with deck, proposal and comment numbers).",
            "They can unlink your Archidekt account, disable and enable your account, sign you "
            "out everywhere and delete your data.",
            "They cannot see your password or session, cannot open your proposals, and have no "
            "button that uses your link. An admin who also has server access can do everything in "
            "the section above.",
        ),
    ),
    (
        "Who else can use your link",
        (
            "Anyone who can sign in here as you: someone holding your browser session, an "
            "assistant you connected, or whoever runs the sign-in service, if they can sign in as "
            "you there.",
        ),
    ),
    (
        "Logs and backups",
        (
            "Logs never contain your password or session. At the usual log level they hold no "
            "Archidekt addresses or usernames; a few messages include a deck number. With debug "
            "logging on, they list the Archidekt addresses called, which include deck numbers and "
            "usernames.",
            "The gateway's own backups leave out the session and keep everything else above. A "
            "copy of the server's disk made another way includes the session, still encrypted.",
        ),
    ),
    (
        "How long it lasts and how to end it",
        (
            "The access token lasts about an hour and is renewed with the refresh token. The "
            "refresh token stops working about 40 days after you link (measured October 2026; "
            "Archidekt can change this). Then you link again. Your Account page shows your date.",
            "Unlink, Delete my data, or an admin's Unlink or Disable deletes the gateway's copy at once.",
            "If you're removed from this gateway's group, or your sign-in account is deactivated "
            "or deleted, you lose access at once. If the hourly clean-up is on (it needs an extra "
            "setting at the sign-in service), your stored session is usually deleted within about "
            "an hour, later while the sign-in service gives no clear answer. Without it, the "
            "session is deleted when an admin presses Disable or Unlink, when it expires, or "
            "(after a group removal) the next time you or one of your apps uses the gateway.",
            "Unlinking deletes only the gateway's copy. Whether the session also stops working at "
            "Archidekt, or ends when you change your Archidekt password, is not known, so a copy "
            "someone already took may keep working until it expires.",
        ),
    ),
)

# Archidekt's own position on apps like this one: shown with the disclosure, same tick.
TERMS_NOTE = (
    "Archidekt has no official way for other apps to read or change decks, so the gateway uses "
    "the same requests archidekt.com's own pages use. Archidekt's terms of service restrict "
    "automated access, so Archidekt could limit or block an account used this way."
)

ACKNOWLEDGE = (
    "I have read what linking gives this gateway and the person who runs it, and I want to link my account."
)


TITLE = "Before you link your Archidekt account"
DETAIL_SUMMARY = "Full detail (open it to tick the box below)"
DETAIL_ID = "archidekt-disclosure-detail"
# Folds the detail and unlocks the tick box once it is opened (script-src 'self', no inline).
SCRIPT = "/static/disclosure.js"
TICK_HINT = "Open the full detail above to tick this box."

SWEEP_ON = "On this gateway the hourly clean-up is on."
SWEEP_OFF = "On this gateway the hourly clean-up is off."


def _summary_html() -> str:
    return "<h3>In short</h3><ul>" + "".join(f"<li>{html.escape(x)}</li>" for x in SUMMARY) + "</ul>"


def body_html(sweep_on: bool | None = None) -> str:
    """The detail sections as HTML (headings and lists), without a wrapper. ``sweep_on`` adds
    whether this gateway runs the removed-member clean-up (idp_sweep.py) to the last section."""
    out = []
    for i, (heading, lines) in enumerate(SECTIONS):
        items = [html.escape(line) for line in lines]
        if sweep_on is not None and i == len(SECTIONS) - 1:
            items.append(html.escape(SWEEP_ON if sweep_on else SWEEP_OFF))
        out.append(f"<h3>{html.escape(heading)}</h3><ul>{''.join(f'<li>{x}</li>' for x in items)}</ul>")
    return "".join(out)


def _terms_html() -> str:
    return f"<h3>Archidekt's terms</h3><p>{html.escape(TERMS_NOTE)}</p>"


def form_html(sweep_on: bool | None = None, *, read: bool = False) -> str:
    """Above the link form: the summary, then the full detail.

    The detail is rendered open and the tick box usable, so without JavaScript everything shows
    and nobody is locked out. ``static/disclosure.js`` folds it and keeps the tick box disabled
    until the member opens it (``data-must-open``). ``read`` (the form came back after a failed
    link the member had already ticked) leaves it open and unlocked."""
    must = "" if read else " data-must-open"
    return (
        "<div class='notice disclosure' id='archidekt-disclosure'>"
        f"<p><strong>{html.escape(TITLE)}</strong></p>"
        f"{_summary_html()}"
        f"<details class='disclosure-detail' id='{DETAIL_ID}' open{must}>"
        f"<summary>{html.escape(DETAIL_SUMMARY)}</summary>"
        f"{body_html(sweep_on)}{_terms_html()}</details></div>"
        f"<script src='{SCRIPT}' defer></script>"
    )


def linked_html(sweep_on: bool | None = None) -> str:
    """On the Account page once linked: the same text, folded, so it stays readable later."""
    return (
        "<details class='disclosure' id='archidekt-disclosure'>"
        "<summary>What linking gives this gateway and the person who runs it</summary>"
        f"{_summary_html()}{body_html(sweep_on)}{_terms_html()}</details>"
    )


def plain_text() -> str:
    """The disclosure as plain text (for docs and review)."""
    parts = [TITLE, "", "In short"]
    parts.extend(f"- {line}" for line in SUMMARY)
    parts += ["", DETAIL_SUMMARY, ""]
    for heading, lines in SECTIONS:
        parts.append(heading)
        parts.extend(f"- {line}" for line in lines)
        parts.append("")
    parts += ["Archidekt's terms", TERMS_NOTE, "", f"Tick box: {ACKNOWLEDGE}"]
    return "\n".join(parts)
