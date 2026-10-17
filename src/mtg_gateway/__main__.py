"""Command line: ``mtg-gateway serve`` (default), ``backup``, ``check-config``."""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys

from .config import ConfigError, chatgpt_name_warning, load_settings
from .plugin_page import developer_name_warning

# Requests already running get this long on a stop; a background apply then gets up to 90 s more
# (decks.SHUTDOWN_GRACE_SECONDS). Together they fit inside stop_grace_period (200 s) in
# deploy/portainer-stack.yml and deploy/compose.
GRACEFUL_SHUTDOWN_SECONDS = 100


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mtg-gateway")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="run the gateway (default)")
    sub.add_parser("backup", help="write a database backup to MTG_BACKUP_DIR now")
    sub.add_parser("check-config", help="validate configuration and exit")
    args = parser.parse_args(argv)
    command = args.command or "serve"

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if settings.log_level != "DEBUG":
        # httpx logs every outbound request URL at INFO; keep that for DEBUG only.
        logging.getLogger("httpx").setLevel(logging.WARNING)
    # httpcore's DEBUG lines carry response headers (Set-Cookie among them): never log them.
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    if warning := chatgpt_name_warning(settings):
        logging.getLogger("mtg_gateway.config").warning(warning)
    if warning := developer_name_warning(settings.server_name):
        logging.getLogger("mtg_gateway.config").warning(warning)

    if command == "check-config":
        print("configuration ok")
        print(f"  public URL      : {settings.public_url}")
        print(f"  MCP endpoint    : {settings.mcp_url}")
        print(f"  OIDC issuer     : {settings.oidc_issuer}")
        print(f"  OIDC redirect   : {settings.callback_url}")
        print(f"  database        : {settings.db_path}")
        print(f"  backups         : {settings.backup_dir or '(disabled)'}")
        print(f"  backup copies   : {settings.backup_copy_dir or '(none)'}")
        print(f"  required group  : {settings.required_group or '(none; identity provider policy decides)'}")
        return 0

    if command == "backup":
        from .backup import export_now
        from .db import Database

        if settings.backup_dir is None:
            print("MTG_BACKUP_DIR is not set", file=sys.stderr)
            return 2
        settings.backup_dir.mkdir(parents=True, exist_ok=True)
        db = Database(settings.db_path)
        try:
            dest = export_now(db, settings.backup_dir, settings.backup_keep_days, settings.backup_copy_dir)
        finally:
            db.close()
        print(f"backup written: {dest}")
        return 0

    import uvicorn

    from .app import create_app

    try:
        app = create_app(settings)
    except sqlite3.DatabaseError as exc:
        print(
            f"database error: {settings.db_path} could not be opened ({exc}). If the file is damaged, "
            f"stop the gateway and restore the newest copy from {settings.backup_dir or 'your backups'} "
            "(docs/OPERATIONS.md, restoring a backup).",
            file=sys.stderr,
        )
        return 3
    # The log level may come from the admin Settings page (create_app applied the stored values).
    running = app.state.gateway.settings
    if running.log_level != settings.log_level:
        logging.getLogger().setLevel(running.log_level)
        logging.getLogger("httpx").setLevel(
            logging.NOTSET if running.log_level == "DEBUG" else logging.WARNING
        )
    config = uvicorn.Config(
        app,
        host=settings.listen_host,
        port=settings.listen_port,
        proxy_headers=True,
        forwarded_allow_ips=settings.trusted_proxies,
        log_level=running.log_level.lower(),
        access_log=False,
        # On SIGTERM (a redeploy), let running requests such as a deck apply finish before exiting.
        # The stack files give the container a longer stop_grace_period than this.
        timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS,
    )
    notifier = app.state.gateway.notifier

    class _Server(uvicorn.Server):
        """Ends the Android app's notification connections at the stop signal: they never end on
        their own, so they would hold the stop for the whole grace period. The apps reconnect."""

        def handle_exit(self, sig: int, frame: object) -> None:
            if notifier is not None:
                notifier.close_streams()
            super().handle_exit(sig, frame)  # type: ignore[arg-type]

    _Server(config).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
