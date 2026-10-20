"""The admin Users and Activity pages read every member's apps in a fixed number of queries
(0.7.29), with the same result as the per-member lookups they replace."""

from __future__ import annotations

import re
import time
from pathlib import Path
from types import SimpleNamespace

from mtg_gateway import activity_view, admin
from mtg_gateway.db import Database


def _members(db: Database, members: int, apps: int) -> None:
    for a in range(apps):
        db.save_client(f"app-{a}", {"client_name": f"App {a}"})
    db.save_cimd_client("https://example.com/cimd.json", {"client_name": "Published app"}, ttl=3600)
    later = int(time.time()) + 3600
    for m in range(members):
        db.upsert_user(f"m{m}", email=None, name=f"Member {m}", preferred_username=None, groups=[])
        for a in range(apps):
            db.save_token(
                f"t-{m}-{a}",
                kind="access",
                client_id=f"app-{a}",
                sub=f"m{m}",
                scopes=[],
                resource=None,
                family=f"f-{m}-{a}",
                expires_at=later,
            )
        db.save_token(
            f"t-{m}-cimd",
            kind="access",
            client_id="https://example.com/cimd.json",
            sub=f"m{m}",
            scopes=[],
            resource=None,
            family=f"f-{m}-c",
            expires_at=later,
        )
        db.audit("tool_call", sub=f"m{m}", client_id=f"app-{m % apps}")
        db.audit("tool_call", sub=f"m{m}", client_id="unknown-app")


def _queries(db: Database, fn) -> int:
    seen: list[str] = []
    db._conn.set_trace_callback(seen.append)
    try:
        fn()
    finally:
        db._conn.set_trace_callback(None)
    return sum(1 for q in seen if q.lstrip().upper().startswith("SELECT"))


def test_users_page_data_matches_the_per_member_lookups(tmp_path: Path):
    db = Database(tmp_path / "g.sqlite")
    _members(db, members=4, apps=3)
    state = SimpleNamespace(db=db)
    rows = admin.users_data(state)
    assert len(rows) == 4
    for r in rows:
        assert r["clients"] == db.user_clients(r["sub"])
        assert {c["name"] for c in r["clients"]} == {"App 0", "App 1", "App 2", "Published app"}
    db.close()


def test_users_page_queries_do_not_grow_with_members(tmp_path: Path):
    small = Database(tmp_path / "s.sqlite")
    _members(small, members=2, apps=2)
    big = Database(tmp_path / "b.sqlite")
    _members(big, members=30, apps=4)
    count_small = _queries(small, lambda: admin.users_data(SimpleNamespace(db=small)))
    count_big = _queries(big, lambda: admin.users_data(SimpleNamespace(db=big)))
    assert count_big == count_small <= 4
    small.close()
    big.close()


def test_activity_names_each_app_with_a_fixed_number_of_queries(tmp_path: Path):
    """The redesigned Activity page reads its apps, members and rows in queries that do not
    grow with the number of members or apps on the page."""

    def count(name: str, members: int, apps: int) -> tuple[int, str]:
        db = Database(tmp_path / name)
        _members(db, members=members, apps=apps)
        out: list[str] = []
        n = _queries(db, lambda: out.append(activity_view.admin_body(SimpleNamespace(db=db), {}, nav="")))
        db.close()
        return n, out[0]

    small, _ = count("s.sqlite", members=2, apps=2)
    big, page = count("b.sqlite", members=25, apps=3)
    apps = re.findall(r"<span class='c-app'>(.*?)</span><span class='c-when'>", page)
    assert any("App " in a for a in apps) and "/admin/activity?sub=m24'" in page
    # an app with no stored name is named "An assistant"; its id shows only in the row's details
    assert any("An assistant" in a for a in apps) and not any("unknown-app" in a for a in apps)
    assert big == small
