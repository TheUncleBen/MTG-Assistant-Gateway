"""Command line: ``mtg-gateway serve`` (default), ``backup``, ``check-config``."""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys

from .config import ConfigError, load_settings

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
    uvicorn.run(
        app,
        host=settings.listen_host,
        port=settings.listen_port,
        proxy_headers=True,
        forwarded_allow_ips=settings.trusted_proxies,
        log_level=settings.log_level.lower(),
        access_log=False,
        # On SIGTERM (a redeploy), let running requests such as a deck apply finish before exiting.
        # The stack files give the container a longer stop_grace_period than this.
        timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
