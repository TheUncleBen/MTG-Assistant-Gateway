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
    oidc_client_secret: str
    oidc_scopes: str
    required_group: str | None
    session_secret: str
    fernet_key: str
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
    apply_via_mcp: bool = False
    apply_min_age_seconds: int = 15  # MCP apply refused on proposals younger than this
    archidekt_base: str = "https://archidekt.com/api"
    archidekt_backups: bool = True
    archidekt_backup_folder: str = "MTG Gateway backups"
    archidekt_user_agent: str = (
        "mtg-assistant-gateway/0.1 (+https://github.com/TheUncleBen/MTG-Assistant-Gateway)"
    )
    archidekt_min_interval: float = 1.0
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
    auth_code_ttl: int = 300
    login_ttl: int = 600
    listen_host: str = "0.0.0.0"
    listen_port: int = 8080
    log_level: str = "INFO"
    server_name: str = "MTG Assistant Gateway"

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
    if len(session_secret) < 32:
        raise ConfigError("MTG_SESSION_SECRET_FILE must contain at least 32 characters")

    data_dir = Path(_env("MTG_DATA_DIR", "/data") or "/data")
    backup_raw = _env("MTG_BACKUP_DIR", "")
    backup_dir = Path(backup_raw) if backup_raw else None

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
        oidc_scopes=_env("MTG_OIDC_SCOPES", "openid profile email") or "openid profile email",
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
        allowed_hosts=allowed_hosts,
        mystic_forge_url=(_env("MTG_MYSTIC_FORGE_URL", "") or None),
        cimd_enabled=_bool_env("MTG_CIMD_ENABLED", True),
        cimd_allowed_hosts=[
            h.strip() for h in (_env("MTG_CIMD_ALLOWED_HOSTS", "") or "").split(",") if h.strip()
        ],
        apply_via_mcp=_bool_env("MTG_APPLY_VIA_MCP", False),
        apply_min_age_seconds=_int_env("MTG_APPLY_MIN_AGE_SECONDS", 15, lo=0, hi=3600),
        browser_session_ttl=_int_env("MTG_BROWSER_SESSION_TTL", 2 * 3600, lo=300, hi=86400),
        writes_enabled=_bool_env("MTG_WRITES_ENABLED", False),
        archidekt_base=(_env("MTG_ARCHIDEKT_BASE", "https://archidekt.com/api") or "").rstrip("/"),
        archidekt_backups=_bool_env("MTG_ARCHIDEKT_BACKUPS", True),
        archidekt_backup_folder=(_env("MTG_ARCHIDEKT_BACKUP_FOLDER", "MTG Gateway backups") or "").strip()[
            :100
        ]
        or "MTG Gateway backups",
        access_token_ttl=_int_env("MTG_ACCESS_TOKEN_TTL", 3600, lo=60, hi=86400),
        refresh_token_ttl=_int_env("MTG_REFRESH_TOKEN_TTL", 30 * 24 * 3600, lo=3600, hi=365 * 24 * 3600),
        reauth_interval=_int_env("MTG_REAUTH_INTERVAL", 7 * 24 * 3600, lo=3600, hi=365 * 24 * 3600),
        listen_host=_env("MTG_LISTEN_HOST", "0.0.0.0") or "0.0.0.0",
        listen_port=_int_env("MTG_LISTEN_PORT", 8080, lo=1, hi=65535),
        log_level=(_env("MTG_LOG_LEVEL", "INFO") or "INFO").upper(),
        server_name=_env("MTG_SERVER_NAME", "MTG Assistant Gateway") or "MTG Assistant Gateway",
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
