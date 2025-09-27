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
import copy
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


# ---------------------------- Deep analysis ----------------------------

@dataclass
class Registry:
    data_dir: Path
    # Maps
    events: dict  # name -> (file_path: Path, element)
    event_lists: dict  # name -> list[element]
    text_lists: dict  # name -> list[str]


def _build_registry(data_dir: Path):
    import xml.etree.ElementTree as ET  # noqa: F401 (ensures xml parser available)

    events = {}
    event_lists = {}
    text_lists = {}

    xml_files = list(
        f
        for f in data_dir.rglob("*")
        if f.is_file() and (f.suffix.lower() == ".xml" or str(f).lower().endswith(".xml.append"))
    )

    for fp in xml_files:
        root = _parse_xml_etree(fp)
        if root is None:
            continue
        # Events
        for name, el in _iter_named_events_etree(root):
            events[name] = (fp, el)
        # EventLists
        for el in root.iter():
            try:
                if _strip_namespace(getattr(el, "tag", "")) == "eventList":
                    name = el.attrib.get("name")
                    if name:
                        items = []
                        for ch in list(el):
                            if _strip_namespace(getattr(ch, "tag", "")) == "event":
                                items.append(ch)
                        if items:
                            event_lists[name] = items
            except Exception:
                continue
        # TextLists
        for el in root.iter():
            try:
                if _strip_namespace(getattr(el, "tag", "")) == "textList":
                    name = el.attrib.get("name")
                    if name:
                        vals = []
                        for t in el:
                            if _strip_namespace(getattr(t, "tag", "")) == "text":
                                vals.append((t.text or "").strip())
                        if vals:
                            text_lists[name] = vals
            except Exception:
                continue

    return Registry(data_dir=data_dir, events=events, event_lists=event_lists, text_lists=text_lists)


def _node_text(el) -> str:
    try:
        return "".join(el.itertext())
    except Exception:
        return el.text or ""


def _text_from_text_element(el, reg: Registry | None) -> str:
    if el is None:
        return ""
    txt = (el.text or "").strip()
    load = el.attrib.get("load")
    if load and reg is not None:
        vals = reg.text_lists.get(load)
        if vals:
            sample = " | ".join(vals[:2])
            return f"[textList {load}] {sample}"
        else:
            return f"[textList {load}]"
    tid = el.attrib.get("id")
    if tid:
        return f"[text id={tid}] {txt}"
    return txt


def _first_matching_node(root, query: str):
    q = query
    for el in root.iter():
        tag = _strip_namespace(getattr(el, "tag", ""))
        if tag == "text":
            s = _node_text(el)
            if q in s:
                return el, s
        else:
            s = (el.text or "")
            if s and q in s:
                return el, s
    return None, None


def _find_ancestor_path(root, target) -> List[object]:
    path: List[object] = []

    found = False

    def dfs(node) -> bool:
        nonlocal found
        if node is target:
            return True
        for ch in list(node):
            if dfs(ch):
                path.append(node)
                return True
        return False

    dfs(root)
    return list(reversed(path))


def _extract_effects(event_el) -> List[str]:
    effects: List[str] = []

    def walk(node):
        for ch in list(node):
            tag = _strip_namespace(getattr(ch, "tag", ""))
            if tag == "choice":
                # Don't descend into choices when collecting immediate effects
                continue
            if tag == "autoReward":
                val = (ch.text or "").strip()
                lvl = ch.attrib.get("level")
                effects.append(f"autoReward({lvl+': ' if lvl else ''}{val})")
            elif tag == "item_modify":
                for it in list(ch):
                    if _strip_namespace(getattr(it, "tag", "")) == "item":
                        typ = it.attrib.get("type")
                        mn = it.attrib.get("min")
                        mx = it.attrib.get("max")
                        rng = None
                        if mn and mx and mn != mx:
                            rng = f"{mn}..{mx}"
                        else:
                            rng = mn or mx or "?"
                        effects.append(f"{typ} {rng}")
            elif tag in ("weapon", "drone", "augment"):
                nm = ch.attrib.get("name")
                effects.append(f"+{tag}:{nm}")
            elif tag == "crewMember":
                amt = ch.attrib.get("amount", "1")
                cls = ch.attrib.get("class")
                effects.append(f"+crew x{amt}{(' '+cls) if cls else ''}")
            elif tag == "ship":
                if ch.attrib.get("hostile", "false").lower() == "true":
                    load = ch.attrib.get("load")
                    effects.append(f"combat{(':'+load) if load else ''}")
            elif tag == "status":
                typ = ch.attrib.get("type")
                tgt = ch.attrib.get("target")
                amt = ch.attrib.get("amount")
                effects.append(f"status({typ}:{tgt} {amt})")
            elif tag == "system":
                nm = ch.attrib.get("name")
                effects.append(f"system:{nm}")
            elif tag in ("damage", "repair"):
                amt = ch.attrib.get("amount")
                effects.append(f"{tag}:{amt}")
            walk(ch)

    walk(event_el)
    seen = set()
    out: List[str] = []
    for e in effects:
        if e not in seen:
            seen.add(e)
            out.append(e)
    return out


def _summarize_event(event_el, reg: Registry, depth: int, max_depth: int, visited: set, out_lines: List[str]):
    if depth > max_depth:
        out_lines.append("  " * depth + "…")
        return

    # Event lead text
    txt_nodes = [ch for ch in list(event_el) if _strip_namespace(getattr(ch, "tag", "")) == "text"]
    txt = _text_from_text_element(txt_nodes[0], reg) if txt_nodes else ""
    if txt:
        out_lines.append("  " * depth + f"文本: {txt}")

    eff = _extract_effects(event_el)
    if eff:
        out_lines.append("  " * depth + f"效果: {', '.join(eff)}")

    for ch in list(event_el):
        if _strip_namespace(getattr(ch, "tag", "")) != "choice":
            continue
        ctext_nodes = [t for t in list(ch) if _strip_namespace(getattr(t, "tag", "")) == "text"]
        ctext = _text_from_text_element(ctext_nodes[0], reg) if ctext_nodes else ""
        meta = []
        if ch.attrib.get("blue") in ("true", "blue"):
            meta.append("blue")
        if ch.attrib.get("req"):
            meta.append(f"req={ch.attrib.get('req')}")
        if ch.attrib.get("hidden") in ("true", "1"):
            meta.append("hidden")
        suffix = f" [{'; '.join(meta)}]" if meta else ""
        out_lines.append("  " * depth + f"选择: {ctext}{suffix}")

        # Outcomes
        nested_events = [t for t in list(ch) if _strip_namespace(getattr(t, "tag", "")) == "event"]
        nested_lists = [t for t in list(ch) if _strip_namespace(getattr(t, "tag", "")) == "eventList"]

        def handle_event_ref(ev_el):
            load = ev_el.attrib.get("load")
            if load:
                # This load can point to an <event name> or an <eventList name>
                if load in reg.events:
                    key = f"E:{load}"
                    if key in visited:
                        out_lines.append("  " * (depth + 1) + f"→ 事件 {load} (已访问，跳过循环)")
                        return
                    _, ref_el = reg.events[load]
                    visited.add(key)
                    out_lines.append("  " * (depth + 1) + f"→ 事件 {load}")
                    _summarize_event(ref_el, reg, depth + 2, max_depth, visited, out_lines)
                    visited.discard(key)
                elif load in reg.event_lists:
                    key = f"L:{load}"
                    if key in visited:
                        out_lines.append("  " * (depth + 1) + f"→ 事件列表 {load} (已访问，跳过循环)")
                        return
                    visited.add(key)
                    out_lines.append("  " * (depth + 1) + f"→ 事件列表 {load}：")
                    items = reg.event_lists.get(load) or []
                    if not items:
                        out_lines.append("  " * (depth + 2) + "(空)")
                    for idx, item in enumerate(items, 1):
                        out_lines.append("  " * (depth + 2) + f"随机分支{idx}：")
                        if item.attrib.get("load"):
                            handle_event_ref(item)
                        else:
                            _summarize_event(item, reg, depth + 2, max_depth, visited, out_lines)
                    visited.discard(key)
                else:
                    out_lines.append("  " * (depth + 1) + f"→ 引用 {load} (未找到为事件或事件列表)")
            else:
                _summarize_event(ev_el, reg, depth + 1, max_depth, visited, out_lines)

        # If there are multiple sibling <event> under a choice, they are random outcomes
        if len(nested_events) > 1:
            for idx, ev in enumerate(nested_events, 1):
                out_lines.append("  " * (depth + 1) + f"→ 随机分支{idx}：")
                handle_event_ref(ev)
        else:
            for ev in nested_events:
                handle_event_ref(ev)

        for evl in nested_lists:
            load = evl.attrib.get("load")
            if load:
                out_lines.append("  " * (depth + 1) + f"→ 事件列表 {load}：")
                items = reg.event_lists.get(load) or []
                if not items:
                    out_lines.append("  " * (depth + 2) + "(空)")
                for idx, item in enumerate(items, 1):
                    out_lines.append("  " * (depth + 2) + f"随机分支{idx}：")
                    if item.attrib.get("load"):
                        handle_event_ref(item)
                    else:
                        _summarize_event(item, reg, depth + 2, max_depth, visited, out_lines)
            else:
                children = [t for t in list(evl) if _strip_namespace(getattr(t, "tag", "")) == "event"]
                out_lines.append("  " * (depth + 1) + f"→ 事件列表（内联）共 {len(children)} 个：")
                for idx, item in enumerate(children, 1):
                    out_lines.append("  " * (depth + 2) + f"随机分支{idx}：")
                    if item.attrib.get("load"):
                        handle_event_ref(item)
                    else:
                        _summarize_event(item, reg, depth + 2, max_depth, visited, out_lines)


def _show_single_event_detail(entry: EventEntry, query: str, reg: Registry, max_depth: int = 3):
    root = _parse_xml_etree(entry.file)
    if root is None:
        print("[警告] 无法解析该事件文件，跳过详细展开。")
        return
    target_event_el = None
    for name, el in _iter_named_events_etree(root):
        if name == entry.name:
            target_event_el = el
            break
    if target_event_el is None:
        print("[警告] 在文件中未找到目标事件。")
        return

    match_node, s = _first_matching_node(target_event_el, query)
    print(f"匹配事件: {entry.name} ({entry.file})")
    if match_node is None or not s:
        print("定位文本: 未能在事件内部精确定位（可能匹配来自子事件/列表）。")
        branch_root = target_event_el
    else:
        i = s.find(query)
        pre = s[max(0, i - 20):i]
        post = s[i + len(query): i + len(query) + 20]
        print(f"定位文本: …{pre}[{query}]{post}…")
        # Try to continue from the most relevant context
        # If located inside a <choice>, summarize that choice's outcomes only
        path = _find_ancestor_path(target_event_el, match_node)
        choice_ancestor = None
        for anc in reversed(path):
            if _strip_namespace(getattr(anc, "tag", "")) == "choice":
                choice_ancestor = anc
                break

        lines: List[str] = []
        if choice_ancestor is not None:
            # Show just this choice branch
            ctext_nodes = [t for t in list(choice_ancestor) if _strip_namespace(getattr(t, "tag", "")) == "text"]
            ctext = _text_from_text_element(ctext_nodes[0], reg) if ctext_nodes else ""
            print("分支预览（从所定位的选择继续）：")
            # Reuse summarizer: fake an event wrapper to render outcomes
            fake_event = type(target_event_el)('event')  # same Element type
            fake_event.append(copy.deepcopy(choice_ancestor))
            _summarize_event(fake_event, reg, depth=0, max_depth=max_depth, visited=set(), out_lines=lines)
        else:
            print("分支预览：")
            _summarize_event(target_event_el, reg, depth=0, max_depth=max_depth, visited=set(), out_lines=lines)

        for ln in lines:
            print(ln)


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
    ap.add_argument("--max-depth", dest="max_depth", type=int, default=10, help="Max recursion depth for branch expansion")
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
            if len(names) == 1:
                print("仅定位到 1 个事件，解析分支中……")
                reg = _build_registry(data_dir)
                entry = next(e for e in entries if e.name == names[0])
                _show_single_event_detail(entry, q, reg, max_depth=args.max_depth)
            else:
                for name in names:
                    print(name)
    except KeyboardInterrupt:
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
