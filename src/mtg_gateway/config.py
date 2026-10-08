"""Configuration from environment variables and Docker-secret files.

Nothing site-specific is hard-coded. Every deployment value comes from the
environment; every secret comes from a file (Docker Swarm mounts secrets under
/run/secrets/<name>).
"""

from __future__ import annotations

import ipaddress
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from cryptography.fernet import Fernet

from .oidc import TOKEN_AUTH_METHODS, validate_groups_claim


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


def _env(name: str, default: str | None = None, *, required: bool = False) -> str | None:
    value = os.environ.get(name)
    if value is None or value == "":
        if required and default is None:
            raise ConfigError(f"{name} is required")
        return default
    return value


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


def _float_env(name: str, default: float, *, lo: float, hi: float) -> float:
    raw = _env(name, str(default)) or str(default)
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number of seconds") from exc
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
    mystic_forge_url: str | None = None
    cimd_enabled: bool = True
    cimd_allowed_hosts: list[str] = field(default_factory=list)
    writes_enabled: bool = False
    # The Approve/Reject card an AI app shows in the chat (MCP Apps) may apply a proposal with
    # its one-time code; false leaves only the review page.
    apply_in_chat: bool = True
    # Approval modes (modes.py): the mode of a member who has not chosen one on their Account
    # page, the highest mode members may choose (auto = no cap), and how many review rows an
    # edit may have and still count as low risk in semi mode.
    approval_mode_default: str = "manual"
    approval_mode_max: str = "auto"
    auto_apply_max_rows: int = 5
    archidekt_base: str = "https://archidekt.com/api"
    archidekt_backups: bool = True
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
    archidekt_max_per_minute: int = 40
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
    # Optional hourly clean-up of removed members' Archidekt sessions (idp_sweep.py): an Authentik
    # API token that may only view groups, and the Authentik address (empty = the issuer's).
    authentik_api_token: str | None = field(default=None, repr=False)
    authentik_api_token_problem: str | None = None
    authentik_api_url: str | None = None

    def grants_access(self, groups: list[str] | None) -> bool:
        """May someone in ``groups`` use the gateway? Members of MTG_REQUIRED_GROUP may, and so
        may members of MTG_ADMIN_GROUP: an admin needs no second group to sign in. Both are read
        from the identity provider's live answer, so leaving either group is seen the same way."""
        if not self.required_group:
            return True
        held = groups or []
        return self.required_group in held or bool(self.admin_group and self.admin_group in held)

    @property
    def public_host(self) -> str:
        return urlparse(self.public_url).netloc

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
    try:
        sweep_token = _read_secret("MTG_AUTHENTIK_API_TOKEN_FILE", required=False)
    except ConfigError as exc:
        sweep_problem = str(exc)
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
        session_secret=session_secret,
        fernet_key=fernet_key,
        data_dir=data_dir,
        backup_dir=backup_dir,
        backup_hour_utc=_int_env("MTG_BACKUP_HOUR_UTC", 3, lo=0, hi=23),
        backup_keep_days=_int_env("MTG_BACKUP_KEEP_DAYS", 14, lo=1, hi=3650),
        backup_copy_dir=backup_copy_dir,
        allowed_hosts=allowed_hosts,
        mystic_forge_url=(_env("MTG_MYSTIC_FORGE_URL", "") or None),
        cimd_enabled=_bool_env("MTG_CIMD_ENABLED", True),
        cimd_allowed_hosts=[
            h.strip() for h in (_env("MTG_CIMD_ALLOWED_HOSTS", "") or "").split(",") if h.strip()
        ],
        apply_in_chat=_bool_env("MTG_APPLY_IN_CHAT", True),
        approval_mode_default=_mode_env("MTG_APPROVAL_MODE_DEFAULT", "manual"),
        approval_mode_max=_mode_env("MTG_APPROVAL_MODE_MAX", "auto"),
        auto_apply_max_rows=_int_env("MTG_AUTO_APPLY_MAX_ROWS", 5, lo=1, hi=100),
        browser_session_ttl=_int_env("MTG_BROWSER_SESSION_TTL", 2 * 3600, lo=300, hi=86400),
        writes_enabled=_bool_env("MTG_WRITES_ENABLED", False),
        archidekt_base=(_env("MTG_ARCHIDEKT_BASE", "https://archidekt.com/api") or "").rstrip("/"),
        archidekt_backups=_bool_env("MTG_ARCHIDEKT_BACKUPS", True),
        archidekt_calls_per_10_min=_int_env("MTG_ARCHIDEKT_CALLS_PER_10_MIN", 120, lo=10, hi=100_000),
        archidekt_min_interval=_float_env("MTG_ARCHIDEKT_MIN_INTERVAL", 1.0, lo=0.25, hi=10.0),
        archidekt_max_per_minute=_int_env("MTG_ARCHIDEKT_MAX_PER_MINUTE", 40, lo=1, hi=120),
        archidekt_retries=_int_env("MTG_ARCHIDEKT_RETRIES", 2, lo=0, hi=4),
        archidekt_backoff_base=_float_env("MTG_ARCHIDEKT_BACKOFF_BASE", 1.0, lo=0.1, hi=10.0),
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
        authentik_api_token=sweep_token,
        authentik_api_token_problem=sweep_problem,
        authentik_api_url=_authentik_api_url(oidc_issuer),
        scryfall_lookup_interval=_float_env("MTG_SCRYFALL_LOOKUP_INTERVAL", 0.5, lo=0.1, hi=5.0),
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
        scan_art_image_interval=_float_env("MTG_SCAN_ART_IMAGE_INTERVAL", 0.1, lo=0.05, hi=5.0),
        scan_art_images_per_hour=_int_env("MTG_SCAN_ART_IMAGES_PER_HOUR", 1500, lo=1, hi=36000),
        scan_art_max_galleries=_int_env("MTG_SCAN_ART_MAX_GALLERIES", 500, lo=1, hi=100000),
        scan_art_gallery_idle_days=_int_env("MTG_SCAN_ART_GALLERY_IDLE_DAYS", 180, lo=1, hi=3650),
        **kwargs,
    )
