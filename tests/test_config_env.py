"""Start-up checks of the stack's on/off variables and of variables the gateway no longer reads
(0.7.46 audit PA-16, PA-10): a typo in an on/off value (MTG_WRITES_ENABLED=ture) started the
gateway with that feature silently off, and a removed variable left in a stack was silently
ignored."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from mtg_gateway.config import BOOL_ENV, RETIRED_ENV, ConfigError, load_settings

from .test_backup_and_config import _write_secrets


@pytest.mark.parametrize("name", BOOL_ENV)
def test_an_on_off_variable_with_a_typo_stops_start_up_and_names_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    _write_secrets(tmp_path, monkeypatch)
    monkeypatch.setenv(name, "ture")
    with pytest.raises(ConfigError) as err:
        load_settings()
    text = str(err.value)
    assert name in text and "'ture'" in text and "true or false" in text, text


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("true", True), ("TRUE", True), ("1", True), ("yes", True), ("on", True), (" On ", True),
        ("false", False), ("False", False), ("0", False), ("no", False), ("off", False),
    ],
)  # fmt: skip
def test_every_usual_spelling_of_on_and_off_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str, expected: bool
) -> None:
    _write_secrets(tmp_path, monkeypatch)
    monkeypatch.setenv("MTG_WRITES_ENABLED", value)
    assert load_settings().writes_enabled is expected


def test_an_empty_on_off_variable_keeps_its_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The stack files pass most switches empty when the person leaves them out."""
    _write_secrets(tmp_path, monkeypatch)
    monkeypatch.setenv("MTG_WATCHLISTS_ENABLED", "")
    monkeypatch.setenv("MTG_WRITES_ENABLED", "  ")
    s = load_settings()
    assert s.watchlists_enabled is True and s.writes_enabled is True


def test_the_any_user_switch_is_checked_too(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """MTG_ALLOW_ANY_IDP_USER=ture with no group used to stop start-up with a message about the
    group; it now names the typo."""
    _write_secrets(tmp_path, monkeypatch)
    monkeypatch.setenv("MTG_REQUIRED_GROUP", "")
    monkeypatch.setenv("MTG_ALLOW_ANY_IDP_USER", "ture")
    with pytest.raises(ConfigError) as err:
        load_settings()
    assert "MTG_ALLOW_ANY_IDP_USER" in str(err.value) and "'ture'" in str(err.value)


@pytest.mark.parametrize("name", ["MTG_FORGE_GAMES", "MTG_BACKUP_KEEP_DAYS", "MTG_FORGE_PARALLEL_GAMES"])
def test_a_retired_variable_with_a_value_is_warned_about_at_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, name: str
) -> None:
    _write_secrets(tmp_path, monkeypatch)
    monkeypatch.setenv(name, "500")
    with caplog.at_level(logging.DEBUG, logger="mtg_gateway.config"):
        load_settings()
    warned = [r for r in caplog.records if name in r.getMessage()]
    assert len(warned) == 1 and warned[0].levelno == logging.WARNING, caplog.records
    assert "Settings page" in warned[0].getMessage(), warned[0].getMessage()


def test_every_retired_variable_is_warned_about_and_none_is_still_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The list is every MTG_ variable an earlier release read and this one does not: none of them
    is named anywhere in the gateway's code but the list itself."""
    root = Path(__file__).resolve().parents[1] / "src" / "mtg_gateway"
    code = (
        "\n".join(p.read_text(encoding="utf-8") for p in root.rglob("*.py") if p.name != "config.py")
        + (root / "config.py").read_text(encoding="utf-8").split("RETIRED_ENV = (", 1)[0]
    )
    for name in RETIRED_ENV:
        assert name not in code or name == "MTG_REAUTH_INTERVAL", name
    _write_secrets(tmp_path, monkeypatch)
    for name in RETIRED_ENV:
        monkeypatch.setenv(name, "1")
    with caplog.at_level(logging.DEBUG, logger="mtg_gateway.config"):
        load_settings()
    said = " ".join(r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)
    for name in RETIRED_ENV:
        assert name in said, name
