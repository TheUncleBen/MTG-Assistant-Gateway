"""Provider-agnostic OIDC options: the groups-claim path and the token-endpoint auth method."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from mtg_gateway.config import ConfigError, load_settings
from mtg_gateway.oidc import (
    MAX_GROUP_LEN,
    MAX_GROUPS,
    OIDCClient,
    basic_auth_header,
    resolve_groups,
    validate_groups_claim,
)
from tests.conftest import CLIENT_ID, CLIENT_SECRET, IDP, FakeIdP, Harness, make_settings, running

# --- resolve_groups: every rule, fail-closed ---------------------------------------------------


def test_resolve_flat_list_default_claim():
    assert resolve_groups({"groups": ["a", "b"]}, "groups") == ["a", "b"]


def test_resolve_missing_path_is_empty_not_error():
    assert resolve_groups({}, "groups") == []
    assert resolve_groups({"other": ["a"]}, "groups") == []
    assert resolve_groups({"realm_access": {}}, "realm_access.roles") == []
    assert resolve_groups({"realm_access": {"roles": ["x"]}}, "realm_access.roles.deeper") == []


def test_resolve_non_dict_on_the_way_is_empty():
    assert resolve_groups({"realm_access": ["roles"]}, "realm_access.roles") == []
    assert resolve_groups({"realm_access": "roles"}, "realm_access.roles") == []
    assert resolve_groups({"realm_access": None}, "realm_access.roles") == []
    assert resolve_groups(["groups"], "groups") == []
    assert resolve_groups(None, "groups") == []


def test_resolve_nested_path_keycloak_shape():
    claims = {"realm_access": {"roles": ["offline_access", "mtg-users"]}}
    assert resolve_groups(claims, "realm_access.roles") == ["offline_access", "mtg-users"]


def test_resolve_dict_keys_are_group_names_zitadel_shape():
    claims = {"urn:zitadel:iam:org:project:roles": {"mtg-users": {"123": "org.example"}, "admin": {}}}
    # a role whose value is empty (no org grants it) is not a membership
    assert resolve_groups(claims, "urn:zitadel:iam:org:project:roles") == ["mtg-users"]


def test_resolve_single_string_becomes_one_item_list():
    assert resolve_groups({"groups": "mtg-users"}, "groups") == ["mtg-users"]
    assert resolve_groups({"groups": "  "}, "groups") == []


def test_resolve_list_drops_non_strings():
    assert resolve_groups({"groups": [1, None, "a", {"b": 1}, ["c"], "d", True]}, "groups") == ["a", "d"]


def test_resolve_wrong_value_types_are_empty():
    for bad in (42, 1.5, True, None):
        assert resolve_groups({"groups": bad}, "groups") == []


def test_resolve_drops_empties_and_dedupes_in_order_without_trimming():
    claims = {"groups": [" b ", "", "a", "b", "   ", "a ", "c", "a"]}
    assert resolve_groups(claims, "groups") == [" b ", "a", "b", "a ", "c"]


def test_resolve_caps_count_and_length():
    many = [f"g{i}" for i in range(MAX_GROUPS + 50)]
    out = resolve_groups({"groups": many}, "groups")
    assert len(out) == MAX_GROUPS and out[0] == "g0" and out[-1] == f"g{MAX_GROUPS - 1}"
    # Over-long names are dropped, not truncated: a truncated name must never match a real one.
    long = "x" * (MAX_GROUP_LEN + 1)
    ok = "y" * MAX_GROUP_LEN
    assert resolve_groups({"groups": [long, ok]}, "groups") == [ok]
    # Dropped entries do not count towards the cap.
    assert len(resolve_groups({"groups": [long] * 10 + many}, "groups")) == MAX_GROUPS


def test_resolve_literal_claim_wins_over_the_dot_path():
    # A claim named exactly "a.b" is read as that key; the dot-path walk is the fallback.
    assert resolve_groups({"a.b": ["x"]}, "a.b") == ["x"]
    assert resolve_groups({"a.b": ["x"], "a": {"b": ["y"]}}, "a.b") == ["x"]
    assert resolve_groups({"a": {"b": ["y"]}}, "a.b") == ["y"]


# --- validate_groups_claim and settings --------------------------------------------------------


@pytest.mark.parametrize("good", ["groups", "realm_access.roles", "urn:zitadel:iam:org:project:roles"])
def test_validate_groups_claim_accepts(good: str):
    assert validate_groups_claim(good) == good


@pytest.mark.parametrize("bad", ["", "   ", ".groups", "groups.", "a..b", " groups", "x" * 201])
def test_validate_groups_claim_rejects(bad: str):
    with pytest.raises(ValueError):
        validate_groups_claim(bad)


def test_oidc_client_rejects_bad_options():
    with pytest.raises(ValueError):
        OIDCClient(IDP, CLIENT_ID, CLIENT_SECRET, "https://x/cb", "openid", groups_claim="a..b")
    with pytest.raises(ValueError, match="token_auth_method"):
        OIDCClient(IDP, CLIENT_ID, CLIENT_SECRET, "https://x/cb", "openid", token_auth_method="none")
    c = OIDCClient(IDP, CLIENT_ID, CLIENT_SECRET, "https://x/cb", "openid")
    assert c.groups_claim == "groups" and c.token_auth_method == "client_secret_post"


def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "fernet").write_text(Fernet.generate_key().decode())
    (tmp_path / "session").write_text("x" * 40)
    (tmp_path / "oidc").write_text("client-secret")
    monkeypatch.setenv("MTG_FERNET_KEY_FILE", str(tmp_path / "fernet"))
    monkeypatch.setenv("MTG_SESSION_SECRET_FILE", str(tmp_path / "session"))
    monkeypatch.setenv("MTG_OIDC_CLIENT_SECRET_FILE", str(tmp_path / "oidc"))
    monkeypatch.setenv("MTG_PUBLIC_URL", "https://mtg.example.test")
    monkeypatch.setenv("MTG_OIDC_ISSUER", "https://auth.example.test/realms/mtg")
    monkeypatch.setenv("MTG_OIDC_CLIENT_ID", "abc")
    monkeypatch.setenv("MTG_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MTG_REQUIRED_GROUP", "mtg-users")


def test_settings_defaults_and_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _env(tmp_path, monkeypatch)
    s = load_settings()
    assert s.oidc_groups_claim == "groups" and s.oidc_token_auth_method == "client_secret_post"
    monkeypatch.setenv("MTG_OIDC_GROUPS_CLAIM", "realm_access.roles")
    monkeypatch.setenv("MTG_OIDC_TOKEN_AUTH_METHOD", "client_secret_basic")
    s = load_settings()
    assert s.oidc_groups_claim == "realm_access.roles"
    assert s.oidc_token_auth_method == "client_secret_basic"


@pytest.mark.parametrize("bad", [".groups", "groups.", "a..b", " groups", "x" * 201])
def test_settings_rejects_bad_groups_claim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad: str):
    _env(tmp_path, monkeypatch)
    monkeypatch.setenv("MTG_OIDC_GROUPS_CLAIM", bad)
    with pytest.raises(ConfigError, match="MTG_OIDC_GROUPS_CLAIM"):
        load_settings()


@pytest.mark.parametrize("bad", ["none", "private_key_jwt", "CLIENT_SECRET_POST", "basic"])
def test_settings_rejects_bad_token_auth_method(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad: str):
    _env(tmp_path, monkeypatch)
    monkeypatch.setenv("MTG_OIDC_TOKEN_AUTH_METHOD", bad)
    with pytest.raises(ConfigError, match="MTG_OIDC_TOKEN_AUTH_METHOD"):
        load_settings()


# --- end to end through the gateway ------------------------------------------------------------


def _nest_groups(idp: FakeIdP) -> None:
    """Make the fake IdP mint Keycloak-shaped tokens: groups only under realm_access.roles."""
    idp.id_token_omit = {"groups"}
    idp.id_token_claims = {"realm_access": {"roles": ["offline_access", *idp.user["groups"]]}}  # type: ignore[misc]


async def test_nested_groups_claim_passes_required_group(tmp_path: Path, idp: FakeIdP):
    _nest_groups(idp)
    settings = make_settings(tmp_path, required_group="mtg-users", oidc_groups_claim="realm_access.roles")
    async with running(Harness(settings, idp)) as h:
        h.oidc.groups_claim = settings.oidc_groups_claim
        client = await h.register()
        tokens = await h.tokens_for(client)
        assert tokens["access_token"]
        assert h.db.get_user("user-1")["groups"] == ["offline_access", "mtg-users"]


async def test_wrong_groups_claim_path_is_refused(tmp_path: Path, idp: FakeIdP):
    _nest_groups(idp)
    settings = make_settings(tmp_path, required_group="mtg-users", oidc_groups_claim="realm_access.groups")
    async with running(Harness(settings, idp)) as h:
        h.oidc.groups_claim = settings.oidc_groups_claim
        client = await h.register()
        r, _ = await h.start_login(client)
        cb = await h.idp_leg(r)
        r2 = await h.callback(cb)
        assert r2.status_code == 403
        # The default path also fails closed: userinfo still has top-level "groups" but the
        # configured path is what is read, so nothing is granted by accident.
        assert h.db.get_user("user-1") is None


async def test_default_claim_still_refuses_when_token_lacks_it(tmp_path: Path, idp: FakeIdP):
    # Groups moved under a nested key, gateway left on the default "groups" path: the ID token
    # has none and the userinfo fallback (top-level groups) is what lets the user in.
    _nest_groups(idp)
    settings = make_settings(tmp_path, required_group="mtg-users")
    async with running(Harness(settings, idp)) as h:
        client = await h.register()
        await h.tokens_for(client)
        assert h.db.get_user("user-1")["groups"] == ["mtg-users"]


# --- client_secret_basic -----------------------------------------------------------------------


def test_basic_auth_header_percent_encodes_per_rfc6749():
    header = basic_auth_header("my client/id", "s3cr=t:&%")
    assert header.startswith("Basic ")
    decoded = base64.b64decode(header[6:]).decode("ascii")
    assert decoded == "my%20client%2Fid:s3cr%3Dt%3A%26%25"


async def test_token_exchange_with_client_secret_basic(tmp_path: Path, idp: FakeIdP):
    settings = make_settings(tmp_path, oidc_token_auth_method="client_secret_basic")
    async with running(Harness(settings, idp)) as h:
        h.oidc.token_auth_method = settings.oidc_token_auth_method
        client = await h.register()
        tokens = await h.tokens_for(client)
        assert tokens["access_token"]
    call = idp.token_calls[-1]
    assert call["_auth_method"] == "client_secret_basic"
    assert "client_secret" not in call
    assert call["code_verifier"] and call["grant_type"] == "authorization_code"


async def test_token_exchange_default_is_client_secret_post(gw: Harness, idp: FakeIdP):
    client = await gw.register()
    await gw.tokens_for(client)
    call = idp.token_calls[-1]
    assert call["_auth_method"] == "client_secret_post"
    assert call["client_id"] == CLIENT_ID and call["client_secret"] == CLIENT_SECRET


def test_resolve_compares_names_exactly_without_trimming():
    # " example-admins " is not the admin group; a required group can never match it
    assert resolve_groups({"groups": [" example-admins ", "example-admins"]}, "groups") == [
        " example-admins ",
        "example-admins",
    ]
    assert resolve_groups({"groups": ["", "  ", "\t"]}, "groups") == []


def test_resolve_dict_claim_counts_only_truthy_values():
    claims = {"groups": {"example-admins": False, "example-users": True, "nulled": None, "zero": 0}}
    assert resolve_groups(claims, "groups") == ["example-users"]
    zitadel = {"urn:zitadel:iam:org:project:roles": {"mtg-users": {"1": "org.example"}, "admin": {}}}
    assert resolve_groups(zitadel, "urn:zitadel:iam:org:project:roles") == ["mtg-users"]


def test_resolve_tries_the_whole_path_as_a_literal_claim_first():
    claims = {"https://example.com/groups": ["example-users"], "https://example": {"com/groups": ["x"]}}
    assert resolve_groups(claims, "https://example.com/groups") == ["example-users"]
    # the dot-path walk still works when no literal key matches
    assert resolve_groups({"realm_access": {"roles": ["a"]}}, "realm_access.roles") == ["a"]
