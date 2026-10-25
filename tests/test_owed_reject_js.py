"""The deck page's owed reject (static/cardmenu.js oweReject / settleOwed), run in node with stubs:
the note is kept per member (security review 0.7.44 S-03), and it is dropped, with
``mtg:rejected`` announced, only when the reject went through or the server says the proposal is
no longer pending or not there; a refusal for now (403, 429, 5xx) keeps it for the next page, one
try a page load (S-04). Skipped without node."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

CARDMENU = Path(__file__).resolve().parents[1] / "src" / "mtg_gateway" / "static" / "cardmenu.js"

# Runs the owed-reject block of cardmenu.js (from ``var OWED`` to ``var norm``) with stub storage,
# a stub post() answering with the case's status, and a page whose top bar names ``who`` (the
# account link's data-who).
HARNESS = r"""
const fs = require("fs");
const [path, raw] = process.argv.slice(2);
const c = JSON.parse(raw);
const src = fs.readFileSync(path, "utf8");
const block = src.slice(src.indexOf("var OWED"), src.indexOf("var norm = function"));
const store = new Map(Object.entries(c.stored || {}));
const localStorage = {
  getItem: k => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: k => store.delete(k),
};
const sent = [], events = [];
const post = p => {
  sent.push(p);
  return c.offline ? Promise.reject(new Error("offline")) : Promise.resolve(c.answer);
};
const tick = () => new Promise(r => setTimeout(r, 0));
const who = c.who ? { getAttribute: n => (n === "data-who" ? c.who : null) } : null;
const document = {
  querySelector: s => (s.indexOf("who") >= 0 ? who : null),
  dispatchEvent: e => events.push(e.detail.proposal_id),
};
class CustomEvent { constructor(t, o) { this.type = t; this.detail = o.detail; } }
const api = new Function("localStorage", "post", "csrf", "enc", "document", "CustomEvent",
  block + "; return { oweReject: oweReject, settleOwed: settleOwed," +
  " forgetOwed: typeof forgetOwed === 'function' ? forgetOwed : function () {} };")(
  localStorage, post, () => "tok", encodeURIComponent, document, CustomEvent);
(async () => {
  for (const pid of c.owe || []) api.oweReject(pid);
  if (c.signOut) api.forgetOwed();
  for (const pid of c.oweAfter || []) api.oweReject(pid);
  for (let i = 0; i < (c.loads ?? 1); i++) { api.settleOwed(); await tick(); await tick(); }
  console.log(JSON.stringify({ store: Object.fromEntries(store), sent, events }));
})();
"""

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is needed to run cardmenu.js")


def _run(tmp_path: Path, **case) -> dict:
    harness = tmp_path / "owed_harness.js"
    harness.write_text(HARNESS, encoding="utf-8")
    out = subprocess.run(
        ["node", str(harness), str(CARDMENU), json.dumps(case)], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)


def _owed(got: dict) -> dict[str, list]:
    return {
        k: json.loads(v) for k, v in got["store"].items() if k.startswith("mtg_reject_owed") and json.loads(v)
    }


def test_the_note_is_kept_per_member(tmp_path: Path) -> None:
    # alice's page notes her proposal under her own key
    alice = _run(tmp_path, who="alice", owe=["alicePid"], loads=0)
    assert list(_owed(alice)) != ["mtg_reject_owed"] and list(_owed(alice).values()) == [["alicePid"]]
    # bob signs in on the same browser: his pages leave alice's note alone and send nothing
    bob = _run(tmp_path, who="bob", stored=alice["store"], answer={"ok": True, "status": 200})
    assert bob["sent"] == [] and bob["events"] == [] and _owed(bob) == _owed(alice)
    # alice's next page settles it
    back = _run(tmp_path, who="alice", stored=alice["store"], answer={"ok": True, "status": 200})
    assert back["sent"] == ["/api/v1/proposals/alicePid/reject"] and back["events"] == ["alicePid"]
    assert _owed(back) == {}
    # a page that names no member writes and sends nothing
    nobody = _run(tmp_path, who="", stored=alice["store"], owe=["x"], answer={"ok": True, "status": 200})
    assert nobody["sent"] == [] and _owed(nobody) == _owed(alice)


def test_signing_out_clears_the_members_note(tmp_path: Path) -> None:
    got = _run(
        tmp_path,
        who="alice",
        owe=["p1"],
        signOut=True,  # the sign-out form's submit (cardmenu.js)
        # the deck page's pagehide comes after the sign-out's submit: it notes nothing then
        oweAfter=["p2"],
        loads=0,
    )
    assert _owed(got) == {}


@pytest.mark.parametrize(
    "answer",
    [
        {"ok": False, "status": 403, "error": "forbidden"},
        {"ok": False, "status": 429, "error": "rate_limited"},
        {"ok": False, "status": 503, "error": "unavailable"},
        {"ok": False, "status": 502},
    ],
)
def test_a_refusal_for_now_keeps_the_note_and_announces_nothing(tmp_path: Path, answer: dict) -> None:
    first = _run(tmp_path, who="alice", owe=["p1"], answer=answer)
    assert first["sent"] == ["/api/v1/proposals/p1/reject"]  # one try a page load
    assert first["events"] == [] and list(_owed(first).values()) == [["p1"]]
    # the next page load tries once more, and only once
    again = _run(tmp_path, who="alice", stored=first["store"], answer=answer)
    assert again["sent"] == ["/api/v1/proposals/p1/reject"] and list(_owed(again).values()) == [["p1"]]


@pytest.mark.parametrize(
    "answer",
    [
        {"ok": True, "status": 200, "state": "rejected"},
        {"ok": False, "status": 404, "error": "not_found"},
        {"ok": False, "status": 409, "error": "not_pending", "state": "applied"},
    ],
)
def test_done_or_not_pending_drops_the_note_and_announces_it(tmp_path: Path, answer: dict) -> None:
    got = _run(tmp_path, who="alice", owe=["p1"], answer=answer)
    assert got["sent"] == ["/api/v1/proposals/p1/reject"] and got["events"] == ["p1"]
    assert _owed(got) == {}


def test_no_connection_keeps_the_note(tmp_path: Path) -> None:
    got = _run(tmp_path, who="alice", owe=["p1"], offline=True)
    assert got["sent"] == ["/api/v1/proposals/p1/reject"] and got["events"] == []
    assert list(_owed(got).values()) == [["p1"]]
