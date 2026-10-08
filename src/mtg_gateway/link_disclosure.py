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
  date shown on the Account page is read from the member's own stored token (``link_expiry``);
- admins: ``admin.py`` shows the Archidekt username, the activity log and the unlink, disable,
  revoke and delete actions, and calls nothing that uses a member's session;
- logs: no logger is given the password or a token (``tests/test_link_disclosure.py`` checks the
  DEBUG output of a link, refresh, use and unlink); httpx's request lines (Archidekt URLs) are
  logged at DEBUG only (``__main__.py``);
- backups: ``Database.backup_to`` blanks every stored session before the copy is written;
- ending it: unlink, delete my data, admin unlink or disable blank the stored session at once;
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
            "gets when you sign in there. Archidekt offers no limited version, so the session can "
            "do whatever your Archidekt sign-in can do.",
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
            "Stored as plain text: your Archidekt username and user number, when you linked, and "
            "when the link was last used.",
            "Never stored: your Archidekt password.",
        ),
    ),
    (
        "What the person who runs this server can see and do",
        (
            "The session is encrypted, but the key that opens it is on the same server. Anyone "
            "with access to the server and that key can open the session and use it to act as you "
            "on Archidekt until it expires: read, change or delete your decks and collection, or "
            "anything else your Archidekt sign-in allows. They cannot get your password from it.",
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
            "Admins use the admin pages, not the server itself. They see your name, email, "
            "groups, your Archidekt username, and the activity log: what happened and when (a "
            "link, an unlink, a proposal made or applied, with deck and proposal numbers) and "
            "which app did it.",
            "They can unlink your Archidekt account, disable your account, sign you out "
            "everywhere and delete your data.",
            "They cannot see your password or your session, cannot open your proposals, and have "
            "no button that uses your link to read or change your decks. An admin who also has "
            "access to the server can do everything in the section above.",
        ),
    ),
    (
        "Logs and backups",
        (
            "The gateway never writes your password or your session to its logs.",
            "At the usual log level it writes no Archidekt addresses. If the person who runs it "
            "turns on debug logging, the logs list the Archidekt addresses it calls, which "
            "include deck numbers and usernames.",
            "Backups leave your session out. They keep everything else listed above.",
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
            "If you are removed from this gateway's group, you lose access to the gateway at "
            "once, but the stored session is only deleted the next time you or one of your apps "
            "tries to use the gateway, when an admin presses Disable or Unlink, or when it "
            "expires.",
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


def body_html() -> str:
    """The sections as HTML (headings and lists), without a wrapper."""
    out = []
    for heading, lines in SECTIONS:
        items = "".join(f"<li>{html.escape(line)}</li>" for line in lines)
        out.append(f"<h3>{html.escape(heading)}</h3><ul>{items}</ul>")
    return "".join(out)


def form_html() -> str:
    """Above the link form: everything, open, plus Archidekt's terms."""
    return (
        "<div class='notice disclosure' id='archidekt-disclosure'>"
        f"<p><strong>Before you link.</strong> {html.escape(LEAD)}</p>"
        f"{body_html()}"
        f"<h3>Archidekt's terms</h3><p>{html.escape(TERMS_NOTE)}</p></div>"
    )


def linked_html() -> str:
    """On the Account page once linked: the same text, folded, so it stays readable later."""
    return (
        "<details class='disclosure' id='archidekt-disclosure'>"
        "<summary>What linking gives this gateway and the person who runs it</summary>"
        f"{body_html()}"
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
