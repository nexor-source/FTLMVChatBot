#!/usr/bin/env python3
"""
Simple Flask server to expose generated images under ./outputs/ as HTTP URLs.

Routes:
- GET /images/<filename>  -> serves outputs/<filename>
- GET /healthz            -> returns ok

Usage:
  python serve_outputs.py            # default host=0.0.0.0, port=5000
  HOST=0.0.0.0 PORT=5000 python serve_outputs.py

Set IMAGE_DIR to override outputs directory.
"""
from __future__ import annotations

import os
from pathlib import Path
from flask import Flask, send_from_directory, abort

ROOT = Path(__file__).resolve().parent
IMAGE_DIR = Path(os.environ.get("IMAGE_DIR", ROOT / "outputs")).resolve()
IMAGE_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/images/<path:filename>")
def get_image(filename: str):
    # Only serve files within IMAGE_DIR
    try:
        # Basic traversal protection
        safe = Path(filename).name
        fp = (IMAGE_DIR / safe).resolve()
        if not fp.is_file() or IMAGE_DIR not in fp.parents and fp != IMAGE_DIR:
            abort(404)
        return send_from_directory(IMAGE_DIR, safe)
    except Exception:
        abort(404)


def main():
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "5000"))
    app.run(host=host, port=port)


if __name__ == "__main__":
    main()

