"""索引 data 目录，构建检索所需的注册表。

数据结构:
- EventEntry: 具名事件的简要信息（名称、文件、全文/去空白文本）
- Registry: 汇总 data 下的事件/事件列表/文本列表/舰船败亡映射

主要函数:
- index_events: 为快速模糊检索建立条目列表
- build_registry: 构建 Registry，提供事件/列表/文本与舰船败亡映射解析
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple, Dict, Any

import xml.etree.ElementTree as ET


@dataclass
class EventEntry:
    name: str
    file: Path
    text: str
    text_nows: str


@dataclass
class Registry:
    data_dir: Path
    events: Dict[str, Tuple[Path, Any]]
    event_lists: Dict[str, List[Any]]
    text_lists: Dict[str, List[str]]
    ship_defs: Dict[str, Dict[str, Dict[str, Any]]]


def _strip_namespace(tag: str) -> str:
    if tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag


def _iter_named_events_etree(root) -> Iterable[Tuple[str, object]]:
    for el in root.iter():
        try:
            if _strip_namespace(getattr(el, "tag", "")) == "event":
                name = el.attrib.get("name")
                if name:
                    yield name, el
        except Exception:
            continue


def _gather_subtree_text(el) -> str:
    parts: List[str] = []
    try:
        for s in el.itertext():
            if s:
                parts.append(s)
    except Exception:
        pass
    text = " ".join(p.strip() for p in parts if p.strip())
    text = html.unescape(text)
    return text


def _parse_xml_etree(path: Path):
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
_SHIP_BLOCK_RE = re.compile(
    r"<ship\b(?P<attrs>[^>]*)>(?P<body>.*?)</ship>", re.IGNORECASE | re.DOTALL
)
_SHIP_NAME_ATTR_RE = re.compile(r"\bname\s*=\s*\"([^\"]+)\"", re.IGNORECASE)


def _fallback_parse_events_text(path: Path) -> Iterable[Tuple[str, str]]:
    try:
        data = path.read_text(encoding="utf-8")
    except Exception:
        try:
            data = path.read_text(encoding="gb18030", errors="ignore")
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
    xml_files = [
        f
        for f in data_dir.rglob("*")
        if f.is_file() and (f.suffix.lower() == ".xml" or str(f).lower().endswith(".xml.append"))
    ]

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


def build_registry(data_dir: Path) -> Registry:
    events: Dict[str, Tuple[Path, Any]] = {}
    event_lists: Dict[str, List[Any]] = {}
    text_lists: Dict[str, List[str]] = {}
    ship_defs: Dict[str, Dict[str, Dict[str, Any]]] = {}

    xml_files = [
        f
        for f in data_dir.rglob("*")
        if f.is_file() and (f.suffix.lower() == ".xml" or str(f).lower().endswith(".xml.append"))
    ]

    for fp in xml_files:
        root = _parse_xml_etree(fp)
        if root is not None:
            for name, el in _iter_named_events_etree(root):
                events[name] = (fp, el)

            for el in root.iter():
                try:
                    tag = _strip_namespace(getattr(el, "tag", ""))
                    if tag == "eventList":
                        name = el.attrib.get("name")
                        if name:
                            items = [
                                ch for ch in list(el) if _strip_namespace(getattr(ch, "tag", "")) == "event"
                            ]
                            if items:
                                event_lists[name] = items
                    elif tag == "textList":
                        name = el.attrib.get("name")
                        if name:
                            vals = []
                            for t in el:
                                if _strip_namespace(getattr(t, "tag", "")) == "text":
                                    vals.append((t.text or "").strip())
                            if vals:
                                text_lists[name] = vals
                    elif tag == "ship" and el.attrib.get("name"):
                        sname = el.attrib.get("name")
                        defs = ship_defs.setdefault(sname, {})
                        for k in ("surrender", "destroyed", "deadCrew", "escape", "gotaway"):
                            for sub in [c for c in list(el) if _strip_namespace(getattr(c, "tag", "")) == k]:
                                entry: Dict[str, Any] = {}
                                if 'load' in sub.attrib:
                                    entry['load'] = sub.attrib.get('load')
                                else:
                                    entry['el'] = sub
                                defs[k] = entry
                except Exception:
                    continue
        else:
            # fallback: regex 扫 ship 以补充败亡映射
            try:
                data = fp.read_text(encoding="utf-8")
            except Exception:
                try:
                    data = fp.read_text(encoding="gb18030", errors="ignore")
                except Exception:
                    data = ""
            if data:
                for m in _SHIP_BLOCK_RE.finditer(data):
                    attrs = m.group('attrs') or ''
                    name_m = _SHIP_NAME_ATTR_RE.search(attrs)
                    if not name_m:
                        continue
                    sname = html.unescape(name_m.group(1))
                    defs = ship_defs.setdefault(sname, {})
                    body = m.group('body') or ''
                    for k in ("surrender", "destroyed", "deadCrew", "escape", "gotaway"):
                        km = re.search(rf"<{k}[^>]*load=\"([^\"]+)\"", body, re.IGNORECASE)
                        if km:
                            defs[k] = {'load': html.unescape(km.group(1))}

    return Registry(
        data_dir=data_dir,
        events=events,
        event_lists=event_lists,
        text_lists=text_lists,
        ship_defs=ship_defs,
    )

