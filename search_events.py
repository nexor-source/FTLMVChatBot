#!/usr/bin/env python3
"""
Interactive event text searcher for FTL MV data.

Usage:
  python search_events.py [--data DIR]

- Indexes all named <event name="..."> nodes in XML/XML.append files under the
  given data directory (default: ./data).
- Prompts for a Chinese substring and prints event names whose subtree text
  contains it.
"""

from __future__ import annotations

import argparse
import html
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

try:
    # Python 3.7+
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass


@dataclass
class EventEntry:
    name: str
    file: Path
    text: str  # subtree text
    text_nows: str  # text with whitespace removed (for fuzzy matching)


def _strip_namespace(tag: str) -> str:
    if tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag


def _iter_named_events_etree(root) -> Iterable[Tuple[str, object]]:
    """Yield (name, element) for all <event name="..."> in the tree."""
    for el in root.iter():
        try:
            if _strip_namespace(getattr(el, "tag", "")) == "event":
                name = el.attrib.get("name")
                if name:
                    yield name, el
        except Exception:
            # Defensive: ignore any odd nodes
            continue


def _gather_subtree_text(el) -> str:
    parts: List[str] = []
    try:
        for s in el.itertext():
            if s:
                parts.append(s)
    except Exception:
        pass
    # Normalize common whitespace, unescape XML entities
    text = " ".join(p.strip() for p in parts if p.strip())
    text = html.unescape(text)
    return text


def _parse_xml_etree(path: Path):
    # Use built-in ElementTree for portability; rely on file prolog for encoding
    import xml.etree.ElementTree as ET

    try:
        return ET.parse(str(path)).getroot()
    except Exception:
        return None


_EVENT_BLOCK_RE = re.compile(
    r"<event\b(?P<attrs>[^>]*)>(?P<body>.*?)</event>", re.IGNORECASE | re.DOTALL
)
_NAME_ATTR_RE = re.compile(r"\bname\s*=\s*\"([^\"]+)\"", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def _fallback_parse_events_text(path: Path) -> Iterable[Tuple[str, str]]:
    """Naive fallback: regex-scan named <event> blocks and strip tags.

    Returns (name, text) pairs.
    """
    try:
        data = path.read_text(encoding="utf-8")
    except Exception:
        try:
            data = path.read_text(encoding=sys.getdefaultencoding(), errors="ignore")
        except Exception:
            return []

    out: List[Tuple[str, str]] = []
    for m in _EVENT_BLOCK_RE.finditer(data):
        attrs = m.group("attrs") or ""
        name_m = _NAME_ATTR_RE.search(attrs)
        if not name_m:
            continue
        name = html.unescape(name_m.group(1))
        body = _COMMENT_RE.sub("", m.group("body") or "")
        body = _TAG_RE.sub(" ", body)
        body = html.unescape(body)
        body = re.sub(r"\s+", " ", body).strip()
        out.append((name, body))
    return out


def index_events(data_dir: Path) -> List[EventEntry]:
    xml_files = list(
        f
        for f in data_dir.rglob("*")
        if f.is_file() and (f.suffix.lower() == ".xml" or str(f).lower().endswith(".xml.append"))
    )

    entries: List[EventEntry] = []
    for fp in xml_files:
        root = _parse_xml_etree(fp)
        if root is not None:
            for name, el in _iter_named_events_etree(root):
                text = _gather_subtree_text(el)
                if text:
                    entries.append(
                        EventEntry(
                            name=name,
                            file=fp,
                            text=text,
                            text_nows=re.sub(r"\s+", "", text),
                        )
                    )
            continue

        # Fallback (non-strict or fragmentary files)
        for name, text in _fallback_parse_events_text(fp):
            if text:
                entries.append(
                    EventEntry(
                        name=name,
                        file=fp,
                        text=text,
                        text_nows=re.sub(r"\s+", "", text),
                    )
                )

    return entries


def search(entries: List[EventEntry], query: str) -> List[str]:
    q = query.strip()
    if not q:
        return []
    q_nows = re.sub(r"\s+", "", q)

    seen = set()
    results: List[str] = []
    for e in entries:
        if q in e.text or (q_nows and q_nows in e.text_nows):
            if e.name not in seen:
                seen.add(e.name)
                results.append(e.name)
    return results


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Search FTL MV event texts and print event names.")
    ap.add_argument("--data", dest="data", default=str(Path("data")), help="Path to data directory (default: ./data)")
    args = ap.parse_args(argv)

    data_dir = Path(args.data)
    if not data_dir.is_dir():
        print(f"[错误] 数据目录不存在: {data_dir}", file=sys.stderr)
        return 2

    print(f"索引构建中: {data_dir} ...", flush=True)
    entries = index_events(data_dir)
    print(f"索引完成: 事件共 {len(entries)} 条（仅统计具名 <event>）。")

    print("输入中文检索子串（Ctrl+C 或 Ctrl+D 退出）：")
    try:
        while True:
            try:
                q = input("> ").strip()
            except EOFError:
                print()
                break
            if not q:
                continue
            names = search(entries, q)
            if not names:
                print("无匹配事件。")
                continue
            for name in names:
                print(name)
    except KeyboardInterrupt:
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

