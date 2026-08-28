#!/usr/bin/env python3
"""Servidor HTTP local para la aplicación estática."""

from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DevelopmentHandler(SimpleHTTPRequestHandler):
    """Sirve el proyecto y obliga al navegador a revalidar durante el desarrollo."""

    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".js": "text/javascript; charset=utf-8",
        ".mjs": "text/javascript; charset=utf-8",
        ".py": "text/x-python; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".wasm": "application/wasm",
    }

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sirve consenso-simulador como un sitio estático local."
    )
    parser.add_argument("--host", default="127.0.0.1", help="Dirección de escucha.")
    parser.add_argument("--port", type=int, default=8000, help="Puerto HTTP.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    handler = partial(DevelopmentHandler, directory=str(PROJECT_ROOT))
    server = ThreadingHTTPServer((args.host, args.port), handler)

    display_host = "localhost" if args.host in {"127.0.0.1", "::1"} else args.host
    print(f"Sirviendo {PROJECT_ROOT}")
    print(f"Abre http://{display_host}:{args.port}")
    print("Presiona Ctrl+C para detener el servidor.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor detenido.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
