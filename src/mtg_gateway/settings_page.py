"""The admin Settings page (0.7.29): every setting in config.MANAGED_SETTINGS, grouped, with where
its value comes from. A value set in the deployment wins and is shown read-only;
the others are stored through LiveSettings, which checks bounds and writes the audit log.

"""

from __future__ import annotations

import html
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from . import ui
from .config import MANAGED_SETTINGS, STACK_LOCK_HOW, LiveSettings, SettingError, SettingSpec, _number
from .modes import MODE_LABELS
from .theme import icon
from .ui import label as label_html

# (group, title, intro). The order is the page's; "scan" starts folded away.
GROUPS: tuple[tuple[str, str, str], ...] = (
    ("approvals", "Approvals", "How members' assistants may apply changes, and how long a change waits."),
    ("sync", "Deck sync", "How often the gateway checks Archidekt for decks changed elsewhere."),
    (
        "backups",
        "Backups",
        "Copies kept before every edit, and the nightly backup of the gateway's database.",
    ),
    ("simulation", "Tests", "Forge games for deck tests."),
    ("watchlists", "Watchlists and prices", "Watched cards and decks, and the daily price check."),
    ("general", "General", "The gateway's name and housekeeping."),
    (
        "signin",
        "Sign-in",
        "How long members stay signed in on the website and in the Android app, and how long an "
        "assistant's access lasts before it renews it. A change applies to the next sign-in.",
    ),
    (
        "pacing",
        "Archidekt and Scryfall pacing",
        "These can only be set to ask Archidekt and Scryfall less often than the gateway's "
        "defaults, to stay gentle on their servers.",
    ),
    (
        "scan",
        "Card scanning (advanced)",
        "Thresholds for recognising scanned cards. The defaults suit most cameras.",
    ),
)
FOLDED = frozenset({"scan"})
# the page's purpose line (admin.py draws it with the title, above the tab bar)
PURPOSE = (
    "Settings for the whole gateway. A setting your stack sets is shown greyed out with its value, "
    "and can only be changed in the stack."
)

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
        "Apply buttons in the chat",
        "Claude and ChatGPT show each change as a card with Apply and Reject buttons.",
    ),
    "browser_session_ttl": (
        "Website sign-in when unused",
        "A member who doesn't use the website or the Android app for this long signs in again. "
        "Each use starts the time again.",
    ),
    "browser_session_max_age": (
        "Longest website sign-in",
        "However much it is used, a website or Android app sign-in ends this long after it was made.",
    ),
    "access_token_ttl": (
        "Assistant access",
        "How long an assistant can use the gateway before it asks for new access. It does that by "
        "itself, without the member.",
    ),
    "refresh_token_ttl": (
        "Assistant refresh",
        "How long an assistant can keep asking for new access. After this, the member connects it again.",
    ),
    "deck_sync_minutes": (
        "Check Archidekt for deck changes every",
        "For members with changes waiting. 0 turns the background check off.",
    ),
    "archidekt_backups": (
        "Keep a backup copy on Archidekt",
        "Before an edit is applied, the deck is copied into the member's private backup folder on Archidekt "
        "(not for the deck page's own saves, Card search's Add to deck, or the deck editor's save with its "
        "box unticked). An edit within 15 minutes of the deck's last copy, or with the deck unchanged "
        "since, shares that copy instead of making another.",
    ),
    "archidekt_backup_folder": (
        "Backup folder on Archidekt",
        "Each member's own private folder on Archidekt, made when their first backup copy needs it. A new "
        "name applies to the next copy; copies already made stay where they are.",
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
        "Kept on the gateway, newest first, for History > Restore. Your own edits on the deck page count "
        "apart, with the same number, so they never push out the one from before an assistant's change. "
        "Restore points that record a backup copy the gateway may still delete are kept too.",
    ),
    "backup_hour_utc": ("Nightly backup hour (UTC)", "When the gateway backs up its own database, 0 to 23."),
    "backup_keep_days": (
        "Days of nightly backups kept",
        "Older backup files are deleted, and so are older copies saved before database upgrades.",
    ),
    "forge_games": (
        "Forge games per report",
        "How many games a new report plays in all. More games give steadier numbers and take longer. "
        "Play more games on a report adds this many again. "
        "\u201cForge games played at once\u201d only sets how many of them play side by side.",
    ),
    "forge_parallel_games": (
        "Forge games played at once",
        "How many of one report's games Forge plays side by side, so the report finishes sooner. "
        "It doesn't change how many games a report plays: that is \u201cForge games per report\u201d. "
        "Forge plays fewer at once when it has no room for more.",
    ),
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
    "archidekt_max_per_minute": (
        "Archidekt requests per minute",
        "For everyone together. Fewer is gentler on Archidekt, and large changes take longer.",
    ),
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

# Changes that delete or stop keeping something need a tick on the page:
# key -> (what needs it: "off" or "lower", the tick box's text).
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
/* the admin Settings page: one flat card per group, one row per setting,
   every control in the same 16rem column so the boxes, switches and lists line up */
/* the form stays in the 52rem column under the tab bar (the page itself is 1200 px) */
.set-page{max-width:52rem}
.set-page .notice{margin:0 0 1rem}
.set-nav{margin:0 0 1rem}
.set-nav .ui-chip{background:var(--surface);min-height:var(--ctl)}
.set-nav .ui-chip[aria-pressed=true]{background:var(--orange-tint)}
.set-group{padding:1rem 1rem .25rem;scroll-margin-top:4.5rem}
.set-group .gh{display:flex;flex-wrap:wrap;align-items:center;gap:.4rem .75rem;margin:0 0 .15rem}
.set-group .gh h2,.set-group .h2{margin:0;font-size:1.25rem;font-weight:700}
.set-group .intro{margin:0 0 .25rem}
.set-group details > summary{list-style:none;display:flex;align-items:center;justify-content:space-between;
  gap:.75rem;min-height:44px;cursor:pointer;margin:-.25rem 0 .5rem}
.set-group details > summary::-webkit-details-marker{display:none}
.set-group details > summary .sum{display:flex;flex-wrap:wrap;align-items:center;gap:.2rem .75rem}
.set-group details > summary .sum .ui-sub{margin:0}
.set-group details > summary > svg.i{width:20px;height:20px;flex:none}
.set-group details[open] > summary > svg.i{transform:rotate(180deg)}
.set-row{display:grid;grid-template-columns:minmax(0,1fr) 16rem;gap:.35rem 1.5rem;align-items:start;
  padding:.85rem 0;border-top:1px solid var(--border-soft);scroll-margin-top:4.5rem}
form > .set-row:first-of-type{border-top:0}
.set-row .sl{display:flex;flex-wrap:wrap;align-items:center;gap:.3rem .5rem}
.set-label{display:inline;margin:0;font-weight:700}
.set-row .sl .ui-lbl{height:22px;font-size:.78rem}
.set-help{margin:.3rem 0 0;color:var(--text-muted);font-size:.88rem;line-height:1.4}
.set-src{margin:.3rem 0 0;font-size:.86rem}
.set-src code{font-size:.8rem}
.set-lock code{overflow-wrap:break-word}
.set-err{margin:.35rem 0 0;color:var(--danger-text);font-weight:700;font-size:.88rem}
.set-row.bad input:not([type=checkbox]):not([type=radio]),.set-row.bad select{border-color:var(--danger-text)}
.set-ctl{display:flex;flex-direction:column;gap:.35rem;min-width:0}
.set-ctl input:not([type=radio]):not([type=checkbox]),.set-ctl select:not(.msel-native),.set-ctl .msel-btn{
  width:100%;margin:0}
.set-ctl .range{margin:0;color:var(--text-muted);font-size:.84rem}
.set-ctl .reset{align-self:stretch;min-height:var(--ctl);margin:0}
/* a number with its unit inside the box: every box is the same width */
.set-num{position:relative;display:block}
.set-num.has-u input{padding-right:4.75rem}
.set-num .unit{position:absolute;right:.75rem;top:50%;transform:translateY(-50%);color:var(--text-muted);
  pointer-events:none}
.set-num.typed input{border-color:var(--orange-text);box-shadow:inset 0 0 0 1px var(--orange-text)}
/* On / Off: two equal halves of the control column */
.set-seg{display:grid;grid-template-columns:1fr 1fr;margin:0;padding:0;border:1px solid var(--control-border);
  border-radius:var(--radius);overflow:hidden;min-width:0}
.set-seg legend,.set-choices legend{position:absolute;width:1px;height:1px;overflow:hidden;
  clip:rect(0 0 0 0);white-space:nowrap}
.set-seg label{position:relative;display:flex;align-items:center;justify-content:center;min-height:var(--ctl);
  margin:0;cursor:pointer;font-weight:400;background:var(--surface)}
.set-seg label + label{border-left:1px solid var(--control-border)}
.set-seg input{position:absolute;opacity:0;inset:0;width:100%;height:100%;margin:0;cursor:pointer}
.set-seg label:has(input:checked){background:var(--orange-tint);font-weight:700}
.set-seg label:has(input:focus-visible),.set-choices label:has(input:focus-visible){
  outline:2px solid var(--focus);outline-offset:-2px}
.set-choices{display:flex;flex-direction:column;margin:0;padding:0;border:1px solid var(--control-border);
  border-radius:var(--radius);overflow:hidden;min-width:0}
.set-choices label{position:relative;display:flex;align-items:center;gap:.6rem;min-height:44px;
  padding:.45rem .75rem;margin:0;cursor:pointer;font-weight:400;background:var(--surface);
  overflow-wrap:anywhere;line-height:1.3}
.set-choices label + label{border-top:1px solid var(--control-border)}
.set-choices input{flex:none;margin:0;width:1.1rem;height:1.1rem;accent-color:var(--orange)}
.set-choices label:has(input:checked){background:var(--orange-tint);font-weight:700}
.set-seg label:has(input:disabled),.set-choices label:has(input:disabled){cursor:default;
  color:var(--text-muted)}
/* set in the stack: greyed, dashed, the value still readable (4.5:1) */
.set-row.locked input:disabled:not([type=radio]):not([type=checkbox]),.set-row.locked select:disabled,
.set-row.locked .msel-btn:disabled{background:var(--surface-2);color:var(--text-muted);
  -webkit-text-fill-color:var(--text-muted);border-style:dashed;opacity:1;cursor:not-allowed}
.set-row.locked .set-seg,.set-row.locked .set-choices{border-style:dashed}
.set-row.locked input[type=radio]{accent-color:var(--text-muted)}
.set-row.locked .set-seg label,.set-row.locked .set-choices label{background:var(--surface-2);
  cursor:not-allowed}
.set-row.locked .set-seg label:has(input:checked),.set-row.locked .set-choices label:has(input:checked){
  background:var(--surface-2);box-shadow:inset 0 0 0 2px var(--text-muted)}
/* the tick that confirms lowering or turning off; with scripts only once the value goes down */
.set-confirm{grid-column:1 / -1;margin:.25rem 0 0;padding:.6rem .75rem;border-left:4px solid var(--orange);
  background:var(--orange-tint);border-radius:var(--radius)}
.set-confirm[hidden]{display:none}
.set-confirm .lead{display:flex;gap:.4rem;align-items:flex-start;margin:0 0 .25rem;color:var(--orange-text);
  font-weight:700;font-size:.92rem}
.set-confirm .lead[hidden]{display:none}
.set-confirm .lead svg.i{width:18px;height:18px;flex:none;margin-top:.05rem}
.set-confirm .tick{display:flex;align-items:flex-start;gap:.6rem;margin:0;min-height:44px;padding:.35rem 0;
  font-weight:400;font-size:.92rem;line-height:1.4;color:var(--text)}
.set-confirm .tick input{flex:none;width:20px;height:20px;margin:.1rem 0 0;accent-color:var(--orange-ctl)}
.set-save{display:flex;flex-wrap:wrap;align-items:center;gap:.5rem 1rem;padding:.85rem 0 .75rem;
  border-top:1px solid var(--border-soft)}
.set-save button{min-height:var(--ctl);margin:0}
.set-save .ui-hint{margin:0}
p.set-saved{margin:0} ul.set-saved{margin:.35rem 0 0;padding-left:1.2rem}
/* "Set in your stack": only the rows the stack sets (the chip, or ?locked=1 without scripts) */
.only-locked .set-row:not(.locked),.only-locked .set-group:not(.has-locked),
.only-locked .set-save{display:none}
@media (max-width:719.98px){
  .set-row{grid-template-columns:minmax(0,1fr)}
  .set-save .primary{flex:1 1 100%}
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
    # the form and named in a notice, never dropped without a word).
    unsaved: list[str] = field(default_factory=list)


def group_keys(group: str) -> list[str]:
    return [k for k, spec in MANAGED_SETTINGS.items() if spec.group == group]


def label(key: str) -> str:
    return TEXT.get(key, (key, ""))[0]


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
        _unused_within_longest(live, [(reset, before, spec.default)], out)
        if out.errors:
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
    _unused_within_longest(live, pending, out)
    if out.errors:
        return out
    for key, before, value in pending:
        live.set(key, value, by=by)
        out.changes.append((key, before, value))
    return out


def _unused_within_longest(live: LiveSettings, pending: list[tuple[str, Any, Any]], out: Outcome) -> None:
    """A website sign-in may not last longer unused than it may last at all: refuse a change that
    would make "Website sign-in when unused" longer than "Longest website sign-in"."""
    new = {key: value for key, _before, value in pending}
    unused_key, longest_key = "browser_session_ttl", "browser_session_max_age"
    if unused_key not in new and longest_key not in new:
        return
    unused_spec, longest_spec = MANAGED_SETTINGS[unused_key], MANAGED_SETTINGS[longest_key]
    unused = new[unused_key] * unused_spec.scale if unused_key in new else live.seconds(unused_key)
    longest = new[longest_key] * longest_spec.scale if longest_key in new else live.seconds(longest_key)
    if unused <= longest:
        return
    if unused_key in new:
        shown_longest = shown(longest_spec, longest / longest_spec.scale)
        out.errors[unused_key] = f"Must not be longer than “{label(longest_key)}” ({shown_longest})."
    else:
        shown_unused = shown(unused_spec, live.get(unused_key))
        out.errors[longest_key] = f"Must not be shorter than “{label(unused_key)}” ({shown_unused})."


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
    return ui.notice(text, "warn", role="status")


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
    locked_only: bool = False,
) -> str:
    """The page body. ``running`` is the Settings the gateway started with (a setting that applies
    after a restart may differ from its saved value); ``who`` names an account, ``when`` writes a
    time (both return HTML). ``locked_only`` shows only the rows the stack sets (the "Set in your
    stack" chip without scripts, ``?locked=1``)."""
    outcome = outcome or Outcome()
    rows = {d["key"]: d for d in live.describe()}
    locked_total = sum(1 for k in MANAGED_SETTINGS if live.locked(k))
    jump = [
        f"<a class='ui-chip' href='#g-{g}'>{html.escape(title)}</a>"
        for g, title, _intro in GROUPS
        if group_keys(g)
    ]
    if locked_total:
        # The stack's settings in one place: a toggle with scripts (static/settings.js), a link to
        # ?locked=1 without them.
        jump.append(
            f"<a class='ui-chip lockchip' href='{'/admin/settings' if locked_only else '?locked=1'}' "
            f"role='button' aria-pressed='{'true' if locked_only else 'false'}'>{icon('lock')}Set in your "
            f"stack <span class='n'>{locked_total}</span></a>"
        )
    parts = [
        notice,
        f"<nav class='ui-chips set-nav' aria-label='Setting groups'>{''.join(jump)}</nav>",
    ]
    for group, title, intro in GROUPS:
        keys = group_keys(group)
        if not keys:
            continue
        fields = "".join(_row(rows[k], running, outcome, who, when) for k in keys)
        n_locked = sum(1 for k in keys if live.locked(k))
        editable = n_locked < len(keys)
        save = (
            f"<div class='set-save'><button type='submit' class='primary'>Save {html.escape(title)}"
            "</button><span class='ui-hint unsaved' aria-live='polite'></span></div>"
            if editable
            else "<p class='muted small set-save'>Every setting here is set in your stack.</p>"
        )
        form = (
            f"<form method='post' action='/admin/settings#g-{group}' novalidate>"
            f"<input type='hidden' name='csrf' value='{html.escape(csrf)}'>"
            f"<input type='hidden' name='group' value='{group}'>{fields}{save}</form>"
        )
        intro_html = f"<p class='ui-sub intro'>{html.escape(intro)}</p>"
        stack = label_html(f"{n_locked} set in your stack", "grey", "lock") if n_locked else ""
        if group in FOLDED:
            opened = focus == group or any(k in outcome.errors for k in keys) or (locked_only and n_locked)
            inner = (
                f"<details{' open' if opened else ''}><summary><span class='sum'>"
                f"<span class='h2'>{html.escape(title)}</span>"
                f"<span class='ui-sub'>{len(keys)} setting{'' if len(keys) == 1 else 's'}</span>"
                f"{stack}</span>{icon('chev')}</summary>"
                f"{intro_html}{form}</details>"
            )
        else:
            inner = f"<div class='gh'><h2>{html.escape(title)}</h2>{stack}</div>{intro_html}{form}"
        parts.append(
            f"<section class='ui-card set-group{' has-locked' if n_locked else ''}' id='g-{group}' "
            f"aria-label='{html.escape(title)}'>{inner}</section>"
        )
    cls = "set-page only-locked" if locked_only else "set-page"
    return f"<div class='{cls}'>{''.join(parts)}</div>"


def _row(d: dict[str, Any], running: Any, outcome: Outcome, who: Callable, when: Callable) -> str:
    key = d["key"]
    spec = MANAGED_SETTINGS[key]
    name, help_text = TEXT.get(key, (key, ""))
    locked = d["source"] == "deployment"
    err = outcome.errors.get(key)
    value = outcome.posted.get(key, d["value"])
    help_id = f"h-{key}"
    described = f"{help_id} s-{key}" + (f" e-{key}" if err else "")
    control = _control(spec, value, locked=locked, described=described)
    src = _source(d, spec, running, who, when)
    reset = ""
    if d["source"] == "dashboard":
        reset = (
            f"<button type='submit' class='btn-plain reset' name='reset' value='{key}' formnovalidate>"
            f"Back to default ({html.escape(shown(spec, spec.default))})</button>"
        )
    badges = (label_html("After a restart", "grey", "refresh", cls="set-badge") if spec.restart else "") + (
        label_html("Set in your stack", "grey", "lock", cls="set-badge") if locked else ""
    )
    title = (
        f"<span class='set-label' id='l-{key}'>{html.escape(name)}</span>"
        if spec.kind == "bool" or (spec.kind == "choice" and spec.labels)
        else f"<label class='set-label' for='f-{key}'>{html.escape(name)}</label>"
    )
    confirm = ""
    if key in CONFIRM and not locked and (CONFIRM[key][0] == "lower" or d["value"]):
        confirm = _confirm(spec, d["value"], outcome.posted.get(key))
    return (
        f"<div class='set-row{' bad' if err else ''}{' locked' if locked else ''}' id='s-row-{key}'>"
        f"<div class='set-text'><div class='sl'>{title}{badges}</div>"
        + (
            f"<p class='set-help' id='{help_id}'>{html.escape(help_text)}</p>"
            if help_text
            else f"<span id='{help_id}' hidden></span>"
        )
        + f"<p class='set-src' id='s-{key}'>{src}</p>"
        + (f"<p class='set-err' id='e-{key}' role='alert'>{html.escape(err)}</p>" if err else "")
        + f"</div><div class='set-ctl'>{control}{reset}</div>{confirm}</div>"
    )


def _confirm(spec: SettingSpec, saved: Any, posted: str | None) -> str:
    """The tick that confirms turning a setting off or lowering it (CONFIRM). It is asked on every
    save that lowers the value (apply_form refuses one without it). With scripts it shows only once
    the value is typed lower or switched off (static/settings.js fills in the first sentence);
    without them it shows all the time, as before. After a refused save it shows with the value
    that was sent."""
    key = spec.key
    rule = CONFIRM[key][0]
    what = "turning this off" if rule == "off" else "lowering this"
    said = ""
    if posted is not None:
        try:
            value = spec.parse(posted)
        except SettingError:
            value = saved
        if needs_confirm(key, saved, value):
            said = (
                "You turned this off."
                if rule == "off"
                else f"You lowered this from {shown(spec, saved)} to {shown(spec, value)}."
            )
    start = "on" if (rule == "off" and saved) else ("off" if rule == "off" else _plain(saved))
    return (
        f"<div class='set-confirm' role='group' aria-labelledby='c-{key}' data-confirm='{rule}' "
        f"data-from='{html.escape(start)}' data-unit='{html.escape(spec.unit or '')}'"
        f"{' data-shown' if said else ''}>"
        f"<p class='lead' id='c-{key}'{'' if said else ' hidden'}>{icon('alert')}<span>"
        f"<span class='said'>{html.escape(said)}</span> Tick the box below to confirm {what}.</span></p>"
        f"<label class='tick'><input type='checkbox' name='confirm_{key}' value='yes'>"
        f"<span>{html.escape(CONFIRM[key][1])}</span></label></div>"
    )


def _source(d: dict[str, Any], spec: SettingSpec, running: Any, who: Callable, when: Callable) -> str:
    if d["source"] == "deployment":
        return (
            "<span class='set-lock'>Set in your stack "
            # it may break only after an underscore, never mid-word
            f"(<code>{html.escape(spec.env).replace('_', '_<wbr>')}</code>), {STACK_LOCK_HOW}</span>"
        )
    if d["source"] == "dashboard":
        changed = d.get("changed") or {}
        text = f"Changed here by {who(changed.get('updated_by'))}, {when(changed.get('updated_at'))}."
    else:
        text = "Default."
    now = getattr(running, spec.key, d["value"])
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
        # and wraps at any width or text size.
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
    # the unit sits inside the box at its right, so every box is the same width (K-04)
    unit = f"<span class='unit' id='u-{key}'>{html.escape(spec.unit)}</span>" if spec.unit else ""
    unit_id = f" u-{key}" if spec.unit else ""
    return (
        f"<span class='set-num{' has-u' if spec.unit else ''}'>"
        f"<input type='number' id='f-{key}' name='{name}' "
        f"value='{html.escape(shown_value)}' min='{lo}' max='{_plain(spec.hi)}' step='{step}' "
        f"inputmode='{'numeric' if spec.kind == 'int' else 'decimal'}' "
        f"aria-describedby='{described}{unit_id} r-{key}'{dis}>{unit}</span>"
        f"<p class='range' id='r-{key}'>Allowed: {html.escape(span)}</p>"
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
        return ui.notice("Nothing to save: no setting was changed.", "ok", role="status")
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
