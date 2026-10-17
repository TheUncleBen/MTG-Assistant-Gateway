"""The admin Settings page (0.7.29): every setting in config.MANAGED_SETTINGS, grouped, with where
its value comes from. A value set in the deployment wins and is shown read-only (Q17, R-172);
the others are stored through LiveSettings, which checks bounds and writes the audit log.

All text here is draft until Ben's wording card for the page (WORDING-AND-CALLS-2).
"""

from __future__ import annotations

import html
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .config import MANAGED_SETTINGS, STACK_LOCK_HOW, LiveSettings, SettingError, SettingSpec, _number
from .modes import MODE_LABELS
from .plugin_page import CHATGPT_NAME_MAX, chatgpt_name_too_long

# (group, title, intro). The order is the page's; "scan" starts folded away.
GROUPS: tuple[tuple[str, str, str], ...] = (
    ("approvals", "Approvals", "How members' assistants may apply changes, and how long a change waits."),
    ("sync", "Deck sync", "How often the gateway checks Archidekt for decks changed elsewhere."),
    (
        "backups",
        "Backups",
        "Copies kept before every edit, and the nightly backup of the gateway's database.",
    ),
    ("simulation", "Simulation", "Forge games for deck reports."),
    ("watchlists", "Watchlists and prices", "Watched cards and decks, and the daily price check."),
    ("general", "General", "The gateway's name and housekeeping."),
    (
        "pacing",
        "Archidekt and Scryfall pacing",
        "Here these can only be set to ask Archidekt and Scryfall less often than the gateway's "
        "defaults, to stay gentle on their servers. Only your stack can make them ask more often.",
    ),
    (
        "scan",
        "Card scanning (advanced)",
        "Thresholds for recognising scanned cards. The defaults suit most cameras.",
    ),
)
FOLDED = frozenset({"scan"})

# key -> (label, one-line help)
TEXT: dict[str, tuple[str, str]] = {
    "approval_mode_default": (
        "Approval mode for new members",
        "Used until a member picks their own mode on their Account page.",
    ),
    "approval_mode_max": (
        "Highest approval mode allowed",
        "Members can't pick a mode above this one.",
    ),
    "auto_apply_max_rows": (
        "Rows a small edit may have",
        # the Semi-auto mode's own label, so the help always names the choice as the page shows it
        f"With \u201c{MODE_LABELS['semi']}\u201d, an edit with at most this many card rows counts as small.",
    ),
    "auto_big_edit_rows": (
        "Rows before an edit counts as big",
        f"With \u201c{MODE_LABELS['auto']}\u201d, an edit with more card rows than this waits for the "
        "member.",
    ),
    "proposal_expiry_days": (
        "Days a change waits for approval",
        "A change nobody approves in this time expires.",
    ),
    "apply_in_chat": (
        "Approve buttons in the chat",
        "Claude and ChatGPT show each change as a card with Approve and Reject buttons.",
    ),
    "deck_sync_minutes": (
        "Check Archidekt for deck changes every",
        "For members with changes waiting. 0 turns the background check off.",
    ),
    "archidekt_backups": (
        "Keep a backup copy on Archidekt",
        "Before an edit is applied, the deck is copied into the member's private backup folder on Archidekt.",
    ),
    "archidekt_backup_copies": (
        "Backup copies kept per deck",
        "Older copies are deleted from the backup folder on Archidekt, one at a time, once that deck gets "
        "its next new backup copy. A copy the member changed or moved, or one the gateway couldn't "
        "delete, is kept and no longer counts toward this number. Copies made before 0.7.29 are never "
        "deleted and don't count toward it either.",
    ),
    "archidekt_backup_box_default": (
        "Backup box ticked by default",
        "Whether the deck editor's “Also keep a backup copy on Archidekt” box starts ticked.",
    ),
    "snapshots_per_deck": (
        "Restore points kept per deck",
        "Kept on the gateway, newest first, for History > Restore. Restore points that record a backup "
        "copy the gateway may still delete are kept too.",
    ),
    "backup_hour_utc": ("Nightly backup hour (UTC)", "When the gateway backs up its own database, 0 to 23."),
    "backup_keep_days": ("Days of nightly backups kept", "Older backup files are deleted."),
    "forge_games": ("Forge games per report", "More games give steadier numbers and take longer."),
    "forge_parallel_games": ("Forge games played at once", "Higher is faster but uses more memory and CPU."),
    "watchlists_enabled": (
        "Watchlist and price features",
        "Shows the Watchlist page, the price buttons and the watch tools.",
    ),
    "watch_max_cards": ("Cards one member may watch", "0 turns card watching off."),
    "watch_max_decks": (
        "Decks one member may watch",
        "Each watched deck is read from Archidekt once a day. 0 turns deck watching off.",
    ),
    "price_history_days": (
        "Days of prices kept",
        "Daily prices and deck values older than this are deleted.",
    ),
    "price_hour_utc": (
        "Daily price check hour (UTC)",
        "When prices are checked and summaries are sent, 0 to 23.",
    ),
    "server_name": ("Gateway name", "Shown in the top bar, on sign-in and in Claude's connector list."),
    "chatgpt_name": (
        "Name in ChatGPT",
        "Leave it blank to use the gateway name. ChatGPT refuses a plugin file whose name is over 80 "
        "characters.",
    ),
    "update_check": (
        "Check for new versions",
        "Twice a day, one anonymous request to GitHub. Admins see a notice when a new version is out.",
    ),
    "log_level": ("Log detail", "How much the gateway writes to its log. INFO suits most servers."),
    "archidekt_calls_per_10_min": (
        "Archidekt work per member per 10 minutes",
        "Deck reads, changes and applies one member may start.",
    ),
    "archidekt_min_interval": ("Time between Archidekt requests", "For everyone together."),
    "archidekt_max_per_minute": ("Archidekt requests per minute", "For everyone together."),
    "archidekt_retries": ("Retries of a failed Archidekt read", "Writes are never retried."),
    "archidekt_backoff_base": ("Wait before a retry", "Doubles with each retry, up to 10 seconds."),
    "archidekt_cache_seconds": ("Reuse public deck reads for", "Cleared by any change the gateway sends."),
    "archidekt_card_cache_seconds": (
        "Reuse card lookups for",
        "Card details read from Archidekt are reused for this long.",
    ),
    "scryfall_lookup_interval": ("Time between Scryfall lookups", "For card scanning."),
    "scan_fuzzy_min_similarity": ("Lowest name similarity accepted", "From 0.3 to 1."),
    "scan_fuzzy_confident_similarity": (
        "Name similarity accepted at once",
        "Accepted without a second lookup.",
    ),
    "scan_ambiguity_margin": ("Closest two names may be", "Closer than this, the result is unsure."),
    "scan_auto_add_confidence": (
        "Reading confidence to add without a tap",
        "Continuous scanning only, 0 to 100.",
    ),
    "scan_foil_star_ink_ratio": ("Foil star threshold", "For spotting the foil star on the collector line."),
    "scan_glare_ratio": ("Glare threshold", "Share of blown-out title pixels that shows the glare hint."),
    "scan_min_ocr_confidence": (
        "Lowest reading confidence looked up",
        "Continuous scanning only. Below this, the title goes to the unidentified list.",
    ),
    "scan_art_enabled": ("Artwork check", "Matches the card's art to pick the right printing."),
    "scan_art_max_distance": ("Largest artwork difference", "Out of 1024. Lower is stricter."),
    "scan_art_min_margin": ("Artwork lead over the next match", "Raise it for fewer wrong picks."),
    "scan_art_max_printings": ("Printings learned per card", "The newest ones first."),
    "scan_art_image_interval": ("Time between image downloads", "From Scryfall's image server."),
    "scan_art_images_per_hour": ("Image downloads per hour", "For everyone together."),
    "scan_art_max_galleries": (
        "Cards whose printings are kept",
        "The artwork check keeps what it learned for this many of the most recently scanned cards.",
    ),
    "scan_art_gallery_idle_days": ("Forget a card's printings after", "Days without a scan of that card."),
}

# Changes that delete or stop keeping something need a tick on the page (QUESTIONS-2 B5, card
# Q135): key -> (what needs it: "off" or "lower", the tick box's text).
CONFIRM: dict[str, tuple[str, str]] = {
    "archidekt_backups": (
        "off",
        "I understand that with this off, edits have no copy on Archidekt to go back to. Restore points "
        "on the gateway are still kept.",
    ),
    "archidekt_backup_copies": (
        "lower",
        "I understand that each deck's copies past the new number are deleted from the backup folder on "
        "Archidekt, one at a time, once that deck gets its next new backup copy. Copies a member changed "
        "or moved are kept and no longer count. Copies made before 0.7.29 are never deleted and don't "
        "count toward the number.",
    ),
    "snapshots_per_deck": (
        "lower",
        "I understand that restore points past the new number are deleted at the next hourly clean-up.",
    ),
}


def needs_confirm(key: str, before: Any, after: Any) -> bool:
    rule = CONFIRM.get(key)
    if rule is None:
        return False
    if rule[0] == "off":
        return bool(before) and not after
    return after < before


SETTINGS_CSS = """
.set-nav{display:flex;flex-wrap:wrap;gap:.4rem;margin:.75rem 0 1rem}
.set-nav a{display:inline-flex;align-items:center;min-height:var(--ctl);padding:0 .8rem;
  border:1px solid var(--border);
  border-radius:999px;color:var(--text);text-decoration:none;background:var(--surface);font-size:.95rem}
.set-nav a:hover{background:var(--surface-2)}
.set-nav a:focus-visible{outline:2px solid var(--focus);outline-offset:2px}
.set-stack{margin:.75rem 0 0;padding:.75rem 1rem;border:1px solid var(--border);
  border-radius:var(--radius-lg);background:var(--surface)}
.set-stack p{margin:0}
.set-stack-list{display:flex;flex-wrap:wrap;gap:.4rem;list-style:none;margin:.6rem 0 0;padding:0}
.set-stack-list a{display:inline-block;box-sizing:border-box;min-height:var(--ctl);padding:.45rem .7rem;
  border:1px solid var(--border);border-radius:var(--radius-lg);color:var(--text);text-decoration:none;
  background:var(--surface-2);max-width:100%}
.set-stack-list a:hover{border-color:var(--text)}
.set-stack-list a:focus-visible{outline:2px solid var(--focus);outline-offset:2px}
.set-stack-list code{background:none;padding:0}
@media (pointer:coarse){ .set-stack-list a{min-height:44px} }
.set-group{scroll-margin-top:4.5rem}
.set-group h2{margin:0 0 .25rem;font-size:1.2rem}
.set-group > .intro,.set-group details > .intro{margin:0 0 .75rem}
.set-group details > summary{cursor:pointer;font-weight:700;font-size:1.2rem;min-height:2.5rem;
  display:flex;align-items:center}
.set-row{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,20rem);gap:.5rem 1.5rem;padding:.9rem 0;
  border-top:1px solid var(--border-soft);scroll-margin-top:4.5rem}
.set-row:first-of-type{border-top:0}
.set-label{font-weight:700;margin:0;display:block}
.set-help,.set-src{margin:.2rem 0 0}
.set-err{margin:.35rem 0 0;color:var(--danger-text);font-weight:700}
.set-warn{margin:.35rem 0 0;padding:.4rem .6rem;border-left:4px solid var(--orange);
  background:var(--orange-tint);color:var(--orange-text);border-radius:var(--radius)}
.set-row.bad input:not([type=checkbox]):not([type=radio]),.set-row.bad select{border-color:var(--danger-text)}
.set-ctl{display:flex;flex-direction:column;gap:.45rem;align-items:stretch;min-width:0}
.set-num{display:flex;align-items:center;gap:.5rem}
.set-num input{flex:1 1 auto;min-width:0}
.set-num .unit{color:var(--text-muted);white-space:nowrap}
.set-ctl .range{margin:0}
.set-seg{display:inline-flex;border:1px solid var(--control-border);border-radius:var(--radius);
  overflow:hidden;
  align-self:flex-start;margin:0;padding:0;min-width:0}
.set-seg legend{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.set-seg label{position:relative;display:inline-flex;align-items:center;justify-content:center;
  min-width:4.5rem;
  min-height:var(--ctl);padding:0 1rem;margin:0;cursor:pointer;font-weight:400;background:var(--surface)}
.set-seg label + label{border-left:1px solid var(--control-border)}
.set-seg input{position:absolute;opacity:0;inset:0;margin:0;width:100%;height:100%;cursor:pointer}
.set-seg label:has(input:checked){background:var(--orange-tint);font-weight:700}
.set-seg label:has(input:focus-visible){outline:2px solid var(--focus);outline-offset:-2px}
.set-seg label:has(input:disabled){cursor:default;color:var(--text-muted)}
.set-ctl .reset{align-self:flex-start;min-height:var(--ctl)}
.set-choices{display:flex;flex-direction:column;border:1px solid var(--control-border);
  border-radius:var(--radius);overflow:hidden;margin:0;padding:0;min-width:0}
.set-choices legend{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);
  white-space:nowrap}
.set-choices label{position:relative;display:flex;align-items:center;gap:.6rem;min-height:var(--ctl);
  padding:.45rem .8rem;margin:0;cursor:pointer;font-weight:400;background:var(--surface);
  overflow-wrap:anywhere}
.set-choices label + label{border-top:1px solid var(--control-border)}
.set-choices input{flex:none;margin:0;width:1.1rem;height:1.1rem;accent-color:var(--orange)}
.set-choices label:has(input:checked){background:var(--orange-tint);font-weight:700}
.set-choices label:has(input:focus-visible){outline:2px solid var(--focus);outline-offset:-2px}
.set-choices label:has(input:disabled){cursor:default;color:var(--text-muted)}
.set-lock code{overflow-wrap:break-word}
.set-confirm{grid-column:1 / -1}
/* Set in the stack: shown where it would be changed, greyed out, its value still readable (4.5:1) */
/* the radios of On/Off and the approval modes are left out: they stay hidden or plain, and their
   labels carry the value */
.set-row.locked input:disabled:not([type=radio]):not([type=checkbox]),.set-row.locked select:disabled,
.set-row.locked .msel-btn:disabled{
  background:var(--surface-2);color:var(--text-muted);-webkit-text-fill-color:var(--text-muted);
  border-style:dashed;opacity:1;cursor:not-allowed}
.set-row.locked .set-seg,.set-row.locked .set-choices{border-style:dashed}
.set-row.locked input[type=radio]{accent-color:var(--text-muted)}
.set-row.locked .set-seg label,.set-row.locked .set-choices label{background:var(--surface-2);
  cursor:not-allowed}
.set-row.locked .set-seg label:has(input:checked),.set-row.locked .set-choices label:has(input:checked){
  background:var(--surface-2);box-shadow:inset 0 0 0 2px var(--text-muted)}
.set-save{display:flex;flex-wrap:wrap;gap:.75rem;align-items:center;padding-top:.9rem;
  border-top:1px solid var(--border-soft)}
.set-save button{min-height:var(--ctl)}
.set-badge{display:inline-block;margin-left:.4rem;vertical-align:.1em}
p.set-saved{margin:0} ul.set-saved{margin:.35rem 0 0;padding-left:1.2rem}
@media (max-width:720px){
  .set-row{grid-template-columns:minmax(0,1fr)}
  .set-ctl{max-width:none}
}
"""


@dataclass
class Outcome:
    """What a Save or Back to default did: changes (key, before, after), errors by key, and the
    values the form sent (kept on the page when a field is refused)."""

    changes: list[tuple[str, Any, Any]] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    posted: dict[str, str] = field(default_factory=dict)
    # Back to default: the section's other fields the admin had changed but not saved (kept in
    # the form and named in a notice, never dropped without a word; review round 1, finding 6).
    unsaved: list[str] = field(default_factory=list)


def group_keys(group: str) -> list[str]:
    return [k for k, spec in MANAGED_SETTINGS.items() if spec.group == group]


def label(key: str) -> str:
    return TEXT.get(key, (key, ""))[0]


# The summary box's link text where the row's name only reads right beside its value box ("Check
# Archidekt for deck changes every [30] minutes"). A link has no value after it, so these five say
# the whole thing (SUMMARY-LINK-TEXTS-r3.md option a, approved 10-17 about 03:21Z, R-216). The rows, the
# save notices and the Activity log keep the row's name.
LINK_TEXT = {
    "deck_sync_minutes": "How often to check Archidekt for deck changes",
    "archidekt_cache_seconds": "How long public deck reads are reused",
    "archidekt_card_cache_seconds": "How long Archidekt card lookups are reused",
    "scan_ambiguity_margin": "Name similarity lead over the next match",
    "scan_art_gallery_idle_days": "Days without a scan before a card's printings are forgotten",
}


def link_text(key: str) -> str:
    """A setting's name in the summary box: its row's name, or the whole wording in LINK_TEXT."""
    return LINK_TEXT.get(key) or label(key)


def shown(spec: SettingSpec, value: Any) -> str:
    """A value as the page writes it (plain text, not escaped)."""
    if spec.kind == "bool":
        return "On" if value else "Off"
    if spec.kind == "choice":
        if spec.labels and value in spec.choices:
            return spec.labels[spec.choices.index(value)]
        return str(value)
    if spec.kind == "text":
        return f"“{value}”" if value else "blank"
    if spec.key == "deck_sync_minutes" and value == 0:
        return "0 (off)"
    text = _number(value)
    if not spec.unit:
        return text
    # "1 second", "1 day": every unit is a plain plural ("seconds", "minutes", "days")
    return f"{text} {spec.unit[:-1] if value == 1 else spec.unit}"


def apply_form(live: LiveSettings, data: Mapping[str, str], *, by: str) -> Outcome:
    """Save one section of the form (``group``), or put one setting back to its default (``reset``).

    Every field of the section is checked first; nothing is stored unless all of them pass, so a
    refused field never leaves the section half saved. Fields set by the deployment are ignored."""
    out = Outcome()
    reset = data.get("reset")
    if reset:
        spec = MANAGED_SETTINGS.get(reset)
        if spec is None:
            out.errors["_"] = "That setting isn't one this page can change."
            return out
        _keep_unsaved(live, data, spec.group, reset, out)
        if live.locked(reset):
            out.errors[reset] = f"Set in your stack ({spec.env}), {STACK_LOCK_HOW}"
            return out
        before = live.get(reset)
        if needs_confirm(reset, before, spec.default) and data.get(f"confirm_{reset}") != "yes":
            what = "turning this off" if CONFIRM[reset][0] == "off" else "lowering this"
            out.errors[reset] = f"Tick the box below to confirm {what}."
            return out
        live.reset(reset, by=by)
        after = live.get(reset)
        if after != before:
            out.changes.append((reset, before, after))
        return out
    group = data.get("group", "")
    pending: list[tuple[str, Any, Any]] = []
    for key in group_keys(group):
        spec = MANAGED_SETTINGS[key]
        raw = data.get(f"v_{key}")
        if raw is None or live.locked(key):
            continue
        out.posted[key] = raw
        try:
            value = spec.parse(raw)
        except SettingError as exc:
            out.errors[key] = f"{exc.message[:1].upper()}{exc.message[1:]}."
            continue
        before = live.get(key)
        if value != before:
            if needs_confirm(key, before, value) and data.get(f"confirm_{key}") != "yes":
                what = "turning this off" if CONFIRM[key][0] == "off" else "lowering this"
                out.errors[key] = f"Tick the box below to confirm {what}."
                continue
            pending.append((key, before, value))
    if out.errors:
        return out
    for key, before, value in pending:
        live.set(key, value, by=by)
        out.changes.append((key, before, value))
    return out


def _keep_unsaved(live: LiveSettings, data: Mapping[str, str], group: str, reset: str, out: Outcome) -> None:
    """The fields of ``group`` (besides ``reset``) the form sent with a value other than the saved
    one: kept as typed, and listed in ``out.unsaved``."""
    for key in group_keys(group):
        raw = data.get(f"v_{key}")
        if key == reset or raw is None or live.locked(key):
            continue
        try:
            same = MANAGED_SETTINGS[key].parse(raw) == live.get(key)
        except SettingError:
            same = False
        if not same:
            out.posted[key] = raw
            out.unsaved.append(key)


def unsaved_notice(keys: list[str], group: str) -> str:
    """Said after Back to default when other fields of the section were changed but not saved."""
    if not keys:
        return ""
    title = next((t for g, t, _i in GROUPS if g == group), "")
    names = ", ".join(f"“{label(k)}”" for k in keys)
    text = f"Not saved yet: {names}. What you typed is still in the form; press “Save {title}” to keep it."
    return f"<div class='notice warn' role='status'>{html.escape(text)}</div>"


def settings_body(
    live: LiveSettings,
    running: Any,
    *,
    csrf: str,
    who: Callable[[str | None], str],
    when: Callable[[int | None], str],
    outcome: Outcome | None = None,
    notice: str = "",
    focus: str | None = None,
) -> str:
    """The page body. ``running`` is the Settings the gateway started with (a setting that applies
    after a restart may differ from its saved value); ``who`` names an account, ``when`` writes a
    time (both return HTML)."""
    outcome = outcome or Outcome()
    rows = {d["key"]: d for d in live.describe()}
    warns = {"chatgpt_name": chatgpt_name_warning(live)}
    nav = "".join(
        f"<a href='#g-{g}'>{html.escape(title)}</a>" for g, title, _intro in GROUPS if group_keys(g)
    )
    parts = [
        "<p class='muted'>Settings for the whole gateway. A setting your stack sets is shown greyed out "
        "with its value, and can only be changed in the stack.</p>",
        _stack_summary(live),
        notice,
        f"<nav class='set-nav' aria-label='Setting groups'>{nav}</nav>",
    ]
    for group, title, intro in GROUPS:
        keys = group_keys(group)
        if not keys:
            continue
        fields = "".join(_row(rows[k], running, outcome, who, when, warns.get(k, "")) for k in keys)
        editable = any(not live.locked(k) for k in keys)
        save = (
            f"<div class='set-save'><button type='submit' class='primary'>Save {html.escape(title)}"
            "</button></div>"
            if editable
            else "<p class='muted small set-save'>Every setting here is set in your stack.</p>"
        )
        form = (
            f"<form method='post' action='/admin/settings#g-{group}' novalidate>"
            f"<input type='hidden' name='csrf' value='{html.escape(csrf)}'>"
            f"<input type='hidden' name='group' value='{group}'>{fields}{save}</form>"
        )
        intro_html = f"<p class='muted intro'>{html.escape(intro)}</p>"
        if group in FOLDED:
            is_open = " open" if (focus == group or any(k in outcome.errors for k in keys)) else ""
            inner = f"<details{is_open}><summary>{html.escape(title)}</summary>{intro_html}{form}</details>"
        else:
            inner = f"<h2>{html.escape(title)}</h2>{intro_html}{form}"
        parts.append(
            f"<section class='card set-group' id='g-{group}' aria-label='{html.escape(title)}'>"
            f"{inner}</section>"
        )
    return "".join(parts)


def _stack_summary(live: LiveSettings) -> str:
    """Card Q139 item 3: one line at the top naming every setting the stack sets, by its name on
    the page (for five, the whole wording in LINK_TEXT) and its variable, each a link to its row
    (a folded group opens), so the operator can see which lines to remove from the stack to manage
    those settings here. Nothing when the stack sets none."""
    locked = [spec for spec in MANAGED_SETTINGS.values() if live.locked(spec.key)]
    if not locked:
        return ""
    links = "".join(
        f"<li><a href='{f'?open={spec.group}' if spec.group in FOLDED else ''}#s-row-{spec.key}'>"
        f"{html.escape(link_text(spec.key))} (<code>{html.escape(spec.env).replace('_', '_<wbr>')}</code>)"
        "</a></li>"
        for spec in locked
    )
    n = len(locked)
    one = n == 1
    return (
        "<div class='set-stack' role='note'>"
        f"<p>Your stack sets {n} of the settings on this page, so {'it' if one else 'they'} can only be "
        f"changed there. To manage {'it' if one else 'one'} here instead, remove its variable from your "
        "stack, then redeploy.</p>"
        f"<ul class='set-stack-list' role='list' aria-label='Settings your stack sets'>{links}</ul></div>"
    )


def chatgpt_name_warning(live: LiveSettings) -> str:
    """The warning under "Name in ChatGPT" when the name ChatGPT gets (this setting, else the gateway
    name) is over the plugin file's limit, as the start-up log and the admin page warn (C4)."""
    long = chatgpt_name_too_long(live.get("server_name"), live.get("chatgpt_name") or None)
    if not long:
        return ""
    fix = (
        f"change {MANAGED_SETTINGS['chatgpt_name'].env} in your stack to a name of {CHATGPT_NAME_MAX} "
        "characters or fewer, then redeploy"
        if live.locked("chatgpt_name")
        else f"set a name of {CHATGPT_NAME_MAX} characters or fewer here"
    )
    return (
        f"ChatGPT gets the name “{long[0]}”, which is {long[1]} characters. ChatGPT refuses a plugin file "
        f"whose name is over {CHATGPT_NAME_MAX} characters, so {fix}."
    )


def _row(
    d: dict[str, Any], running: Any, outcome: Outcome, who: Callable, when: Callable, warn: str = ""
) -> str:
    key = d["key"]
    spec = MANAGED_SETTINGS[key]
    name, help_text = TEXT.get(key, (key, ""))
    locked = d["source"] == "deployment"
    err = outcome.errors.get(key)
    value = outcome.posted.get(key, d["value"])
    help_id = f"h-{key}"
    described = f"{help_id} s-{key}" + (f" w-{key}" if warn else "") + (f" e-{key}" if err else "")
    control = _control(spec, value, locked=locked, described=described)
    src = _source(d, spec, running, who, when)
    reset = ""
    if d["source"] == "dashboard":
        reset = (
            f"<button type='submit' class='btn reset' name='reset' value='{key}' formnovalidate>"
            f"Back to default ({html.escape(shown(spec, spec.default))})</button>"
        )
    badge = "<span class='badge set-badge'>After a restart</span>" if spec.restart else ""
    title = (
        f"<span class='set-label' id='l-{key}'>{html.escape(name)}{badge}</span>"
        if spec.kind == "bool" or (spec.kind == "choice" and spec.labels)
        else f"<label class='set-label' for='f-{key}'>{html.escape(name)}{badge}</label>"
    )
    confirm = ""
    if key in CONFIRM and not locked and (CONFIRM[key][0] == "lower" or d["value"]):
        confirm = (
            f"<label class='set-confirm small'><input type='checkbox' name='confirm_{key}' value='yes'>"
            f"{html.escape(CONFIRM[key][1])}</label>"
        )
    return (
        f"<div class='set-row{' bad' if err else ''}{' locked' if locked else ''}' id='s-row-{key}'>"
        f"<div class='set-text'>{title}"
        + (
            f"<p class='set-help muted small' id='{help_id}'>{html.escape(help_text)}</p>"
            if help_text
            else f"<span id='{help_id}' hidden></span>"
        )
        + f"<p class='set-src small' id='s-{key}'>{src}</p>"
        + (f"<p class='set-warn small' id='w-{key}'>{html.escape(warn)}</p>" if warn else "")
        + (f"<p class='set-err small' id='e-{key}' role='alert'>{html.escape(err)}</p>" if err else "")
        + f"</div><div class='set-ctl'>{control}{reset}</div>{confirm}</div>"
    )


def _source(d: dict[str, Any], spec: SettingSpec, running: Any, who: Callable, when: Callable) -> str:
    if d["source"] == "deployment":
        return (
            "<span class='set-lock'>Set in your stack "
            # it may break only after an underscore, never mid-word (review round 2, finding 4)
            f"(<code>{html.escape(spec.env).replace('_', '_<wbr>')}</code>), {STACK_LOCK_HOW}</span>"
        )
    if d["source"] == "dashboard":
        changed = d.get("changed") or {}
        text = f"Changed here by {who(changed.get('updated_by'))}, {when(changed.get('updated_at'))}."
    else:
        text = "Default."
    now = getattr(running, spec.key, d["value"])
    if spec.key == "chatgpt_name":
        now = now or ""
    if spec.restart and now != d["value"]:
        text += f" The gateway uses {html.escape(shown(spec, now))} until it restarts."
    return text


def _control(spec: SettingSpec, value: Any, *, locked: bool, described: str) -> str:
    key = spec.key
    dis = " disabled" if locked else ""
    name = f"v_{key}"
    if spec.kind == "bool":
        on = value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes", "on")
        return (
            f"<fieldset class='set-seg' aria-labelledby='l-{key}' aria-describedby='{described}'>"
            f"<legend>{html.escape(label(key))}</legend>"
            f"<label><input type='radio' name='{name}' value='on'{' checked' if on else ''}{dis}>On</label>"
            f"<label><input type='radio' name='{name}' value='off'{'' if on else ' checked'}{dis}>Off</label>"
            "</fieldset>"
        )
    if spec.kind == "choice" and spec.labels:
        # Named choices (the approval modes) as a list of radio buttons, so the whole name shows
        # and wraps at any width or text size (review round 1, finding 9).
        picked = next((c for c in spec.choices if str(value).lower() == c.lower()), None)
        opts = "".join(
            f"<label><input type='radio' name='{name}' value='{html.escape(c)}'"
            f"{' checked' if c == picked else ''}{dis}>{html.escape(n)}</label>"
            for c, n in zip(spec.choices, spec.labels, strict=True)
        )
        return (
            f"<fieldset class='set-choices' id='f-{key}' aria-labelledby='l-{key}' "
            f"aria-describedby='{described}'><legend>{html.escape(label(key))}</legend>{opts}</fieldset>"
        )
    if spec.kind == "choice":
        names = spec.labels or spec.choices
        opts = "".join(
            f"<option value='{html.escape(c)}'{' selected' if str(value).lower() == c.lower() else ''}>"
            f"{html.escape(n)}</option>"
            for c, n in zip(spec.choices, names, strict=True)
        )
        return f"<select id='f-{key}' name='{name}' aria-describedby='{described}'{dis}>{opts}</select>"
    if spec.kind == "text":
        return (
            f"<input type='text' id='f-{key}' name='{name}' value='{html.escape(str(value or ''))}' "
            f"maxlength='{spec.max_len}' autocomplete='off' aria-describedby='{described}'{dis}>"
        )
    step = "1" if spec.kind == "int" else "any"
    shown_value = value if isinstance(value, str) else (_plain(value))
    lo = "0" if spec.zero_ok else _plain(spec.lo)
    span = (
        f"0 (off), or {_number(spec.lo)} to {_number(spec.hi)}"
        if spec.zero_ok
        else f"{_number(spec.lo)} to {_number(spec.hi)}"
    )
    unit = f"<span class='unit'>{html.escape(spec.unit)}</span>" if spec.unit else ""
    return (
        f"<div class='set-num'><input type='number' id='f-{key}' name='{name}' "
        f"value='{html.escape(shown_value)}' min='{lo}' max='{_plain(spec.hi)}' step='{step}' "
        f"inputmode='{'numeric' if spec.kind == 'int' else 'decimal'}' "
        f"aria-describedby='{described} r-{key}'{dis}>{unit}</div>"
        f"<p class='muted small range' id='r-{key}'>Allowed: {html.escape(span)}</p>"
    )


def _plain(value: Any) -> str:
    if value is None:
        return ""
    return str(int(value)) if float(value).is_integer() else str(value)


def change_line(detail: Any) -> str | None:
    """A ``setting_changed`` audit entry in words, as the save notice writes it ("Days a change waits
    for approval: 7 days → 14 days"); None when it isn't one this page knows. Plain text."""
    if not isinstance(detail, dict) or detail.get("key") not in MANAGED_SETTINGS:
        return None
    spec = MANAGED_SETTINGS[detail["key"]]
    return f"{label(spec.key)}: {shown(spec, detail.get('previous'))} → {shown(spec, detail.get('value'))}"


def changes_notice(changes: list[tuple[str, Any, Any]]) -> str:
    """The notice after a save: what changed, from and to."""
    if not changes:
        return "<div class='notice ok' role='status'>Nothing to save: no setting was changed.</div>"
    items = []
    for key, before, after in changes:
        spec = MANAGED_SETTINGS[key]
        later = " (after a restart)" if spec.restart else ""
        items.append(
            f"<li><b>{html.escape(label(key))}</b>: {html.escape(shown(spec, before))} "
            f"→ {html.escape(shown(spec, after))}{later}</li>"
        )
    return (
        "<div class='notice ok' role='status'><p class='b set-saved'>Saved.</p>"
        f"<ul class='set-saved'>{''.join(items)}</ul></div>"
    )
