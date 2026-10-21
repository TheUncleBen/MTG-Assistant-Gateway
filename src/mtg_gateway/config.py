"""Configuration from environment variables and Docker-secret files.

Nothing site-specific is hard-coded. Every deployment value comes from the
environment; every secret comes from a file (Docker Swarm mounts secrets under
/run/secrets/<name>).
"""

from __future__ import annotations

import ipaddress
import json
import logging
import math
import os
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from cryptography.fernet import Fernet

from .modes import MODE_LABELS
from .oidc import TOKEN_AUTH_METHODS, validate_groups_claim
from .plugin_page import CHATGPT_NAME_MAX, chatgpt_name_from_setting, chatgpt_name_too_long


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


def _env(name: str, default: str | None = None, *, required: bool = False) -> str | None:
    value = os.environ.get(name)
    if value is not None and name in _managed_envs():
        # A setting the Settings page manages: a value of only spaces is unset, as for its lock
        # (env_set), and spaces around a value are dropped.
        value = value.strip()
    if value is None or value == "":
        if required and default is None:
            raise ConfigError(f"{name} is required")
        return default
    return value


def _managed_envs() -> frozenset[str]:
    return frozenset(spec.env for spec in MANAGED_SETTINGS.values())


def _read_secret(env_name: str, *, required: bool = True) -> str | None:
    """Read a secret from the file named by ``env_name``.

    ``env_name`` holds a path (for example ``/run/secrets/mtg_fernet_key``). The
    value itself is never read from the environment, so it does not show up in
    ``docker inspect`` or in Portainer's stack view.
    """
    path = _env(env_name)
    if not path:
        if required:
            raise ConfigError(f"{env_name} must point at a secret file")
        return None
    try:
        value = Path(path).read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ConfigError(f"cannot read secret file for {env_name}: {exc}") from exc
    if not value:
        raise ConfigError(f"secret file for {env_name} is empty")
    return value


def _authentik_api_url(issuer: str) -> str | None:
    """MTG_AUTHENTIK_API_URL, or the scheme and host of the issuer (Authentik serves its API on
    the same address as its OpenID endpoints)."""
    raw = (_env("MTG_AUTHENTIK_API_URL", "") or "").strip().rstrip("/")
    if raw:
        if not raw.startswith("https://"):
            raise ConfigError("MTG_AUTHENTIK_API_URL must be an https URL such as https://auth.example.com")
        return raw
    parsed = urlparse(issuer)
    return f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else None


def _ntfy_url_env(name: str) -> str | None:
    """The ntfy server's address (https, or http for a server on the same private network)."""
    raw = (_env(name, "") or "").strip().rstrip("/")
    if not raw:
        return None
    parsed = urlparse(raw)
    if parsed.scheme not in ("https", "http") or not parsed.netloc or parsed.query or parsed.fragment:
        raise ConfigError(f"{name} must be the ntfy server's address, such as https://ntfy.example.com")
    if parsed.username or parsed.password:
        # members see this address on their Account page; the token goes in its Docker secret
        raise ConfigError(f"{name} must not hold a user name or password; use the mtg_ntfy_token secret")
    return raw


def _chatgpt_name_env(name: str) -> str | None:
    """The ChatGPT connector name, when it should differ from MTG_SERVER_NAME. A long one is
    warned about (chatgpt_name_warning), never refused: a stopped gateway locks everyone out."""
    raw = (_env(name, "") or "").strip()
    return raw or None


def chatgpt_name_warning(settings: Settings) -> str | None:
    """The startup log warning when the ChatGPT connector name is over the plugin file's limit,
    else None: the same test the admin page and the install page's plugin-file step use, with the
    setting the name came from."""
    long = chatgpt_name_too_long(settings.server_name, settings.chatgpt_name)
    if not long:
        return None
    if chatgpt_name_from_setting(settings.chatgpt_name):
        return (
            f"MTG_CHATGPT_NAME is {long[1]} characters; ChatGPT refuses a plugin file whose name is over "
            f"{CHATGPT_NAME_MAX} characters. The gateway runs, but set a shorter name, or leave it empty "
            f"to use MTG_SERVER_NAME if that is {CHATGPT_NAME_MAX} characters or fewer."
        )
    return (
        f"MTG_SERVER_NAME is {long[1]} characters and MTG_CHATGPT_NAME is empty, so the ChatGPT connector "
        f"name is too long: ChatGPT refuses a plugin file whose name is over {CHATGPT_NAME_MAX} characters. "
        f"The gateway runs, but set MTG_CHATGPT_NAME to a name of {CHATGPT_NAME_MAX} characters or fewer."
    )


def _bool_env(name: str, default: bool) -> bool:
    """1/true/yes (any case) is True, anything else False; unset or empty is ``default``."""
    raw = _env(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes")


def _assetlinks_env(name: str) -> str | None:
    """The Android App Links statement list served at /.well-known/assetlinks.json: a JSON list,
    returned re-serialised, or None when unset."""
    raw = _env(name)
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a JSON list (the assetlinks.json statement list)") from exc
    if not isinstance(parsed, list):
        raise ConfigError(f"{name} must be a JSON list (the assetlinks.json statement list)")
    return json.dumps(parsed, separators=(",", ":"))


_PACKAGE_ID = re.compile(r"[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+")


def _package_env(name: str, default: str) -> str:
    """An Android package ID (``com.example.app``): the app the browser sign-in hands back to."""
    raw = _env(name, default) or default
    if len(raw) > 200 or not _PACKAGE_ID.fullmatch(raw):
        raise ConfigError(f"{name} must be an Android package ID such as local.mtgassistantgateway.app")
    return raw


def _groups_claim_env(name: str, default: str) -> str:
    """Dot-path into the ID token claims naming the group list (see oidc.validate_groups_claim)."""
    raw = _env(name, default) or default
    try:
        return validate_groups_claim(raw)
    except ValueError as exc:
        raise ConfigError(f"{name}: {exc} (a dot-path such as groups or realm_access.roles)") from exc


def _token_auth_method_env(name: str, default: str) -> str:
    raw = (_env(name, default) or default).strip()
    if raw not in TOKEN_AUTH_METHODS:
        raise ConfigError(f"{name} must be one of {', '.join(TOKEN_AUTH_METHODS)}")
    return raw


DEFAULT_OIDC_SCOPES = "openid profile email offline_access"


def _mode_env(name: str, default: str) -> str:
    """An approval mode (manual, semi or auto; see modes.py)."""
    raw = (_env(name, "") or "").strip().lower()
    if not raw:
        return default
    if raw not in ("manual", "semi", "auto"):
        raise ConfigError(f"{name} must be manual, semi or auto, not {raw!r}")
    return raw


def _int_env(name: str, default: int, *, lo: int, hi: int) -> int:
    raw = _env(name, str(default)) or str(default)
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc
    if not lo <= value <= hi:
        raise ConfigError(f"{name} must be between {lo} and {hi}")
    return value


def _float_env(name: str, default: float, *, lo: float, hi: float, seconds: bool = False) -> float:
    raw = _env(name, str(default)) or str(default)
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be {'a number of seconds' if seconds else 'a number'}") from exc
    if not lo <= value <= hi:
        raise ConfigError(f"{name} must be between {lo} and {hi}")
    return value


@dataclass(frozen=True)
class Settings:
    public_url: str
    oidc_issuer: str
    oidc_client_id: str
    # The three secrets are left out of repr(), so a settings object printed or logged by mistake
    # cannot show them.
    oidc_client_secret: str = field(repr=False)
    oidc_scopes: str
    required_group: str | None
    session_secret: str = field(repr=False)
    fernet_key: str = field(repr=False)
    data_dir: Path
    backup_dir: Path | None
    backup_hour_utc: int
    backup_keep_days: int
    allowed_hosts: list[str] = field(default_factory=list)
    # Peers whose X-Forwarded-* headers are believed (the reverse proxy). Default: private
    # networks only, which covers the Docker overlay and bridge ranges the proxy lives on.
    trusted_proxies: list[str] = field(
        default_factory=lambda: ["127.0.0.1", "::1", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]
    )
    # Members of this identity-provider group see /admin; unset, the admin pages do not exist.
    admin_group: str | None = None
    # Dot-path to the group list in the ID token / userinfo claims (Authentik: groups,
    # Keycloak: realm_access.roles, Zitadel: urn:zitadel:iam:org:project:roles).
    oidc_groups_claim: str = "groups"
    # How the gateway authenticates at the token endpoint: client_secret_post or client_secret_basic.
    oidc_token_auth_method: str = "client_secret_post"
    # JSON statement list for /.well-known/assetlinks.json (Android App Links); unset: not served.
    android_assetlinks: str | None = None
    # The Android app's package ID: the app the browser sign-in hands back to (app_signin.py).
    android_package: str = "local.mtgassistantgateway.app"
    mystic_forge_url: str | None = None
    # The Forge simulation service (docker/forge/); unset, reports run the goldfish only.
    forge_url: str | None = None
    # Games per Forge run: four-player Commander games are slow on a Pi, so runs stay short.
    forge_games: int = 4
    # Forge games a report asks to play at the same time (1 to 16); the Forge service plays as many as
    # its machine has places for. The default asks for as many as a report has games by default.
    forge_parallel_games: int = 4
    cimd_enabled: bool = True
    cimd_allowed_hosts: list[str] = field(default_factory=list)
    writes_enabled: bool = False
    # Automatic Archidekt -> gateway deck sync (sync.py): minutes between the timed checks of
    # members with waiting proposals; a signed-in page also starts one. 0 turns the sync off.
    deck_sync_minutes: int = 30
    # The Approve/Reject card an AI app shows in the chat (MCP Apps) may apply a proposal with
    # its one-time code; false leaves only the review page.
    apply_in_chat: bool = True
    # Look up the newest release on GitHub twice a day and tell admins when it is newer than this
    # gateway (update_check.py). Notice only: nothing is downloaded or changed.
    update_check: bool = True
    # Approval modes (modes.py): the mode of a member who has not chosen one on their Account
    # page, the highest mode members may choose (auto = no cap), and how many review rows an
    # edit may have and still count as low risk in semi mode.
    approval_mode_default: str = "manual"
    approval_mode_max: str = "auto"
    auto_apply_max_rows: int = 5
    # Auto mode: an edit changing more than this many rows is a big edit and waits for the
    # member's press on the chat card. Read through LiveSettings.
    auto_big_edit_rows: int = 20
    # How many days a waiting change waits before it expires. Read through
    # LiveSettings; proposal_ttl below is what proposals use until the approvals work switches.
    proposal_expiry_days: int = 7
    archidekt_base: str = "https://archidekt.com/api"
    archidekt_backups: bool = True
    # Archidekt backup copies kept per deck and local snapshots kept per deck (25 is
    # db.SNAPSHOTS_KEEP_PER_DECK). Read through LiveSettings.
    archidekt_backup_copies: int = 10
    # the deck editor's "Also keep a backup copy on Archidekt" box starts ticked (off)
    archidekt_backup_box_default: bool = False
    snapshots_per_deck: int = 25
    # A second folder every database backup is also copied to (another disk or a network share).
    backup_copy_dir: Path | None = None
    archidekt_backup_folder: str = "MTG Gateway backups"
    archidekt_user_agent: str = (
        "mtg-assistant-gateway/0.1 (+https://github.com/TheUncleBen/MTG-Assistant-Gateway)"
    )
    # Gentle on Archidekt (docs/DEPLOY.md "Archidekt request limits"): every request, whoever
    # asks, waits at least archidekt_min_interval after the previous one, and no more than
    # archidekt_max_per_minute go out in any 60 seconds.
    archidekt_min_interval: float = 1.0
    archidekt_max_per_minute: int = 30
    # GETs that time out or get a 5xx are retried this many times, after a random wait of up to
    # archidekt_backoff_base * 2**attempt seconds (capped at 10). Writes and 429s are never retried.
    archidekt_retries: int = 2
    archidekt_backoff_base: float = 1.0
    # How long anonymous public deck and search reads, and card catalogue lookups, are reused.
    archidekt_cache_seconds: int = 60
    archidekt_card_cache_seconds: int = 3600
    # Archidekt work one member may start per 10 minutes (decks.RateBudget), proxied archidekt_*
    # research calls included.
    archidekt_calls_per_10_min: int = 120
    scryfall_lookup_interval: float = 0.5  # seconds between single-card Scryfall lookups (scan)
    # Card-scan match thresholds (see scan.service.ScanThresholds).
    scan_fuzzy_min_similarity: float = 0.65
    scan_fuzzy_confident_similarity: float = 0.8
    scan_ambiguity_margin: float = 0.05
    scan_auto_add_confidence: float = 80.0
    scan_foil_star_ink_ratio: float = 0.09
    scan_glare_ratio: float = 0.08
    scan_min_ocr_confidence: float = 50.0
    # Artwork matching for scans (see scan.art.ArtSettings).
    scan_art_enabled: bool = True
    scan_art_max_distance: int = 380
    scan_art_min_margin: int = 40
    scan_art_max_printings: int = 250
    scan_art_image_interval: float = 0.1
    scan_art_images_per_hour: int = 1500
    scan_art_max_galleries: int = 500
    scan_art_gallery_idle_days: int = 180
    proposal_ttl: int = 24 * 3600
    browser_session_ttl: int = 2 * 3600
    access_token_ttl: int = 3600
    refresh_token_ttl: int = 30 * 24 * 3600
    reauth_interval: int = 7 * 24 * 3600
    membership_check_ttl: int = 5
    oidc_previous_issuers: list[str] = field(default_factory=list)
    auth_code_ttl: int = 300
    login_ttl: int = 600
    listen_host: str = "0.0.0.0"
    listen_port: int = 8080
    log_level: str = "INFO"
    server_name: str = "MTG Assistant Gateway"
    # The ChatGPT connector name (its plugin's displayName and the ChatGPT install steps); None =
    # server_name. Over 80 characters it is only warned about.
    chatgpt_name: str | None = None
    # Optional hourly clean-up of removed members' Archidekt sessions (idp_sweep.py): an Authentik
    # API token that may only view the gateway's groups and its own tokens, and the Authentik
    # address (empty = the issuer's).
    authentik_api_token: str | None = field(default=None, repr=False)
    authentik_api_token_problem: str | None = None
    # True when the token file named is not there at all, as when the stack's secret lines are
    # still commented out (the admin page then names that cause).
    authentik_api_token_missing: bool = False
    authentik_api_url: str | None = None
    # Watchlists and price history (prices.py), off unless MTG_WATCHLISTS_ENABLED is true: off, the
    # Watchlist page, its links, the card viewer's price buttons, the five watch tools and the daily
    # price check are gone (stored watches and prices stay in the database). Then how many cards and
    # decks one member may watch, how many days of daily prices are kept, and the hour (UTC) the
    # daily price check and the members' summaries run. Nothing is fetched while nobody watches.
    watchlists_enabled: bool = False
    watch_max_cards: int = 200
    watch_max_decks: int = 10
    price_history_days: int = 365
    price_hour_utc: int = 6
    # Optional notifications through an ntfy server (notify.py): its address and an access token
    # the gateway publishes with. Unset, the gateway sends no notifications.
    ntfy_url: str | None = None
    ntfy_token: str | None = field(default=None, repr=False)
    # The MANAGED_SETTINGS keys the deployment set (a non-empty environment variable): those win
    # over the admin Settings page, which shows them as set by the deployment.
    env_set: frozenset[str] = frozenset()

    def grants_access(self, groups: list[str] | None) -> bool:
        """May someone in ``groups`` use the gateway? Members of MTG_REQUIRED_GROUP may, and so
        may members of MTG_ADMIN_GROUP: an admin needs no second group to sign in. Both are read
        from the identity provider's live answer, so leaving either group is seen the same way."""
        if not self.required_group:
            return True
        held = groups or []
        return self.required_group in held or bool(self.admin_group and self.admin_group in held)

    @property
    def mcp_url(self) -> str:
        return f"{self.public_url}/mcp"

    @property
    def callback_url(self) -> str:
        return f"{self.public_url}/auth/callback"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "mtg-gateway.sqlite"


def load_settings() -> Settings:
    """Build Settings from the environment. Raises ConfigError with a plain message."""
    public_url = (_env("MTG_PUBLIC_URL", required=True) or "").rstrip("/")
    parsed = urlparse(public_url)
    if parsed.scheme != "https" or not parsed.netloc:
        if not (parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1")):
            raise ConfigError("MTG_PUBLIC_URL must be an https URL such as https://mtg.example.com")
    if parsed.path not in ("", "/"):
        raise ConfigError("MTG_PUBLIC_URL must not contain a path; the gateway must sit at the host root")

    oidc_issuer = (_env("MTG_OIDC_ISSUER", required=True) or "").strip()
    if not oidc_issuer.startswith("https://"):
        raise ConfigError("MTG_OIDC_ISSUER must be the https issuer URL shown by your identity provider")

    fernet_key = _read_secret("MTG_FERNET_KEY_FILE") or ""
    try:
        Fernet(fernet_key.encode())
    except (ValueError, TypeError) as exc:
        raise ConfigError(
            "MTG_FERNET_KEY_FILE does not contain a valid Fernet key; generate one with "
            "python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
        ) from exc

    session_secret = _read_secret("MTG_SESSION_SECRET_FILE") or ""
    # Optional: a missing or unreadable token file turns the sweep off (with one warning at start)
    # instead of stopping the gateway.
    sweep_token: str | None = None
    sweep_problem: str | None = None
    sweep_missing = False
    try:
        sweep_token = _read_secret("MTG_AUTHENTIK_API_TOKEN_FILE", required=False)
    except ConfigError as exc:
        sweep_problem = str(exc)
        sweep_missing = isinstance(exc.__cause__, FileNotFoundError)
    # The stack files always attach this secret, so whoever doesn't use the clean-up puts the word
    # "off" in it (a Docker secret can't be empty).
    if sweep_token is not None and sweep_token.lower() == "off":
        sweep_token = None
    # Optional, like the sweep token: the ntfy access token (notify.py). Never logged.
    ntfy_token = _read_secret("MTG_NTFY_TOKEN_FILE", required=False)
    if len(session_secret) < 32:
        raise ConfigError("MTG_SESSION_SECRET_FILE must contain at least 32 characters")

    data_dir = Path(_env("MTG_DATA_DIR", "/data") or "/data")
    backup_raw = _env("MTG_BACKUP_DIR", "")
    backup_dir = Path(backup_raw) if backup_raw else None
    copy_raw = _env("MTG_BACKUP_COPY_DIR", "")
    backup_copy_dir = Path(copy_raw) if copy_raw and backup_dir is not None else None
    if backup_copy_dir is not None and backup_copy_dir.is_char_device():
        # The stack files mount /dev/null there when no copy folder is set (docker stack deploy
        # has no "only when set" syntax for a mount), which means copies are off.
        backup_copy_dir = None

    allowed_hosts_raw = _env("MTG_ALLOWED_HOSTS", "")
    if allowed_hosts_raw:
        allowed_hosts = [h.strip() for h in allowed_hosts_raw.split(",") if h.strip()]
    else:
        host = parsed.hostname or ""
        allowed_hosts = [host, f"{host}:*"]

    trusted_raw = _env("MTG_TRUSTED_PROXIES", "")
    trusted = [t.strip() for t in trusted_raw.split(",") if t.strip()] if trusted_raw else None
    for entry in trusted or []:
        if entry != "*":
            try:
                ipaddress.ip_network(entry, strict=True)  # same parser uvicorn applies to the list
            except ValueError as exc:
                raise ConfigError(
                    f"MTG_TRUSTED_PROXIES entry {entry!r} is not an IP address or CIDR network "
                    "(a network must have its host bits zero, e.g. 192.168.1.0/24)"
                ) from exc

    # Fail closed: with no group, anyone the identity provider lets through gets in (one
    # application binding missed in Authentik opens the gateway to every IdP user). An empty
    # group must be an explicit choice.
    required_group = (_env("MTG_REQUIRED_GROUP", "") or "").strip() or None
    allow_any = (_env("MTG_ALLOW_ANY_IDP_USER", "false") or "false").strip().lower() in ("1", "true", "yes")
    if required_group is None and not allow_any:
        raise ConfigError(
            "MTG_REQUIRED_GROUP is empty: set it to the identity provider group whose members may "
            "use the gateway (e.g. MTG Assistant Gateway Users), or set MTG_ALLOW_ANY_IDP_USER=true to let "
            "every user your identity provider signs in through"
        )

    kwargs: dict[str, Any] = {}
    if trusted is not None:
        kwargs["trusted_proxies"] = trusted

    return Settings(
        public_url=public_url,
        oidc_issuer=oidc_issuer,
        oidc_client_id=_env("MTG_OIDC_CLIENT_ID", required=True) or "",
        oidc_client_secret=_read_secret("MTG_OIDC_CLIENT_SECRET_FILE") or "",
        # offline_access is in the default: the provider's refresh token is what lets the gateway
        # keep asking it whether a member is still allowed in (membership.py). A value set here is
        # used as is, for providers that refuse that scope (members then sign in again whenever
        # the provider's access token runs out).
        oidc_scopes=_env("MTG_OIDC_SCOPES", DEFAULT_OIDC_SCOPES) or DEFAULT_OIDC_SCOPES,
        oidc_previous_issuers=[
            i.strip().rstrip("/")
            for i in (_env("MTG_OIDC_PREVIOUS_ISSUERS", "") or "").split(",")
            if i.strip()
        ],
        required_group=required_group,
        admin_group=_env("MTG_ADMIN_GROUP") or None,
        oidc_groups_claim=_groups_claim_env("MTG_OIDC_GROUPS_CLAIM", "groups"),
        oidc_token_auth_method=_token_auth_method_env("MTG_OIDC_TOKEN_AUTH_METHOD", "client_secret_post"),
        android_assetlinks=_assetlinks_env("MTG_ANDROID_ASSETLINKS"),
        android_package=_package_env("MTG_ANDROID_PACKAGE", "local.mtgassistantgateway.app"),
        session_secret=session_secret,
        fernet_key=fernet_key,
        data_dir=data_dir,
        backup_dir=backup_dir,
        backup_hour_utc=_int_env("MTG_BACKUP_HOUR_UTC", 3, lo=0, hi=23),
        backup_keep_days=_int_env("MTG_BACKUP_KEEP_DAYS", 14, lo=1, hi=3650),
        backup_copy_dir=backup_copy_dir,
        allowed_hosts=allowed_hosts,
        mystic_forge_url=(_env("MTG_MYSTIC_FORGE_URL", "") or None),
        forge_url=(_env("MTG_FORGE_URL", "") or None),
        forge_games=_int_env("MTG_FORGE_GAMES", 4, lo=1, hi=50),
        forge_parallel_games=_int_env("MTG_FORGE_PARALLEL_GAMES", 4, lo=1, hi=16),
        cimd_enabled=_bool_env("MTG_CIMD_ENABLED", True),
        cimd_allowed_hosts=[
            h.strip() for h in (_env("MTG_CIMD_ALLOWED_HOSTS", "") or "").split(",") if h.strip()
        ],
        apply_in_chat=_bool_env("MTG_APPLY_IN_CHAT", True),
        update_check=_bool_env("MTG_UPDATE_CHECK", True),
        approval_mode_default=_mode_env("MTG_APPROVAL_MODE_DEFAULT", "manual"),
        approval_mode_max=_mode_env("MTG_APPROVAL_MODE_MAX", "auto"),
        auto_apply_max_rows=_int_env("MTG_AUTO_APPLY_MAX_ROWS", 5, lo=1, hi=100),
        auto_big_edit_rows=_int_env("MTG_AUTO_BIG_EDIT_ROWS", 20, lo=1, hi=100),
        proposal_expiry_days=_int_env("MTG_PROPOSAL_EXPIRY_DAYS", 7, lo=1, hi=30),
        browser_session_ttl=_int_env("MTG_BROWSER_SESSION_TTL", 2 * 3600, lo=300, hi=86400),
        writes_enabled=_bool_env("MTG_WRITES_ENABLED", False),
        deck_sync_minutes=_int_env("MTG_DECK_SYNC_MINUTES", 30, lo=0, hi=24 * 60),
        archidekt_base=(_env("MTG_ARCHIDEKT_BASE", "https://archidekt.com/api") or "").rstrip("/"),
        archidekt_backups=_bool_env("MTG_ARCHIDEKT_BACKUPS", True),
        archidekt_backup_copies=_int_env("MTG_ARCHIDEKT_BACKUP_COPIES", 10, lo=1, hi=50),
        archidekt_backup_box_default=_bool_env("MTG_ARCHIDEKT_BACKUP_BOX_DEFAULT", False),
        snapshots_per_deck=_int_env("MTG_SNAPSHOTS_PER_DECK", 25, lo=5, hi=100),
        archidekt_calls_per_10_min=_int_env("MTG_ARCHIDEKT_CALLS_PER_10_MIN", 120, lo=10, hi=100_000),
        archidekt_min_interval=_float_env("MTG_ARCHIDEKT_MIN_INTERVAL", 1.0, lo=0.25, hi=10.0, seconds=True),
        archidekt_max_per_minute=_int_env("MTG_ARCHIDEKT_MAX_PER_MINUTE", 30, lo=1, hi=120),
        archidekt_retries=_int_env("MTG_ARCHIDEKT_RETRIES", 2, lo=0, hi=4),
        archidekt_backoff_base=_float_env("MTG_ARCHIDEKT_BACKOFF_BASE", 1.0, lo=0.1, hi=10.0, seconds=True),
        archidekt_cache_seconds=_int_env("MTG_ARCHIDEKT_CACHE_SECONDS", 60, lo=0, hi=900),
        archidekt_card_cache_seconds=_int_env("MTG_ARCHIDEKT_CARD_CACHE_SECONDS", 3600, lo=0, hi=86400),
        archidekt_backup_folder=(_env("MTG_ARCHIDEKT_BACKUP_FOLDER", "MTG Gateway backups") or "").strip()[
            :100
        ]
        or "MTG Gateway backups",
        access_token_ttl=_int_env("MTG_ACCESS_TOKEN_TTL", 3600, lo=60, hi=86400),
        refresh_token_ttl=_int_env("MTG_REFRESH_TOKEN_TTL", 30 * 24 * 3600, lo=3600, hi=365 * 24 * 3600),
        reauth_interval=_int_env("MTG_REAUTH_INTERVAL", 7 * 24 * 3600, lo=3600, hi=365 * 24 * 3600),
        membership_check_ttl=_int_env("MTG_MEMBERSHIP_CHECK_TTL", 5, lo=0, hi=60),
        listen_host=_env("MTG_LISTEN_HOST", "0.0.0.0") or "0.0.0.0",
        listen_port=_int_env("MTG_LISTEN_PORT", 8080, lo=1, hi=65535),
        log_level=(_env("MTG_LOG_LEVEL", "INFO") or "INFO").upper(),
        server_name=_env("MTG_SERVER_NAME", "MTG Assistant Gateway") or "MTG Assistant Gateway",
        chatgpt_name=_chatgpt_name_env("MTG_CHATGPT_NAME"),
        authentik_api_token=sweep_token,
        authentik_api_token_problem=sweep_problem,
        authentik_api_token_missing=sweep_missing,
        authentik_api_url=_authentik_api_url(oidc_issuer),
        watchlists_enabled=_bool_env("MTG_WATCHLISTS_ENABLED", False),
        watch_max_cards=_int_env("MTG_WATCH_MAX_CARDS", 200, lo=0, hi=5000),
        watch_max_decks=_int_env("MTG_WATCH_MAX_DECKS", 10, lo=0, hi=200),
        price_history_days=_int_env("MTG_PRICE_HISTORY_DAYS", 365, lo=30, hi=3650),
        price_hour_utc=_int_env("MTG_PRICE_HOUR_UTC", 6, lo=0, hi=23),
        ntfy_url=_ntfy_url_env("MTG_NTFY_URL"),
        ntfy_token=ntfy_token,
        scryfall_lookup_interval=_float_env(
            "MTG_SCRYFALL_LOOKUP_INTERVAL", 0.5, lo=0.1, hi=5.0, seconds=True
        ),
        scan_fuzzy_min_similarity=_float_env("MTG_SCAN_FUZZY_MIN_SIMILARITY", 0.65, lo=0.3, hi=1.0),
        scan_fuzzy_confident_similarity=_float_env(
            "MTG_SCAN_FUZZY_CONFIDENT_SIMILARITY", 0.8, lo=0.3, hi=1.0
        ),
        scan_ambiguity_margin=_float_env("MTG_SCAN_AMBIGUITY_MARGIN", 0.05, lo=0.0, hi=0.5),
        scan_auto_add_confidence=_float_env("MTG_SCAN_AUTO_ADD_CONFIDENCE", 80.0, lo=0.0, hi=100.0),
        scan_foil_star_ink_ratio=_float_env("MTG_SCAN_FOIL_STAR_INK_RATIO", 0.09, lo=0.01, hi=0.9),
        scan_glare_ratio=_float_env("MTG_SCAN_GLARE_RATIO", 0.08, lo=0.005, hi=0.9),
        scan_min_ocr_confidence=_float_env("MTG_SCAN_MIN_OCR_CONFIDENCE", 50.0, lo=0.0, hi=100.0),
        scan_art_enabled=_bool_env("MTG_SCAN_ART_ENABLED", True),
        scan_art_max_distance=_int_env("MTG_SCAN_ART_MAX_DISTANCE", 380, lo=0, hi=1024),
        scan_art_min_margin=_int_env("MTG_SCAN_ART_MIN_MARGIN", 40, lo=0, hi=1024),
        scan_art_max_printings=_int_env("MTG_SCAN_ART_MAX_PRINTINGS", 250, lo=1, hi=2000),
        scan_art_image_interval=_float_env("MTG_SCAN_ART_IMAGE_INTERVAL", 0.1, lo=0.05, hi=5.0, seconds=True),
        scan_art_images_per_hour=_int_env("MTG_SCAN_ART_IMAGES_PER_HOUR", 1500, lo=1, hi=36000),
        scan_art_max_galleries=_int_env("MTG_SCAN_ART_MAX_GALLERIES", 500, lo=1, hi=100000),
        scan_art_gallery_idle_days=_int_env("MTG_SCAN_ART_GALLERY_IDLE_DAYS", 180, lo=1, hi=3650),
        # set = a value other than blanks; a blank (or only spaces) leaves it to the page
        env_set=frozenset(
            key for key, spec in MANAGED_SETTINGS.items() if (os.environ.get(spec.env) or "").strip()
        ),
        **kwargs,
    )


# -- settings the server owner manages on the admin Settings page (0.7.29) ------------------
#
# The website address, sign-in provider, secrets, ports and paths, image
# versions, sign-in lifetimes and the writes switch stay in the deployment. Every other setting is
# registered here and can be changed on the admin Settings page while the deployment leaves it
# blank; a value the deployment sets wins, and the page shows it as set by the deployment.
# Read a value with LiveSettings.get(key), never from Settings, so a change on the page applies.
# A key is the Settings field it overrides. ``owner`` is the area whose code reads it
# (A approvals, B settings and data, C simulator).

logger = logging.getLogger(__name__)

SETTING_KINDS = ("int", "float", "bool", "choice", "text")


@dataclass(frozen=True)
class SettingSpec:
    key: str
    env: str
    kind: str
    default: Any
    group: str
    owner: str
    lo: float | None = None
    hi: float | None = None
    choices: tuple[str, ...] = ()
    max_len: int = 0  # text: longest value (0 = no text)
    empty_ok: bool = False  # text: "" is allowed (it means "use the other setting", e.g. chatgpt_name)
    zero_ok: bool = False  # number: 0 is allowed below ``lo`` (it means "off", e.g. deck_sync_minutes)
    unit: str = ""  # number: what it counts, for the refusal ("minutes", "days", "seconds")
    # choice: what the page calls each choice (the refusal names these, not the stored values)
    labels: tuple[str, ...] = ()
    # True when the gateway reads it only when it starts (the page says it applies after a restart)
    restart: bool = False

    def parse(self, raw: Any) -> Any:
        """The value ``raw`` (from a form: text; or already typed) as this setting's type, within
        its bounds; SettingError with a plain message otherwise."""
        if self.kind == "bool":
            if isinstance(raw, bool):
                return raw
            text = str(raw).strip().lower()
            if text in ("1", "true", "yes", "on"):
                return True
            if text in ("0", "false", "no", "off"):
                return False
            raise SettingError(self.key, "must be on or off")
        if self.kind == "choice":
            text = str(raw).strip()
            for choice in self.choices:
                if text.lower() == choice.lower():
                    return choice
            names = [f'"{n}"' for n in (self.labels or self.choices)]
            raise SettingError(self.key, "must be one of " + ", ".join(names[:-1]) + " or " + names[-1])
        if self.kind == "text":
            text = " ".join(str(raw).split()) if raw is not None else ""
            if not text and not self.empty_ok:
                raise SettingError(self.key, "must not be empty")
            if len(text) > self.max_len:
                raise SettingError(self.key, f"must be at most {self.max_len} characters")
            return text
        if isinstance(raw, bool):
            raise SettingError(self.key, "must be a number")
        try:
            if self.kind == "int":
                if isinstance(raw, float) and not raw.is_integer():
                    raise ValueError
                value: float = int(str(raw).strip()) if not isinstance(raw, int | float) else int(raw)
            else:
                value = float(str(raw).strip()) if not isinstance(raw, int | float) else float(raw)
                if not math.isfinite(value):
                    raise ValueError
        except ValueError:
            raise SettingError(
                self.key, "must be a whole number" if self.kind == "int" else "must be a number"
            ) from None
        assert self.lo is not None and self.hi is not None
        if value == 0 and self.zero_ok:
            return 0
        if not self.lo <= value <= self.hi:
            span = f"between {_number(self.lo)} and {_number(self.hi)}" + (
                f" {self.unit}" if self.unit else ""
            )
            raise SettingError(self.key, f"must be 0 (off) or {span}" if self.zero_ok else f"must be {span}")
        return value


def _number(value: float) -> str:
    """A bound as the pages write numbers: thousands commas, no ".0" (browse.py's ``{n:,}``)."""
    return f"{int(value):,}" if float(value).is_integer() else f"{value:,}"


# How to free a setting the stack sets, said wherever the Settings page refuses to change it.
STACK_LOCK_HOW = "so it can't be changed here. Change or remove it in your stack, then redeploy."


class SettingError(ValueError):
    """A value the admin Settings page cannot store: the key is locked by the deployment, unknown,
    or the value is out of its bounds."""

    def __init__(self, key: str, message: str):
        super().__init__(f"{key} {message}")
        self.key = key
        self.message = message


def _spec(key: str, env: str, kind: str, default: Any, group: str, owner: str, **more: Any) -> SettingSpec:
    if kind not in SETTING_KINDS:
        raise ValueError(kind)
    return SettingSpec(key=key, env=env, kind=kind, default=default, group=group, owner=owner, **more)


_MODES = ("manual", "semi", "auto")
_MODE_NAMES = tuple(MODE_LABELS[m] for m in _MODES)  # the Account page's names (modes.py)
_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

# Bounds are what the dashboard may store; a deployment value is checked by load_settings instead.
# They sit inside load_settings' ranges. Pacing towards Archidekt and Scryfall can only be made
# gentler than today's defaults from the page, never faster ("be gentle on Archidekt's servers");
# the same goes for anything else that adds background reads (deck sync, watchlist sizes).
MANAGED_SETTINGS: dict[str, SettingSpec] = {
    s.key: s
    for s in (
        # approvals
        _spec(
            "approval_mode_default",
            "MTG_APPROVAL_MODE_DEFAULT",
            "choice",
            "manual",
            "approvals",
            "A",
            choices=_MODES,
            labels=_MODE_NAMES,
        ),
        _spec(
            "approval_mode_max",
            "MTG_APPROVAL_MODE_MAX",
            "choice",
            "auto",
            "approvals",
            "A",
            choices=_MODES,
            labels=_MODE_NAMES,
        ),
        _spec("auto_apply_max_rows", "MTG_AUTO_APPLY_MAX_ROWS", "int", 5, "approvals", "A", lo=1, hi=100),
        _spec("auto_big_edit_rows", "MTG_AUTO_BIG_EDIT_ROWS", "int", 20, "approvals", "A", lo=1, hi=100),
        _spec("proposal_expiry_days", "MTG_PROPOSAL_EXPIRY_DAYS", "int", 7, "approvals", "A", lo=1, hi=30),
        _spec("apply_in_chat", "MTG_APPLY_IN_CHAT", "bool", True, "approvals", "A", restart=True),
        # sync, backups and the nightly database backup
        _spec(
            "deck_sync_minutes",
            "MTG_DECK_SYNC_MINUTES",
            "int",
            30,
            "sync",
            "B",
            lo=30,
            hi=24 * 60,
            zero_ok=True,
            restart=True,
        ),
        _spec("archidekt_backups", "MTG_ARCHIDEKT_BACKUPS", "bool", True, "backups", "B"),
        _spec(
            "archidekt_backup_copies", "MTG_ARCHIDEKT_BACKUP_COPIES", "int", 10, "backups", "B", lo=1, hi=50
        ),
        _spec(
            "archidekt_backup_box_default",
            "MTG_ARCHIDEKT_BACKUP_BOX_DEFAULT",
            "bool",
            False,
            "backups",
            "B",
        ),
        _spec("snapshots_per_deck", "MTG_SNAPSHOTS_PER_DECK", "int", 25, "backups", "B", lo=5, hi=100),
        _spec("backup_hour_utc", "MTG_BACKUP_HOUR_UTC", "int", 3, "backups", "B", lo=0, hi=23, restart=True),
        _spec(
            "backup_keep_days", "MTG_BACKUP_KEEP_DAYS", "int", 14, "backups", "B", lo=1, hi=3650, restart=True
        ),
        # simulation
        _spec("forge_games", "MTG_FORGE_GAMES", "int", 4, "simulation", "C", lo=1, hi=50),
        _spec("forge_parallel_games", "MTG_FORGE_PARALLEL_GAMES", "int", 4, "simulation", "C", lo=1, hi=16),
        # watchlists and prices
        _spec("watchlists_enabled", "MTG_WATCHLISTS_ENABLED", "bool", False, "watchlists", "B", restart=True),
        _spec("watch_max_cards", "MTG_WATCH_MAX_CARDS", "int", 200, "watchlists", "B", lo=0, hi=200),
        _spec("watch_max_decks", "MTG_WATCH_MAX_DECKS", "int", 10, "watchlists", "B", lo=0, hi=10),
        _spec(
            "price_history_days",
            "MTG_PRICE_HISTORY_DAYS",
            "int",
            365,
            "watchlists",
            "B",
            lo=30,
            hi=3650,
            restart=True,
        ),
        _spec("price_hour_utc", "MTG_PRICE_HOUR_UTC", "int", 6, "watchlists", "B", lo=0, hi=23, restart=True),
        # general
        _spec(
            "server_name",
            "MTG_SERVER_NAME",
            "text",
            "MTG Assistant Gateway",
            "general",
            "B",
            max_len=100,
            restart=True,
        ),
        _spec(
            "chatgpt_name",
            "MTG_CHATGPT_NAME",
            "text",
            "",
            "general",
            "B",
            max_len=200,
            empty_ok=True,
            restart=True,
        ),
        _spec("update_check", "MTG_UPDATE_CHECK", "bool", True, "general", "B", restart=True),
        _spec(
            "log_level", "MTG_LOG_LEVEL", "choice", "INFO", "general", "B", choices=_LOG_LEVELS, restart=True
        ),
        # Archidekt and Scryfall pacing
        _spec(
            "archidekt_calls_per_10_min",
            "MTG_ARCHIDEKT_CALLS_PER_10_MIN",
            "int",
            120,
            "pacing",
            "B",
            lo=10,
            hi=120,
            restart=True,
        ),
        _spec(
            "archidekt_min_interval",
            "MTG_ARCHIDEKT_MIN_INTERVAL",
            "float",
            1.0,
            "pacing",
            "B",
            lo=1.0,
            hi=10.0,
            restart=True,
        ),
        _spec(
            "archidekt_max_per_minute",
            "MTG_ARCHIDEKT_MAX_PER_MINUTE",
            "int",
            30,
            "pacing",
            "B",
            lo=1,
            hi=40,
            restart=True,
        ),
        _spec(
            "archidekt_retries", "MTG_ARCHIDEKT_RETRIES", "int", 2, "pacing", "B", lo=0, hi=2, restart=True
        ),
        _spec(
            "archidekt_backoff_base",
            "MTG_ARCHIDEKT_BACKOFF_BASE",
            "float",
            1.0,
            "pacing",
            "B",
            lo=1.0,
            hi=10.0,
            restart=True,
        ),
        _spec(
            "archidekt_cache_seconds",
            "MTG_ARCHIDEKT_CACHE_SECONDS",
            "int",
            60,
            "pacing",
            "B",
            lo=60,
            hi=900,
            restart=True,
        ),
        _spec(
            "archidekt_card_cache_seconds",
            "MTG_ARCHIDEKT_CARD_CACHE_SECONDS",
            "int",
            3600,
            "pacing",
            "B",
            lo=3600,
            hi=86400,
            restart=True,
        ),
        _spec(
            "scryfall_lookup_interval",
            "MTG_SCRYFALL_LOOKUP_INTERVAL",
            "float",
            0.5,
            "pacing",
            "B",
            lo=0.5,
            hi=5.0,
            restart=True,
        ),
        # card scans (an advanced section)
        _spec(
            "scan_fuzzy_min_similarity",
            "MTG_SCAN_FUZZY_MIN_SIMILARITY",
            "float",
            0.65,
            "scan",
            "B",
            lo=0.3,
            hi=1.0,
            restart=True,
        ),
        _spec(
            "scan_fuzzy_confident_similarity",
            "MTG_SCAN_FUZZY_CONFIDENT_SIMILARITY",
            "float",
            0.8,
            "scan",
            "B",
            lo=0.3,
            hi=1.0,
            restart=True,
        ),
        _spec(
            "scan_ambiguity_margin",
            "MTG_SCAN_AMBIGUITY_MARGIN",
            "float",
            0.05,
            "scan",
            "B",
            lo=0.0,
            hi=0.5,
            restart=True,
        ),
        _spec(
            "scan_auto_add_confidence",
            "MTG_SCAN_AUTO_ADD_CONFIDENCE",
            "float",
            80.0,
            "scan",
            "B",
            lo=0.0,
            hi=100.0,
            restart=True,
        ),
        _spec(
            "scan_foil_star_ink_ratio",
            "MTG_SCAN_FOIL_STAR_INK_RATIO",
            "float",
            0.09,
            "scan",
            "B",
            lo=0.01,
            hi=0.9,
            restart=True,
        ),
        _spec(
            "scan_glare_ratio",
            "MTG_SCAN_GLARE_RATIO",
            "float",
            0.08,
            "scan",
            "B",
            lo=0.005,
            hi=0.9,
            restart=True,
        ),
        _spec(
            "scan_min_ocr_confidence",
            "MTG_SCAN_MIN_OCR_CONFIDENCE",
            "float",
            50.0,
            "scan",
            "B",
            lo=0.0,
            hi=100.0,
            restart=True,
        ),
        _spec("scan_art_enabled", "MTG_SCAN_ART_ENABLED", "bool", True, "scan", "B", restart=True),
        _spec(
            "scan_art_max_distance",
            "MTG_SCAN_ART_MAX_DISTANCE",
            "int",
            380,
            "scan",
            "B",
            lo=0,
            hi=1024,
            restart=True,
        ),
        _spec(
            "scan_art_min_margin",
            "MTG_SCAN_ART_MIN_MARGIN",
            "int",
            40,
            "scan",
            "B",
            lo=0,
            hi=1024,
            restart=True,
        ),
        _spec(
            "scan_art_max_printings",
            "MTG_SCAN_ART_MAX_PRINTINGS",
            "int",
            250,
            "scan",
            "B",
            lo=1,
            hi=2000,
            restart=True,
        ),
        _spec(
            "scan_art_image_interval",
            "MTG_SCAN_ART_IMAGE_INTERVAL",
            "float",
            0.1,
            "scan",
            "B",
            lo=0.1,
            hi=5.0,
            restart=True,
        ),
        _spec(
            "scan_art_images_per_hour",
            "MTG_SCAN_ART_IMAGES_PER_HOUR",
            "int",
            1500,
            "scan",
            "B",
            lo=1,
            hi=1500,
            restart=True,
        ),
        _spec(
            "scan_art_max_galleries",
            "MTG_SCAN_ART_MAX_GALLERIES",
            "int",
            500,
            "scan",
            "B",
            lo=1,
            hi=100000,
            restart=True,
        ),
        _spec(
            "scan_art_gallery_idle_days",
            "MTG_SCAN_ART_GALLERY_IDLE_DAYS",
            "int",
            180,
            "scan",
            "B",
            lo=1,
            hi=3650,
            restart=True,
        ),
    )
}

# What each number counts, named in its refusal ("must be between 30 and 1,440 minutes").
_UNITS = {
    "deck_sync_minutes": "minutes",
    "proposal_expiry_days": "days",
    "backup_keep_days": "days",
    "price_history_days": "days",
    "scan_art_gallery_idle_days": "days",
    "archidekt_min_interval": "seconds",
    "archidekt_backoff_base": "seconds",
    "archidekt_cache_seconds": "seconds",
    "archidekt_card_cache_seconds": "seconds",
    "scryfall_lookup_interval": "seconds",
    "scan_art_image_interval": "seconds",
}
MANAGED_SETTINGS = {k: replace(s, unit=_UNITS[k]) if k in _UNITS else s for k, s in MANAGED_SETTINGS.items()}


def _deployment_value(settings: Settings, spec: SettingSpec) -> Any:
    value = getattr(settings, spec.key)
    if spec.key == "chatgpt_name":
        return value or ""
    return value


def with_stored(settings: Settings, db: Any) -> Settings:
    """``settings`` with the values stored from the admin Settings page applied, for every key the
    deployment left blank (a deployment value always wins).

    The gateway calls this once at start-up (create_app), so settings that are read only when the
    gateway starts ("applies after a restart") take the page's value at the next restart. A stored
    value the current bounds refuse is skipped, as LiveSettings does."""
    stored = db.stored_settings()
    changes: dict[str, Any] = {}
    for key, raw in stored.items():
        spec = MANAGED_SETTINGS.get(key)
        if spec is None or key in settings.env_set:
            continue
        try:
            value = spec.parse(raw)
        except SettingError:
            logger.warning("stored setting %s is out of bounds; the default applies", key)
            continue
        if key == "chatgpt_name":
            value = value or None  # blank: the ChatGPT name follows the server name
        changes[key] = value
    return replace(settings, **changes) if changes else settings


class LiveSettings:
    """The value of every MANAGED_SETTINGS key as it is now: the deployment's when it set one,
    else the value stored from the admin Settings page, else the code default.

    One instance is shared by the whole gateway (create_app: ``state.live``, ``decks.live``), so a
    value stored through it applies at once to every reader. Stored values are kept in memory and
    re-read only by ``reload`` (one gateway process writes them)."""

    def __init__(self, settings: Settings, db: Any):
        self.settings = settings
        self.db = db
        self._lock = threading.Lock()
        self._stored: dict[str, Any] | None = None
        # called with the key after a value changes (a reader that caches it can drop its copy)
        self.on_change: list[Callable[[str], None]] = []

    def _load(self) -> dict[str, Any]:
        with self._lock:
            if self._stored is None:
                self._stored = dict(self.db.stored_settings())
            return self._stored

    def reload(self) -> None:
        with self._lock:
            self._stored = None

    @staticmethod
    def spec(key: str) -> SettingSpec:
        try:
            return MANAGED_SETTINGS[key]
        except KeyError:
            raise SettingError(key, "isn't one of the Settings page's settings") from None

    def locked(self, key: str) -> bool:
        """True when the deployment set it: the page shows it and cannot change it."""
        self.spec(key)
        return key in self.settings.env_set

    def source(self, key: str) -> str:
        """Where the value comes from now: deployment, dashboard or default."""
        if self.locked(key):
            return "deployment"
        return "dashboard" if key in self._load() and self._stored_value(key) is not None else "default"

    def _stored_value(self, key: str) -> Any:
        spec = self.spec(key)
        if key not in self._load():
            return None
        try:
            return spec.parse(self._load()[key])
        except SettingError:
            # a stored value the current bounds refuse (bounds tightened in a later version)
            logger.warning("stored setting %s is out of bounds; the default applies", key)
            return None

    def get(self, key: str) -> Any:
        spec = self.spec(key)
        if key in self.settings.env_set:
            return _deployment_value(self.settings, spec)
        stored = self._stored_value(key)
        return spec.default if stored is None else stored

    def set(self, key: str, raw: Any, *, by: str | None) -> Any:
        """Store a value from the admin Settings page (checked against the setting's bounds) and
        record it in the audit log. Refused when the deployment set the key. Returns the value."""
        spec = self.spec(key)
        if self.locked(key):
            raise SettingError(key, f"is set in your stack ({spec.env}), {STACK_LOCK_HOW}")
        value = spec.parse(raw)
        previous = self.get(key)
        self.db.save_setting(key, value, by=by)
        with self._lock:
            if self._stored is not None:
                self._stored[key] = value
        self.db.audit("setting_changed", sub=by, detail={"key": key, "value": value, "previous": previous})
        self._changed(key)
        return value

    def reset(self, key: str, *, by: str | None) -> None:
        """Forget the stored value, so the code default applies (unless the deployment set one)."""
        spec = self.spec(key)
        if self.locked(key):
            raise SettingError(key, f"is set in your stack ({spec.env}), {STACK_LOCK_HOW}")
        previous = self.get(key)
        cleared = self.db.clear_setting(key)
        with self._lock:
            if self._stored is not None:
                self._stored.pop(key, None)
        if cleared:
            self.db.audit(
                "setting_changed", sub=by, detail={"key": key, "value": spec.default, "previous": previous}
            )
            self._changed(key)

    def _changed(self, key: str) -> None:
        for hook in list(self.on_change):
            try:
                hook(key)
            except Exception:
                logger.exception("a setting change listener failed")

    def describe(self) -> list[dict[str, Any]]:
        """Every managed setting as the admin Settings page lists it, in registry order."""
        changed = self.db.setting_changes()
        return [
            {
                "key": spec.key,
                "env": spec.env,
                "group": spec.group,
                "kind": spec.kind,
                "value": self.get(spec.key),
                "default": spec.default,
                "source": self.source(spec.key),
                "lo": spec.lo,
                "hi": spec.hi,
                "choices": list(spec.choices),
                "max_len": spec.max_len,
                "restart": spec.restart,
                **({"changed": changed[spec.key]} if spec.key in changed else {}),
            }
            for spec in MANAGED_SETTINGS.values()
        ]
