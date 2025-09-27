#!/usr/bin/env python3
"""
FTL MV 事件文本检索与分支展开（CLI）。

用法:
  python search_events.py [--data DIR] [--max-depth N] [--only-outcomes] [--max-line-len N]

实现已模块化，核心逻辑位于 ftl_search 包。
"""

from __future__ import annotations

import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass

from ftl_search.cli import cli_main


if __name__ == "__main__":
    raise SystemExit(cli_main())

