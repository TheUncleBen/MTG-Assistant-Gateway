"""Command line: ``mtg-gateway serve`` (default), ``backup``, ``check-config``."""

from __future__ import annotations

import argparse
import logging
import sys

from .config import ConfigError, load_settings


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

    if command == "check-config":
        print("configuration ok")
        print(f"  public URL      : {settings.public_url}")
        print(f"  MCP endpoint    : {settings.mcp_url}")
        print(f"  OIDC issuer     : {settings.oidc_issuer}")
        print(f"  OIDC redirect   : {settings.callback_url}")
        print(f"  database        : {settings.db_path}")
        print(f"  backups         : {settings.backup_dir or '(disabled)'}")
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
            dest = export_now(db, settings.backup_dir, settings.backup_keep_days)
        finally:
            db.close()
        print(f"backup written: {dest}")
        return 0

    import uvicorn

    from .app import create_app

    app = create_app(settings)
    uvicorn.run(
        app,
        host=settings.listen_host,
        port=settings.listen_port,
        proxy_headers=True,
        forwarded_allow_ips=settings.trusted_proxies,
        log_level=settings.log_level.lower(),
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
