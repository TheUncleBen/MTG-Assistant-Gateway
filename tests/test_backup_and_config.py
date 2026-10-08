from __future__ import annotations

import asyncio
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from mtg_gateway.backup import export_now, prune, seconds_until, sweep_work_dirs
from mtg_gateway.config import ConfigError, load_settings
from mtg_gateway.db import Database


def test_backup_is_also_copied_to_the_copy_folder(tmp_path: Path):
    """MTG_BACKUP_COPY_DIR (a network share, say): every backup lands there too, owner-only, and
    old copies are pruned there as well. A copy folder that cannot be written does not cost the
    main backup; the failure is recorded for the admin page."""
    from mtg_gateway import backup as backup_mod

    db = Database(tmp_path / "d" / "g.sqlite")
    db.upsert_user("u1", email=None, name="x", preferred_username=None, groups=[])
    old = tmp_path / "copies" / "mtg-gateway-20000101T000000Z.sqlite"
    old.parent.mkdir()
    old.write_bytes(b"x")
    os.utime(old, (0, 0))
    out = export_now(db, tmp_path / "b", keep_days=14, copy_dir=tmp_path / "copies")
    copied = tmp_path / "copies" / out.name
    assert copied.read_bytes() == out.read_bytes()
    assert (copied.stat().st_mode & 0o777) == 0o600
    assert not old.exists() and backup_mod.last_run["copy_error"] is None
    blocked = tmp_path / "not-a-folder"
    blocked.write_text("a file where the folder should be")
    out2 = export_now(db, tmp_path / "b", keep_days=14, copy_dir=blocked)
    assert out2.exists() and backup_mod.last_run["copy_error"]
    db.close()


def test_backup_copy_dir_setting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _write_secrets(tmp_path, monkeypatch)
    monkeypatch.setenv("MTG_BACKUP_COPY_DIR", "/copies")
    monkeypatch.delenv("MTG_BACKUP_DIR", raising=False)
    assert load_settings().backup_copy_dir is None  # no backups, so nothing to copy
    monkeypatch.setenv("MTG_BACKUP_DIR", "/backups")
    assert load_settings().backup_copy_dir == Path("/copies")
    monkeypatch.delenv("MTG_BACKUP_COPY_DIR")
    assert load_settings().backup_copy_dir is None


def test_backup_export_and_prune(tmp_path: Path):
    db = Database(tmp_path / "d" / "g.sqlite")
    db.upsert_user("u1", email=None, name="x", preferred_username=None, groups=[])
    out = export_now(db, tmp_path / "b", keep_days=14)
    assert out.exists() and out.name.startswith("mtg-gateway-") and out.suffix == ".sqlite"
    copy = Database(out)
    assert copy.get_user("u1")["name"] == "x"
    copy.close()
    old = tmp_path / "b" / "mtg-gateway-20000101T000000Z.sqlite"
    old.write_bytes(b"x")
    os.utime(old, (0, 0))
    assert prune(tmp_path / "b", keep_days=14) == 1
    assert not old.exists() and out.exists()
    db.close()


def test_seconds_until_next_hour():
    now = datetime(2026, 10, 4, 2, 30, tzinfo=UTC)
    assert seconds_until(3, now) == 1800
    assert seconds_until(2, now) == 23.5 * 3600


def _write_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "fernet").write_text(Fernet.generate_key().decode())
    (tmp_path / "session").write_text("x" * 40)
    (tmp_path / "oidc").write_text("client-secret")
    monkeypatch.setenv("MTG_FERNET_KEY_FILE", str(tmp_path / "fernet"))
    monkeypatch.setenv("MTG_SESSION_SECRET_FILE", str(tmp_path / "session"))
    monkeypatch.setenv("MTG_OIDC_CLIENT_SECRET_FILE", str(tmp_path / "oidc"))
    monkeypatch.setenv("MTG_PUBLIC_URL", "https://mtg.example.test/")
    monkeypatch.setenv("MTG_OIDC_ISSUER", "https://auth.example.test/application/o/mtg/")
    monkeypatch.setenv("MTG_OIDC_CLIENT_ID", "abc")
    monkeypatch.setenv("MTG_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MTG_REQUIRED_GROUP", "MTG Assistant Gateway Users")
    monkeypatch.delenv("MTG_ALLOW_ANY_IDP_USER", raising=False)


def test_load_settings_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _write_secrets(tmp_path, monkeypatch)
    s = load_settings()
    assert s.public_url == "https://mtg.example.test"
    assert s.mcp_url == "https://mtg.example.test/mcp"
    assert s.callback_url == "https://mtg.example.test/auth/callback"
    assert s.allowed_hosts == ["mtg.example.test", "mtg.example.test:*"]
    assert s.backup_dir is None


def test_load_settings_requires_group_or_explicit_opt_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _write_secrets(tmp_path, monkeypatch)
    assert load_settings().required_group == "MTG Assistant Gateway Users"
    for empty in ("", "   "):
        monkeypatch.setenv("MTG_REQUIRED_GROUP", empty)
        with pytest.raises(ConfigError, match="MTG_ALLOW_ANY_IDP_USER"):
            load_settings()
    monkeypatch.delenv("MTG_REQUIRED_GROUP")
    with pytest.raises(ConfigError, match="MTG_REQUIRED_GROUP is empty"):
        load_settings()
    monkeypatch.setenv("MTG_ALLOW_ANY_IDP_USER", "false")
    with pytest.raises(ConfigError):
        load_settings()
    monkeypatch.setenv("MTG_ALLOW_ANY_IDP_USER", "true")
    assert load_settings().required_group is None


def test_load_settings_rejects_bad_fernet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _write_secrets(tmp_path, monkeypatch)
    (tmp_path / "fernet").write_text("not-a-key")
    with pytest.raises(ConfigError, match="Fernet"):
        load_settings()


def test_load_settings_rejects_http_public_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _write_secrets(tmp_path, monkeypatch)
    monkeypatch.setenv("MTG_PUBLIC_URL", "http://mtg.example.test")
    with pytest.raises(ConfigError, match="https"):
        load_settings()


def test_load_settings_missing_secret_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _write_secrets(tmp_path, monkeypatch)
    monkeypatch.setenv("MTG_OIDC_CLIENT_SECRET_FILE", str(tmp_path / "missing"))
    with pytest.raises(ConfigError, match="cannot read secret file"):
        load_settings()


async def test_pacer_releases_lock_when_cancelled() -> None:
    import asyncio

    from mtg_gateway.archidekt import ArchidektError, Pacer

    pacer = Pacer(0.5)
    async with pacer:
        pass  # sets the next allowed time half a second out

    async def waiter() -> None:
        async with pacer:
            pass

    task = asyncio.create_task(waiter())
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not pacer._lock.locked()
    pacer._open_until = 1e12  # breaker open
    with pytest.raises(ArchidektError):
        async with pacer:
            pass
    assert not pacer._lock.locked()


def test_stuck_applying_proposals_are_failed_by_purge(tmp_path) -> None:
    import time

    from mtg_gateway.db import Database

    db = Database(tmp_path / "x.sqlite")
    db.save_proposal(
        {
            "id": "p1",
            "owner_sub": "u",
            "deck_id": "1",
            "deck_name": "d",
            "baseline_fingerprint": "f",
            "changes": [],
            "diff_text": "",
            "expires_at": int(time.time()) + 100,
        }
    )
    assert db.claim_proposal("p1", "u")
    started = db.get_proposal("p1", "u")["applied_at"]
    assert started
    # Recording the snapshot mid-apply must not clear the start time (regression).
    db.finish_proposal("p1", state="applying", snapshot_id="snap")
    assert db.get_proposal("p1", "u")["applied_at"] == started
    with db.tx() as c:
        c.execute("UPDATE proposals SET created_at = ? WHERE id = 'p1'", (int(time.time()) - 7200,))
    db.purge_expired()
    assert db.get_proposal("p1", "u")["state"] == "applying"  # old proposal, but the apply just started
    with db.tx() as c:
        c.execute("UPDATE proposals SET applied_at = ? WHERE id = 'p1'", (int(time.time()) - 2 * 3600,))
    db.purge_expired()
    assert db.get_proposal("p1", "u")["state"] == "applying"  # a long background apply may still run
    with db.tx() as c:
        c.execute("UPDATE proposals SET applied_at = ? WHERE id = 'p1'", (int(time.time()) - 7 * 3600,))
    db.purge_expired()
    row = db.get_proposal("p1", "u")
    assert row["state"] == "failed" and row["result"]["error"] == "interrupted"


def test_trusted_proxies_default_and_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _write_secrets(tmp_path, monkeypatch)
    assert "10.0.0.0/8" in load_settings().trusted_proxies
    monkeypatch.setenv("MTG_TRUSTED_PROXIES", "10.0.5.2, 192.168.1.0/24")
    assert load_settings().trusted_proxies == ["10.0.5.2", "192.168.1.0/24"]
    for bad in ("npm", "192.168.1.5/24"):
        monkeypatch.setenv("MTG_TRUSTED_PROXIES", bad)
        with pytest.raises(ConfigError):
            load_settings()


def test_load_settings_scryfall_lookup_interval(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _write_secrets(tmp_path, monkeypatch)
    assert load_settings().scryfall_lookup_interval == 0.5
    monkeypatch.setenv("MTG_SCRYFALL_LOOKUP_INTERVAL", "1.25")
    assert load_settings().scryfall_lookup_interval == 1.25
    monkeypatch.setenv("MTG_SCRYFALL_LOOKUP_INTERVAL", "0")
    with pytest.raises(ConfigError, match="MTG_SCRYFALL_LOOKUP_INTERVAL"):
        load_settings()
    monkeypatch.setenv("MTG_SCRYFALL_LOOKUP_INTERVAL", "fast")
    with pytest.raises(ConfigError, match="MTG_SCRYFALL_LOOKUP_INTERVAL"):
        load_settings()


def test_load_settings_scan_thresholds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _write_secrets(tmp_path, monkeypatch)
    s = load_settings()
    assert (s.scan_fuzzy_min_similarity, s.scan_ambiguity_margin) == (0.65, 0.05)
    assert (s.scan_fuzzy_confident_similarity, s.scan_auto_add_confidence) == (0.8, 80.0)
    assert (s.scan_glare_ratio, s.scan_min_ocr_confidence) == (0.08, 50.0)
    monkeypatch.setenv("MTG_SCAN_FUZZY_MIN_SIMILARITY", "0.8")
    monkeypatch.setenv("MTG_SCAN_AUTO_ADD_CONFIDENCE", "90")
    monkeypatch.setenv("MTG_SCAN_GLARE_RATIO", "0.2")
    monkeypatch.setenv("MTG_SCAN_MIN_OCR_CONFIDENCE", "35")
    s = load_settings()
    assert (s.scan_fuzzy_min_similarity, s.scan_auto_add_confidence) == (0.8, 90.0)
    assert (s.scan_glare_ratio, s.scan_min_ocr_confidence) == (0.2, 35.0)
    monkeypatch.setenv("MTG_SCAN_MIN_OCR_CONFIDENCE", "120")
    with pytest.raises(ConfigError, match="MTG_SCAN_MIN_OCR_CONFIDENCE"):
        load_settings()
    monkeypatch.delenv("MTG_SCAN_MIN_OCR_CONFIDENCE")
    monkeypatch.setenv("MTG_SCAN_FUZZY_MIN_SIMILARITY", "0.1")
    with pytest.raises(ConfigError, match="MTG_SCAN_FUZZY_MIN_SIMILARITY"):
        load_settings()


def test_backup_is_owner_only_even_with_a_loose_umask(tmp_path: Path):
    db = Database(tmp_path / "d" / "g.sqlite")
    old = os.umask(0o022)  # what `docker exec ... mtg-gateway backup` runs with
    try:
        out = export_now(db, tmp_path / "b", keep_days=14)
    finally:
        os.umask(old)
    assert out.stat().st_mode & 0o777 == 0o600
    db.close()


def test_backup_copy_never_writes_through_a_link_at_its_temporary_name(tmp_path: Path, monkeypatch):
    from mtg_gateway import backup as backup_module

    src = tmp_path / "mtg-gateway-20260101T000000Z.sqlite"
    src.write_bytes(b"backup")
    victim = tmp_path / "victim"
    victim.write_bytes(b"keep me")
    copy_dir = tmp_path / "copies"
    copy_dir.mkdir()
    real_open = os.open

    def open_then_swap(path, flags, mode=0o777):
        fd = real_open(path, flags, mode)
        # someone with write access to a shared copy folder swaps a link in at the temporary name
        if str(path).endswith(".tmp"):
            Path(path).unlink()
            Path(path).symlink_to(victim)
        return fd

    monkeypatch.setattr(backup_module.os, "open", open_then_swap)
    with pytest.raises(OSError):
        backup_module.copy_backup(src, copy_dir, 30)
    assert victim.read_bytes() == b"keep me"
    assert not (copy_dir / src.name).exists()  # no link left behind as a backup


async def test_health_probes_mystic_forge_once_for_many_callers():
    import contextlib

    from mtg_gateway.mf_proxy import MysticForgeProxy

    probes = []

    class Client:
        async def list_tools(self):
            await asyncio.sleep(0.05)

    @contextlib.asynccontextmanager
    async def factory():
        probes.append(1)
        yield Client()

    proxy = MysticForgeProxy("http://mf.test/mcp", client_factory=factory)
    results = await asyncio.gather(*[proxy.healthy() for _ in range(20)])
    assert all(results) and len(probes) == 1


async def test_health_retries_a_failed_probe_sooner_than_a_good_one(monkeypatch):
    """Mystic Forge starts after the gateway: a "down" answer is held for a few seconds only,
    an "ok" answer for the usual half minute."""
    import contextlib

    from mtg_gateway import mf_proxy as mod

    up = {"x": False}
    probes = []

    class Client:
        async def list_tools(self):
            if not up["x"]:
                raise ConnectionError("not yet")

    @contextlib.asynccontextmanager
    async def factory():
        probes.append(1)
        yield Client()

    monkeypatch.setattr(mod, "HEALTH_DOWN_CACHE_SECONDS", 0.05)
    proxy = mod.MysticForgeProxy("http://mf.test/mcp", client_factory=factory)
    assert await proxy.healthy() is False and await proxy.healthy() is False and len(probes) == 1
    up["x"] = True
    await asyncio.sleep(0.06)
    assert await proxy.healthy() is True and len(probes) == 2  # probed again once the short hold passed
    up["x"] = False
    assert await proxy.healthy() is True and len(probes) == 2  # a good answer is held the full window


def test_backup_is_written_in_a_private_folder_and_leaves_nothing_behind(tmp_path: Path, monkeypatch):
    from mtg_gateway import backup as backup_module

    db = Database(tmp_path / "gw.sqlite")
    seen: list[Path] = []
    real = db.backup_to

    def spy(target: Path) -> None:
        seen.append(target)
        real(target)

    monkeypatch.setattr(db, "backup_to", spy)
    backup_dir = tmp_path / "backups"
    dest = backup_module.export_now(db, backup_dir, 30)
    # SQLite wrote inside a fresh owner-only folder, where nobody else can swap a link in
    assert seen[0].parent != backup_dir and seen[0].parent.parent == backup_dir
    assert dest.exists() and oct(dest.stat().st_mode & 0o777) == "0o600"
    assert [p.name for p in backup_dir.iterdir()] == [dest.name]
    db.close()


def test_stale_work_folders_from_a_crashed_backup_are_swept(tmp_path: Path, caplog):
    """A backup killed mid-write leaves its private `.backup-*` folder behind; the next backup
    removes such folders (older than an hour) with the partial copy inside, leaves a young one
    (another backup may still be writing in it), and never follows a link by that name."""
    db = Database(tmp_path / "g.sqlite")
    b = tmp_path / "b"
    b.mkdir()
    stale = b / ".backup-old"
    stale.mkdir(mode=0o700)
    (stale / "mtg-gateway-partial.sqlite").write_bytes(b"partial")
    old = time.time() - 7200
    os.utime(stale, (old, old))
    young = b / ".backup-young"
    young.mkdir(mode=0o700)
    (young / "mtg-gateway-partial.sqlite").write_bytes(b"partial")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "keep.txt").write_text("keep")
    link = b / ".backup-link"
    link.symlink_to(elsewhere, target_is_directory=True)
    os.utime(link, (old, old), follow_symlinks=False)
    with caplog.at_level("INFO", logger="mtg_gateway.backup"):
        out = export_now(db, b, keep_days=14)
    assert out.exists() and not stale.exists() and young.exists()
    assert (elsewhere / "keep.txt").exists() and link.is_symlink()
    assert sorted(p.name for p in b.iterdir() if not p.name.startswith(".backup-")) == [out.name]
    assert sweep_work_dirs(b, older_than=0) == 1 and not young.exists()  # the young one, when old enough
