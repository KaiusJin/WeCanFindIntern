"""Packaged FastAPI sidecar entry point launched by Electron."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import socket
import sys
from pathlib import Path

import uvicorn

from wecanfindintern.desktop.migrations import apply_migrations
from wecanfindintern.desktop.runtime import DesktopPaths


def _configure_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.FileHandler(log_dir / "backend.log", encoding="utf-8"),
            logging.StreamHandler(sys.stderr),
        ],
    )


async def _serve(host: str, port: int) -> None:
    from wecanfindintern.api.app import create_app

    listen_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listen_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listen_socket.bind((host, port))
    listen_socket.listen(2048)
    actual_port = int(listen_socket.getsockname()[1])

    config = uvicorn.Config(
        create_app(),
        host=host,
        port=actual_port,
        log_config=None,
        access_log=False,
        server_header=False,
        proxy_headers=False,
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve(sockets=[listen_socket]))
    while not server.started and not task.done():
        await asyncio.sleep(0.02)
    if task.done():
        await task
        raise RuntimeError("Desktop API stopped before becoming ready")
    print(
        json.dumps({"type": "ready", "host": host, "port": actual_port}),
        flush=True,
    )
    await task


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "::1"}:
        parser.error("The desktop sidecar may only bind to loopback")

    paths = DesktopPaths.from_env()
    paths.apply_environment()
    _configure_logging(paths.logs)

    database_url = os.getenv("DATABASE_URL", "")
    desktop_token = os.getenv("WCFI_DESKTOP_TOKEN", "")
    if not database_url:
        parser.error("DATABASE_URL is required")
    if len(desktop_token) < 32:
        parser.error("WCFI_DESKTOP_TOKEN must contain at least 32 characters")
    if not paths.web.is_dir():
        parser.error(f"Packaged web resources are missing: {paths.web}")

    applied = apply_migrations(database_url, paths.migrations)
    if applied:
        logging.getLogger(__name__).info("Applied migrations: %s", ", ".join(applied))
    asyncio.run(_serve(args.host, args.port))


if __name__ == "__main__":
    main()
