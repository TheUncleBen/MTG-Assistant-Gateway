"""0.7.29: applying a selection of waiting changes with one press (PROPOSALS-FLEXIBLE-FLOWS.md
§4 B-D and G; G16, Q27h, Q27i, Q134 A3, A8, A9).

The member checks any waiting changes, on any decks, in any order, and presses Apply once (the
Proposals page, the deck's strip, or "Apply all" on the chat card). ``plan`` says what will happen
before anything is sent (the confirm); ``run`` does it:

* each deck in turn, its changes oldest first, whatever order they were checked in;
* every change is re-checked against the live deck just before it is sent, as a single apply is
  (recheck.py): one that would now do something else is not sent and waits as "Needs a look"
  (Q27h "Skip it"), and the rest go on;
* one gateway snapshot (and one backup copy on Archidekt) per deck per selection, taken before the
  first change touches it, so "Undo" puts the deck back as it was before the whole selection and a
  big selection does not push older snapshots out (G16);
* a restore never runs with other changes to the same deck, and deleting a deck never runs in a
  selection at all (its own review page only, R-142);
* a change that depends on one that is not applied in this selection waits for it (A9); a held
  change may be applied by the member, and the confirm says why it was held (A8);
* a change that fails after something reached Archidekt stops the rest of that deck's changes
  (they stay waiting), since the deck is then not as any of them was reviewed against.

Nothing here decides who may press: callers check that first (the page's session, CSRF token and
click guard; the card's set code, approve.py). Every change still goes through DeckService.apply,
with its own claim, version check and re-check."""

from __future__ import annotations

import asyncio
import contextvars
import logging
import secrets
import time
from collections import OrderedDict
from typing import Any

from . import formatting

logger = logging.getLogger(__name__)

# The selection running in this task, deck id -> {"snapshot_id", "backup"}: DeckService's
# snapshot and backup steps reuse what the selection's first change on that deck made. The
# applies run as their own tasks, which copy this context, so they share the same dict.
shared_snapshots: contextvars.ContextVar[dict[str, dict[str, Any]] | None] = contextvars.ContextVar(
    "batch_shared_snapshots", default=None
)

KEEP_RESULTS = 100  # finished selections kept in memory for their result panel (per gateway)
ALONE_KINDS = ("restore",)

RESTORE_ALONE = (
    "Not sent: a restore is applied on its own, never together with other changes to the same deck. "
    "It is still waiting: apply it by itself."
)
DELETE_ALONE = "Not sent: deleting a deck is approved only on its own review page. It is still waiting."
FORMATTING_ALONE = (
    "Not sent: it keeps the formatting already there, such as bold text, links or pictures, so it is "
    "approved on its own review page, which shows how it will look. It is still waiting."
)
# D-1, E-2 (wording draft, WORDING-6)
FORMATTING_LOST_ALONE = (
    "Not sent: it removes all the formatting already there, such as bold text, links and pictures, so "
    "it is approved on its own review page, which says what goes. It is still waiting."
)
# a change held after the list Apply all was pressed on was drawn: left out, as on the card
HELD_SINCE = "Not sent: it was held after this list was shown. {reason} It is still waiting."
EARLIER_FAILED = (
    "Not sent: an earlier change to this deck in this selection failed after part of it reached "
    "Archidekt. It is still waiting: open it to check it against the deck as it is now."
)


def waits_for_text(no: Any) -> str:
    which = f"#{no}" if no else "a change it depends on"
    return f"Not sent: it waits for {which}, which was not applied. It is still waiting."


def no_longer_waiting_text(state: str) -> str:
    return f"Not sent: it is no longer waiting (it is {state})."


# A change its own app replaced, picked (review round 3; wording drafts, WORDING-3-r3): its line
# in a selection, and the refusal when it was picked on its own
REPLACED_NOT_SENT = "Not sent: it was replaced by {new}. Apply {new} instead."
REPLACED_ALONE = "{old} was replaced by {new}, so nothing was sent. Apply {new} instead."


def older_waiting_text(n: int) -> str:
    if n <= 0:
        return ""
    return (
        "1 older change to this deck stays waiting."
        if n == 1
        else f"{n} older changes to this deck stay waiting."
    )


def held_text(status_reason: str) -> str:
    """A8: the confirm's line for a held change the member applies anyway."""
    reason = str(status_reason or "").strip()
    if reason[:1].isupper():
        reason = reason[0].lower() + reason[1:]
    return f"Held: {reason}" if reason else "Held."


def _order_key(row: dict[str, Any]) -> tuple[int, int, str]:
    return (int(row.get("created_at") or 0), int(row.get("board_no") or 0), str(row["id"]))


def plan(
    svc: Any,
    sub: str,
    picks: list[str],
    versions: dict[str, int] | None = None,
    shown: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """What applying ``picks`` would do, before anything is sent: per deck, the changes in the
    order they will run, the ones that will not be sent and why, and the older changes left
    waiting. ``versions`` (proposal id -> the version the member saw) is carried to the apply,
    which refuses a change amended since (G16). ``shown`` (proposal id -> the status the member
    saw and when it was set) is carried too: a change held since is left out, not sent."""
    db = svc.db
    shown = dict(shown or {})
    versions = dict(versions or {})
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for pid in picks:
        pid = str(pid)
        if pid in seen:
            continue
        seen.add(pid)
        row = db.get_proposal(pid, sub)
        if row is None:
            continue
        if row["state"] != "pending":
            new = _replaced_by(db, sub, pid) if row["state"] == "replaced" else None
            why = REPLACED_NOT_SENT.format(new=new) if new else no_longer_waiting_text(row["state"])
            skipped.append({**_line(svc, sub, row, why), **({"replaced_by": new} if new else {})})
            continue
        rows.append(row)
    by_deck: dict[str, list[dict[str, Any]]] = {}
    for row in sorted(rows, key=_order_key):
        by_deck.setdefault(str(row["deck_id"]), []).append(row)
    decks: list[dict[str, Any]] = []
    for deck_id, group in sorted(by_deck.items(), key=lambda kv: _order_key(kv[1][0])):
        run: list[dict[str, Any]] = []
        for row in group:
            changes = row.get("changes") if isinstance(row.get("changes"), dict) else {}
            if row.get("kind") == "action" and changes.get("action") == "delete_deck":
                skipped.append(_line(svc, sub, row, DELETE_ALONE))
            elif formatting.keeps_formatting(row):  # Q137: approved where its formatting shows
                lost = formatting.loses_formatting(row)
                skipped.append(_line(svc, sub, row, FORMATTING_LOST_ALONE if lost else FORMATTING_ALONE))
            elif row.get("kind") in ALONE_KINDS and len(group) > 1:
                skipped.append(_line(svc, sub, row, RESTORE_ALONE))
            else:
                line = _line(svc, sub, row)
                line["version"] = int(versions.get(row["id"]) or row.get("version") or 1)
                if row["id"] in shown:
                    line["shown_status"] = str(shown[row["id"]].get("status") or "")
                    line["shown_status_at"] = shown[row["id"]].get("status_at")
                run.append(line)
        if not run:
            continue
        first = min(_order_key(r) for r in group)
        older = [
            r
            for r in db.pending_board(sub, deck_id)
            if r["id"] not in seen and (int(r["created_at"]), int(r.get("board_no") or 0), r["id"]) < first
        ]
        decks.append(
            {
                "deck_id": deck_id,
                "deck_name": group[0].get("deck_name") or deck_id,
                "changes": run,
                "older_waiting": len(older),
                "older_text": older_waiting_text(len(older)),
            }
        )
    return {
        "decks": decks,
        "skipped": skipped,
        "count": sum(len(d["changes"]) for d in decks),
        "rows": sum(int(c.get("row_count") or 0) for d in decks for c in d["changes"]),
    }


def _replaced_by(db: Any, sub: str, proposal_id: str) -> str | None:
    """The board number ("#5") of the change that replaced ``proposal_id``, or None."""
    for link in db.proposal_links(sub, [proposal_id]):
        if link["other_id"] == proposal_id and link["kind"] == "replaces":
            no = db.proposal_board_numbers(sub, [link["proposal_id"]]).get(link["proposal_id"])
            if no:
                return f"#{no}"
    return None


def _line(svc: Any, sub: str, row: dict[str, Any], why: str | None = None) -> dict[str, Any]:
    lines = str(row.get("diff_text") or "").splitlines()
    summary = ", ".join(lines[:4]) + (f" (+{len(lines) - 4} more)" if len(lines) > 4 else "")
    out: dict[str, Any] = {
        "proposal_id": row["id"],
        "board_no": row.get("board_no"),
        "kind": row.get("kind"),
        "deck_id": str(row["deck_id"]),
        "deck_name": row.get("deck_name") or str(row["deck_id"]),
        "summary": summary[:300],
        "row_count": len(row.get("rows") or []),
        "created_by": svc._creator_label(row.get("created_by_client")),
        "created_at": row.get("created_at"),
        # what the confirm showed, so its press can say so (a change held since is left out)
        "status": row.get("status") or "",
        "status_at": row.get("status_at"),
    }
    if row.get("status") == "held":
        out["held"] = held_text(svc._status_fields(sub, row)["status_reason"])
    if why:
        out["why"] = why
    return out


def _depends(svc: Any, sub: str, pid: str) -> list[str]:
    return [
        lk["other_id"]
        for lk in svc.db.proposal_links(sub, [pid])
        if lk["proposal_id"] == pid and lk["kind"] == "depends_on"
    ]


class Batches:
    """The selections being applied and the last finished ones, for their result panels. Kept in
    memory: what a selection changed is in each proposal (state, result, snapshot) anyway."""

    def __init__(self) -> None:
        self._runs: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._tasks: dict[str, asyncio.Task[Any]] = {}

    def get(self, sub: str, batch_id: str) -> dict[str, Any] | None:
        run = self._runs.get(batch_id)
        return run if run is not None and run["sub"] == sub else None

    def start(self, svc: Any, sub: str, the_plan: dict[str, Any], *, via: str) -> dict[str, Any]:
        batch_id = secrets.token_urlsafe(9)
        run: dict[str, Any] = {
            "batch_id": batch_id,
            "sub": sub,
            "state": "applying",
            "started_at": int(time.time()),
            "finished_at": None,
            "plan": the_plan,
            "applied": [],
            "not_sent": list(the_plan["skipped"]),
            "failed": [],
            "undo": [],
        }
        self._runs[batch_id] = run
        while len(self._runs) > KEEP_RESULTS:
            old_id, old = next(iter(self._runs.items()))
            if old["state"] == "applying":
                break
            self._runs.pop(old_id)
        task = asyncio.create_task(_run(svc, sub, run, via=via))
        self._tasks[batch_id] = task

        def done(t: asyncio.Task[Any]) -> None:
            self._tasks.pop(batch_id, None)
            if not t.cancelled() and t.exception() is not None:
                logger.error("applying a selection failed", exc_info=t.exception())
                run["state"] = "done"

        task.add_done_callback(done)
        return run

    async def wait(self, batch_id: str, timeout: float) -> None:
        task = self._tasks.get(batch_id)
        if task is None:
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except TimeoutError:
            return

    async def aclose(self) -> None:
        for task in list(self._tasks.values()):
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)


async def _run(svc: Any, sub: str, run: dict[str, Any], *, via: str) -> None:
    from .decks import DeckError

    not_applied: set[str] = {str(s["proposal_id"]) for s in run["plan"]["skipped"]}
    try:
        for deck in run["plan"]["decks"]:
            shared: dict[str, dict[str, Any]] = {}
            token = shared_snapshots.set(shared)
            stop = False
            try:
                for item in deck["changes"]:
                    pid = item["proposal_id"]
                    if stop:
                        not_applied.add(pid)
                        run["not_sent"].append({**item, "why": EARLIER_FAILED})
                        continue
                    # A9: it waits while a change it depends on is still waiting (not selected,
                    # selected on a deck that comes later) or was not sent in this selection. A
                    # rejected, withdrawn, failed or expired one only holds it, and the member may
                    # apply a held change (A8): the confirm said so.
                    blocked = [
                        d for d in _depends(svc, sub, pid) if d in not_applied or _still_open(svc, sub, d)
                    ]
                    if blocked:
                        no = svc.db.proposal_board_numbers(sub, blocked).get(blocked[0])
                        not_applied.add(pid)
                        run["not_sent"].append({**item, "why": waits_for_text(no)})
                        continue
                    try:
                        await svc.apply(
                            sub,
                            pid,
                            via=via,
                            version=item.get("version"),
                            shown_status=item.get("shown_status"),
                            shown_status_at=item.get("shown_status_at"),
                        )
                        await svc.wait_applied(pid)
                    except DeckError as exc:
                        not_applied.add(pid)
                        if exc.kind == "now_held":
                            # held after the list was drawn: left out, its hold said (a safe default)
                            reason = str(exc.extra.get("status_reason") or "")
                            run["not_sent"].append(
                                {**item, "why": HELD_SINCE.format(reason=reason).replace("  ", " ")}
                            )
                            continue
                        row = svc.db.get_proposal(pid, sub) or {}
                        if row.get("state") == "failed":
                            run["failed"].append({**item, "why": str(exc)})
                            stop = True
                        else:
                            run["not_sent"].append({**item, "why": str(exc)})
                        continue
                    row = svc.db.get_proposal(pid, sub) or {}
                    if row.get("state") == "applied":
                        run["applied"].append({**item, "snapshot_id": row.get("snapshot_id")})
                    elif row.get("state") == "failed":
                        not_applied.add(pid)
                        error = (row.get("result") or {}).get("detail") or (row.get("result") or {}).get(
                            "error"
                        )
                        run["failed"].append({**item, "why": str(error or "It failed.")})
                        stop = True
                    else:
                        not_applied.add(pid)
                        status = svc._status_fields(sub, row) if row.get("status") else {}
                        why = status.get("status_reason") or no_longer_waiting_text(str(row.get("state")))
                        run["not_sent"].append({**item, "why": why})
            finally:
                shared_snapshots.reset(token)
            made = shared.get(str(deck["deck_id"]))
            applied_here = [a for a in run["applied"] if a["deck_id"] == str(deck["deck_id"])]
            if made and applied_here:
                run["undo"].append(
                    {
                        "deck_id": str(deck["deck_id"]),
                        "deck_name": deck["deck_name"],
                        "snapshot_id": made["snapshot_id"],
                        "applied": [a["proposal_id"] for a in applied_here],
                        "after_at": int(time.time()),
                    }
                )
    finally:
        run["state"] = "done"
        run["finished_at"] = int(time.time())
        svc._audit(
            "proposals_applied_together",
            sub=sub,
            detail={
                "batch_id": run["batch_id"],
                "via": via,
                "applied": [a["proposal_id"] for a in run["applied"]],
                "not_sent": [a["proposal_id"] for a in run["not_sent"]],
                "failed": [a["proposal_id"] for a in run["failed"]],
            },
        )


def summary_text(run: dict[str, Any]) -> str:
    """One line for the result panel and the chat card: "Applied 3. 1 not sent."."""
    bits = [f"Applied {len(run['applied'])}."]
    if run["not_sent"]:
        bits.append(f"{len(run['not_sent'])} not sent.")
    if run["failed"]:
        bits.append(f"{len(run['failed'])} failed.")
    return " ".join(bits)


def _still_open(svc: Any, sub: str, proposal_id: str) -> bool:
    """A note still on its way: waiting and unexpired, or being applied."""
    row = svc.db.get_proposal(proposal_id, sub) or {}
    if row.get("state") == "applying":
        return True
    return row.get("state") == "pending" and int(row.get("expires_at") or 0) >= int(time.time())
