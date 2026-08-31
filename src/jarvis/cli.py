"""Command-line entry point for local JARVIS operations."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
from collections.abc import Sequence

import uvicorn

from .config import load_settings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jarvis", description="Secure local JARVIS assistant")
    parser.add_argument("--config", help="Path to a TOML configuration file")
    subcommands = parser.add_subparsers(dest="command")
    serve = subcommands.add_parser("serve", help="Start the local API and web application")
    serve.add_argument("--reload", action="store_true", help="Reload during local development")
    subcommands.add_parser("models", help="List locally installed Ollama models")
    subcommands.add_parser("audit-verify", help="Verify the local audit hash chain")
    subcommands.add_parser(
        "phone-pin-hash",
        help="Interactively create a PBKDF2 record for a six-digit phone PIN",
    )
    return parser


async def _list_models(config_path: str | None) -> int:
    from .api import Services

    services = Services(load_settings(config_path))
    try:
        print(json.dumps(await services.provider.list_models(), indent=2))
    finally:
        await services.provider.close()
        services.database.close()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    command = args.command or "serve"
    if command == "serve":
        if args.config:
            os.environ["JARVIS_CONFIG"] = args.config
        settings = load_settings(args.config)
        if bool(getattr(args, "reload", False)):
            uvicorn.run(
                "jarvis.api:create_app",
                host=settings.host,
                port=settings.port,
                reload=True,
                factory=True,
            )
        else:
            from .api import create_app

            uvicorn.run(create_app(settings), host=settings.host, port=settings.port)
        return 0
    if command == "models":
        return asyncio.run(_list_models(args.config))
    if command == "audit-verify":
        from .api import Services

        services = Services(load_settings(args.config))
        try:
            print(json.dumps(services.audit.verify(), indent=2))
        finally:
            asyncio.run(services.provider.close())
            services.database.close()
        return 0
    if command == "phone-pin-hash":
        from .phone_voice import hash_phone_pin

        first = getpass.getpass("Six-digit phone PIN: ")
        second = getpass.getpass("Confirm phone PIN: ")
        if first != second:
            parser = _parser()
            parser.error("Phone PIN confirmation did not match")
        try:
            print(hash_phone_pin(first))
        except ValueError as exc:
            parser = _parser()
            parser.error(str(exc))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
