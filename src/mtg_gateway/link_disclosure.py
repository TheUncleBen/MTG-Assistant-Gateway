"""What linking an Archidekt account gives this gateway and the person who runs it.

Shown in full above the link form, where a required tick acknowledges it before anything is sent
to Archidekt, and kept readable afterwards on the Account page. It is always shown: no setting
turns it off, because it is there to protect the member, not the operator.

Every sentence has to stay true of the code; the source of each claim is noted beside it:

- password sent once, never stored: ``ArchidektClient.login``, ``DeckService.link`` (the stored
  blob holds the two tokens and the member's subject only);
- what the session is used for: the ``ArchidektClient`` methods reached through
  ``DeckService._call`` (decks, folders, tags, collection) and ``social.py`` (browser-only clicks);
- access token about an hour, refresh token about 40 days, refresh token not rotated: Archidekt's
  own token claims, measured on a live account on 2026-10-05 (``ArchidektClient.refresh``); the
  date shown on the Account page is read from the member's own stored token
  (``DeckService._expiry``, ``link_expires_at``);
- admins: ``admin.py`` shows the Archidekt username, the activity log and the unlink, disable,
  revoke and delete actions, and calls nothing that uses a member's session;
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

LEAD = (
    "Linking gives this gateway a working sign-in to your Archidekt account. Read what that means "
    "for you before you link."
)

# (heading, [lines]); each line is plain text, escaped when rendered.
SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "What linking does",
        (
            "Your Archidekt username or email and password go from this server to Archidekt once, "
            "to sign you in. Your password is not stored anywhere by the gateway.",
            "Archidekt answers with a sign-in session for your account: the same kind archidekt.com "
            "gets when you sign in there. As far as we know Archidekt offers no limited version, so "
            "assume the session can do whatever your Archidekt sign-in can do.",
            "The gateway keeps that session so it can work with your account without asking for "
            "your password again.",
        ),
    ),
    (
        "What the gateway uses it for",
        (
            "Reading your decks (private ones included), folders, tags and collection.",
            "Making the deck and collection changes you approve, or that your approval mode lets "
            "your assistant make.",
            "The likes, bookmarks, follows and comments you make yourself on these pages. No "
            "assistant can do those.",
            "It uses the session only when you, or an assistant you connected, ask for something. "
            "A big change you approved may finish in the background.",
        ),
    ),
    (
        "What is stored",
        (
            "Stored, encrypted: the session (a short-lived access token and the refresh token "
            "that renews it).",
            "Stored as plain text: your Archidekt username and user number (if Archidekt does not "
            "send your username, what you typed to sign in, which may be your email); when you "
            "linked, when the session was last renewed and last used; and activity-log entries for "
            "each link, unlink, renewal and failed attempt.",
            "Never stored: your Archidekt password.",
            "After an unlink, the gateway keeps your Archidekt username and user number (not the "
            "session) until you or an admin delete your data. Activity-log entries, including the "
            "Archidekt username noted when you linked, are kept for a year, even after your data "
            "is deleted.",
        ),
    ),
    (
        "What the person who runs this server can see and do",
        (
            "The session is encrypted, but the key that opens it is on the same server. Anyone "
            "with access to the server and that key can open the session and use it to act as you "
            "on Archidekt until it expires: read, change or delete your decks and collection, or "
            "anything else your Archidekt sign-in allows. If Archidekt lets a session change your "
            "email or password (not known), they could use that to keep your account after the "
            "session expires. They cannot get your password from the session.",
            "They can read everything else the gateway keeps about you, which is not encrypted: "
            "your Archidekt username, your proposed and applied deck changes, deck snapshots, "
            "scans, and the activity log.",
            "They control the code this server runs. This gateway's code never keeps your "
            "password, but someone who changes the code could. Link only if you trust the person "
            "who runs this server.",
        ),
    ),
    (
        "What admins on this site can see and do",
        (
            "Admins use the admin pages, not the server itself. They see your name, email and "
            "groups, your Archidekt username, when you first signed in and were last seen, which "
            "apps you connected, and the activity log: what you did, when and with which app, "
            "including links and unlinks, proposals, deck and collection changes, scans, likes, "
            "bookmarks, follows and comments, with deck, proposal and comment numbers.",
            "They can unlink your Archidekt account, disable your account, sign you out "
            "everywhere and delete your data.",
            "They cannot see your password or your session, cannot open your proposals, and have "
            "no button that uses your link to read or change your decks. An admin who also has "
            "access to the server can do everything in the section above.",
        ),
    ),
    (
        "Who else can use your link",
        (
            "Anyone who can sign in to this gateway as you can use your link through it, just as "
            "you can: someone holding your browser session or an assistant you connected, and the "
            "person who runs the sign-in service this gateway uses, if they can sign in as you "
            "there.",
        ),
    ),
    (
        "Logs and backups",
        (
            "The gateway never writes your password or your session to its logs.",
            "At the usual log level it writes no Archidekt addresses or usernames; a few messages "
            "include a deck number. If the person who runs it turns on debug logging, the logs "
            "list the Archidekt addresses it calls, which include deck numbers and usernames.",
            "The gateway's own backups leave your session out and keep everything else listed "
            "above. A copy of the server's disk made some other way would include the session, "
            "still encrypted.",
        ),
    ),
    (
        "How long it lasts and how to end it",
        (
            "The access token lasts about an hour; when it runs out, the gateway gets a new one "
            "with the refresh token. Archidekt's refresh token stops working about 40 days "
            "after you link (measured in October 2026; Archidekt sets this and can change it). "
            "Then you link again. Your "
            "Account page shows the date for your link.",
            "Unlink on the Account page deletes the gateway's copy at once. So do Delete my data "
            "and an admin's Unlink or Disable.",
            "If you are removed from this gateway's group, or your account at the sign-in service "
            "is deactivated or deleted, you lose access to the gateway at once. Your stored "
            "session is usually deleted within about an hour if the person who runs the gateway "
            "turned on its hourly clean-up (it needs an extra setting at the sign-in service), "
            "later while it cannot get a clear answer from the sign-in service. Without it, "
            "the session is deleted when an admin presses Disable or Unlink, when it expires, or "
            "(after a removal from the group) the next time you or one of your apps tries to use "
            "the gateway.",
            "Unlinking deletes only the gateway's copy. Whether the session also stops working on "
            "Archidekt's side is not known, so a copy someone already took may keep working until "
            "it expires. Whether changing your Archidekt password ends it is not known either.",
        ),
    ),
)

# Archidekt's own position on apps like this one: shown with the disclosure, same tick.
TERMS_NOTE = (
    "Archidekt has no official way for other apps to read or change decks, so this gateway uses "
    "the same requests archidekt.com's own pages use. Archidekt's terms of service restrict "
    "automated access, so Archidekt could limit or block an account used this way."
)

ACKNOWLEDGE = (
    "I have read what linking gives this gateway and the person who runs it, and I want to link my account."
)


SWEEP_ON = "On this gateway the hourly clean-up is on."
SWEEP_OFF = "On this gateway the hourly clean-up is off."


def body_html(sweep_on: bool | None = None) -> str:
    """The sections as HTML (headings and lists), without a wrapper. ``sweep_on`` adds whether
    this gateway runs the removed-member clean-up (idp_sweep.py) to the last section."""
    out = []
    for i, (heading, lines) in enumerate(SECTIONS):
        items = [html.escape(line) for line in lines]
        if sweep_on is not None and i == len(SECTIONS) - 1:
            items.append(html.escape(SWEEP_ON if sweep_on else SWEEP_OFF))
        out.append(f"<h3>{html.escape(heading)}</h3><ul>{''.join(f'<li>{x}</li>' for x in items)}</ul>")
    return "".join(out)


def form_html(sweep_on: bool | None = None) -> str:
    """Above the link form: everything, open, plus Archidekt's terms."""
    return (
        "<div class='notice disclosure' id='archidekt-disclosure'>"
        f"<p><strong>Before you link.</strong> {html.escape(LEAD)}</p>"
        f"{body_html(sweep_on)}"
        f"<h3>Archidekt's terms</h3><p>{html.escape(TERMS_NOTE)}</p></div>"
    )


def linked_html(sweep_on: bool | None = None) -> str:
    """On the Account page once linked: the same text, folded, so it stays readable later."""
    return (
        "<details class='disclosure' id='archidekt-disclosure'>"
        "<summary>What linking gives this gateway and the person who runs it</summary>"
        f"{body_html(sweep_on)}"
        f"<h3>Archidekt's terms</h3><p>{html.escape(TERMS_NOTE)}</p></details>"
    )


def plain_text() -> str:
    """The disclosure as plain text (for docs and review)."""
    parts = [LEAD, ""]
    for heading, lines in SECTIONS:
        parts.append(heading)
        parts.extend(f"- {line}" for line in lines)
        parts.append("")
    parts += ["Archidekt's terms", TERMS_NOTE, "", f"Tick box: {ACKNOWLEDGE}"]
    return "\n".join(parts)
