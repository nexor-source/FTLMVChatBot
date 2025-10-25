"""索引 data 目录，构建检索所需的注册表。

数据结构:
- EventEntry: 具名事件的简要信息（名称、文件、全文/去空白文本）
- Registry: 汇总 data 下的事件/事件列表/文本列表/舰船败亡映射

主要函数:
- index_events: 为快速模糊检索建立条目列表
- build_registry: 构建 Registry，提供事件/列表/文本与舰船败亡映射解析
"""

from __future__ import annotations

import copy
import html
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple, Dict, Any, Set

import xml.etree.ElementTree as ET


@dataclass
class EventEntry:
    name: str
    file: Path
    text: str
    text_nows: str


@dataclass
class EventNodeEntry:
    uid: str
    name: Optional[str]
    file: Path
    el: Any
    text: str
    text_nows: str
    ancestors: List[str]


@dataclass
class Registry:
    data_dir: Path
    events: Dict[str, Tuple[Path, Any]]
    event_lists: Dict[str, List[Any]]
    text_lists: Dict[str, List[str]]
    ship_defs: Dict[str, Dict[str, Dict[str, Any]]]
    event_ancestors: Dict[str, List[str]]  # 事件名 -> 其所有具名祖先事件名（外层->内层）用来做“仅保留最小事件”逻辑


def _strip_namespace(tag: str) -> str:
    """移除 ElementTree 标签上的命名空间前缀，返回本地名。"""
    if tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag


def _iter_named_events_etree(root) -> Iterable[Tuple[str, object]]:
    """遍历 XML 树，产出所有具名 <event> 的 (name, element)。"""
    for el in root.iter():
        try:
            if _strip_namespace(getattr(el, "tag", "")) == "event":
                name = el.attrib.get("name")
                if name:
                    yield name, el
        except Exception:
            continue


def _gather_subtree_text(el) -> str:
    """汇总元素子树所有可见文本，去多余空白并反转义 HTML 实体。"""
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
    """解析 XML 文件，成功返回根节点，失败返回 None（容错）。"""
    try:
        return ET.parse(str(path)).getroot()
    except Exception:
        return None


def _xml_language_preference_key(path: Path) -> tuple[int, str]:
    """XML 路径排序 key：让中文资源最后处理，从而覆盖同名英文事件。"""
    lower_parts = [p.lower() for p in path.parts]
    zh_markers = ("zh", "简体", "chinese", "chs", "cn", "汉化")
    is_zh = any(any(marker in part for marker in zh_markers) for part in lower_parts)
    # 英文（非中文）文件优先，中文延后，保证中文覆盖
    return (0 if not is_zh else 1, str(path).lower())


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
    """正则容错解析具名 <event> 的纯文本（仅用于索引/检索）。

    返回: (事件名, 文本) 迭代器
    限制: 非严格解析，适合在 XML 片段/不规范文件上构建索引。
    """
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


def index_events_expanded(
    data_dir: Path,
    reg: Optional["Registry"] = None,
    *,
    max_expand_depth: int = 8,
) -> List[EventEntry]:
    """增强索引：为具名事件递归合并通过 load 引用到的文本。

    - 合并 event / eventList / textList 的文本（限深、去重、环检测）。
    - 返回值与原 index_events 相同类型，兼容现有检索逻辑。
    - 若未提供 reg，将内部构建一次 Registry。
    """

    if reg is None:
        reg = build_registry(data_dir)

    expand_cache: Dict[str, str] = {}

    def _text_for_textlist(name: str) -> str:
        vals = reg.text_lists.get(name) if hasattr(reg, 'text_lists') else None
        if not vals:
            return ""
        try:
            return " ".join([v for v in vals if v])
        except Exception:
            return ""

    def _expand_event_by_name(name: str, depth: int, visited: Set[str]) -> str:
        key = f"E:{name}"
        if depth >= max_expand_depth or key in visited:
            return ""
        if key in expand_cache:
            return expand_cache[key]
        pair = reg.events.get(name)
        if not pair:
            return ""
        _, el = pair
        visited.add(key)
        try:
            txt = _gather_subtree_text(el)
            more = _expand_loads_in_tree(el, depth + 1, visited)
            out = (txt + " " + more).strip()
            expand_cache[key] = out
            return out
        finally:
            visited.discard(key)

    def _expand_eventlist_by_name(name: str, depth: int, visited: Set[str]) -> str:
        key = f"L:{name}"
        if depth >= max_expand_depth or key in visited:
            return ""
        if key in expand_cache:
            return expand_cache[key]
        items = reg.event_lists.get(name)
        if not items:
            return ""
        visited.add(key)
        try:
            parts: List[str] = []
            for it in items:
                try:
                    parts.append(_gather_subtree_text(it))
                    parts.append(_expand_loads_in_tree(it, depth + 1, visited))
                except Exception:
                    continue
            out = re.sub(r"\s+", " ", " ".join(p for p in parts if p).strip())
            expand_cache[key] = out
            return out
        finally:
            visited.discard(key)

    def _expand_loads_in_tree(root_el, depth: int, visited: Set[str]) -> str:
        if depth >= max_expand_depth:
            return ""
        parts: List[str] = []
        seen_refs: Set[str] = set()
        for node in getattr(root_el, 'iter', lambda: [])():
            try:
                tag = _strip_namespace(getattr(node, "tag", ""))
                tag_lower = tag.lower()
                # textList via <text load="...">
                if tag_lower == "text" and 'load' in getattr(node, 'attrib', {}):
                    tname = node.attrib.get('load') or ""
                    if tname:
                        key = f"T:{tname}"
                        if key not in seen_refs:
                            seen_refs.add(key)
                            parts.append(_text_for_textlist(tname))
                    continue

                # generic load attribute
                if 'load' in getattr(node, 'attrib', {}):
                    lname = node.attrib.get('load') or ""
                    if not lname or lname == 'COMBAT_CHECK':
                        continue
                    if lname in reg.events:
                        key = f"E:{lname}"
                        if key not in seen_refs:
                            seen_refs.add(key)
                            parts.append(_expand_event_by_name(lname, depth + 1, visited))
                        continue
                    if lname in reg.event_lists:
                        key = f"L:{lname}"
                        if key not in seen_refs:
                            seen_refs.add(key)
                            parts.append(_expand_eventlist_by_name(lname, depth + 1, visited))
                        continue

                # loadEvent / loadEventList nodes
                if tag_lower == 'loadevent':
                    name_txt = (node.text or "").strip()
                    if name_txt and name_txt != 'COMBAT_CHECK':
                        if name_txt in reg.events:
                            key = f"E:{name_txt}"
                            if key not in seen_refs:
                                seen_refs.add(key)
                                parts.append(_expand_event_by_name(name_txt, depth + 1, visited))
                        elif name_txt in reg.event_lists:
                            key = f"L:{name_txt}"
                            if key not in seen_refs:
                                seen_refs.add(key)
                                parts.append(_expand_eventlist_by_name(name_txt, depth + 1, visited))
                    continue
                if tag_lower == 'loadeventlist':
                    name_txt = (node.text or "").strip()
                    if name_txt:
                        key = f"L:{name_txt}"
                        if key not in seen_refs:
                            seen_refs.add(key)
                            parts.append(_expand_eventlist_by_name(name_txt, depth + 1, visited))
                    continue
            except Exception:
                continue
        return re.sub(r"\s+", " ", " ".join(p for p in parts if p).strip())

    # 逐具名事件构建条目
    entries: List[EventEntry] = []
    for name, (fp, el) in reg.events.items():
        try:
            base = _gather_subtree_text(el)
            extra = _expand_loads_in_tree(el, 0, set())
            text = (base + " " + extra).strip()
            if text:
                entries.append(
                    EventEntry(
                        name=name,
                        file=fp,
                        text=text,
                        text_nows=re.sub(r"\s+", "", text),
                    )
                )
        except Exception:
            continue

    # 兜底：用正则补充解析失败但包含具名 <event> 的文件
    known = set(reg.events.keys())
    xml_files = sorted(
        [
            f
            for f in data_dir.rglob("*")
            if f.is_file() and (f.suffix.lower() == ".xml" or str(f).lower().endswith(".xml.append"))
        ],
        key=_xml_language_preference_key,
    )
    for fp in xml_files:
        try:
            for nm, txt in _fallback_parse_events_text(fp):
                if not txt or nm in known:
                    continue
                entries.append(
                    EventEntry(
                        name=nm,
                        file=fp,
                        text=txt,
                        text_nows=re.sub(r"\s+", "", txt),
                    )
                )
        except Exception:
            continue

    return entries

def index_events(data_dir: Path) -> List[EventEntry]:
    """扫描 data 目录，构建可搜索的事件条目列表（仅具名 <event>）。

    输入: data_dir 数据目录
    返回: EventEntry 列表（包含全文与去空白文本两种形式）
    """
    xml_files = sorted(
        [
            f
            for f in data_dir.rglob("*")
            if f.is_file() and (f.suffix.lower() == ".xml" or str(f).lower().endswith(".xml.append"))
        ],
        key=_xml_language_preference_key,
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
    """完整解析 data，返回 Registry。

    包含:
    - 事件表/事件列表/文本列表
    - 舰船败亡映射（destroyed/deadCrew/... → 事件或事件列表）
    - 事件的具名祖先链（用于“仅保留最小事件”逻辑）
    """
    events: Dict[str, Tuple[Path, Any]] = {}
    event_lists: Dict[str, List[Any]] = {}
    text_lists: Dict[str, List[str]] = {}
    ship_defs: Dict[str, Dict[str, Dict[str, Any]]] = {}
    event_ancestors: Dict[str, List[str]] = {}

    xml_files = sorted(
        [
            f
            for f in data_dir.rglob("*")
            if f.is_file() and (f.suffix.lower() == ".xml" or str(f).lower().endswith(".xml.append"))
        ],
        key=_xml_language_preference_key,
    )

    for fp in xml_files:
        root = _parse_xml_etree(fp)
        if root is not None:
            for name, el in _iter_named_events_etree(root):
                events[name] = (fp, el)

            # 递归计算具名事件的祖先链
            def visit(node, ancestors: List[str]):
                tag = _strip_namespace(getattr(node, "tag", ""))
                cur_anc = ancestors
                if tag == "event":
                    nm = node.attrib.get("name")
                    if nm:
                        event_ancestors[nm] = list(ancestors)
                        cur_anc = ancestors + [nm]
                for ch in list(node):
                    visit(ch, cur_anc)

            visit(root, [])

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
        event_ancestors=event_ancestors,
    )


def index_event_nodes(data_dir: Path, reg: Optional["Registry"] = None) -> List[EventNodeEntry]:
    """索引所有 <event> 节点（含匿名），并生成可用于“最小事件”检索的条目。

    - 文本来源：节点子树的可见文本 + 子树中的 `<text load="...">` 对应 textList 内容。
    - 祖先关系：按 XML 结构的父子 <event> 链记录（不经由 load 关系）。
    - 不展开 event/eventList 的 load；这些在展示阶段再展开。
    """
    if reg is None:
        reg = build_registry(data_dir)

    def _text_for_textlist(name: str) -> str:
        vals = reg.text_lists.get(name) if hasattr(reg, 'text_lists') else None
        if not vals:
            return ""
        try:
            return " ".join([v for v in vals if v])
        except Exception:
            return ""

    def _gather_textlist_in_subtree(el) -> str:
        parts: List[str] = []
        for node in getattr(el, 'iter', lambda: [])():
            try:
                tag = _strip_namespace(getattr(node, "tag", ""))
                if tag == 'text' and 'load' in getattr(node, 'attrib', {}):
                    tname = node.attrib.get('load') or ""
                    if tname:
                        s = _text_for_textlist(tname)
                        if s:
                            parts.append(s)
            except Exception:
                continue
        return re.sub(r"\s+", " ", " ".join(p for p in parts if p).strip())

    entries: List[EventNodeEntry] = []
    uid_counter = 0
    ship_uid_counter = 0
    ship_branch_tags = {"surrender", "destroyed", "deadCrew", "escape", "gotaway"}

    xml_files = sorted(
        [
            f
            for f in data_dir.rglob("*")
            if f.is_file() and (f.suffix.lower() == ".xml" or str(f).lower().endswith(".xml.append"))
        ],
        key=_xml_language_preference_key,
    )

    for fp in xml_files:
        root = _parse_xml_etree(fp)
        if root is None:
            continue

        stack: List[str] = []  # ancestor event uids

        def visit(node):
            nonlocal uid_counter, ship_uid_counter
            try:
                tag = _strip_namespace(getattr(node, "tag", ""))
            except Exception:
                tag = ""
            is_event = tag == 'event'
            cur_uid: Optional[str] = None
            if is_event:
                uid_counter += 1
                cur_uid = f"EN{uid_counter}"
                try:
                    base = _gather_subtree_text(node)
                except Exception:
                    base = ""
                extra = _gather_textlist_in_subtree(node)
                text = re.sub(r"\s+", " ", (base + " " + extra).strip())
                entries.append(
                    EventNodeEntry(
                        uid=cur_uid,
                        name=node.attrib.get('name'),
                        file=fp,
                        el=node,
                        text=text,
                        text_nows=re.sub(r"\s+", "", text),
                        ancestors=list(stack),
                    )
                )
                stack.append(cur_uid)
            if tag == 'ship':
                ship_name = node.attrib.get('load') or node.attrib.get('name') or "(ship)"
                for branch in list(node):
                    try:
                        btag = _strip_namespace(getattr(branch, "tag", ""))
                    except Exception:
                        continue
                    if btag not in ship_branch_tags:
                        continue
                    try:
                        base_branch = _gather_subtree_text(branch)
                    except Exception:
                        base_branch = ""
                    extra_branch = _gather_textlist_in_subtree(branch)
                    branch_text = re.sub(r"\s+", " ", (base_branch + " " + extra_branch).strip())
                    if not branch_text:
                        continue
                    ship_uid_counter += 1
                    uid = f"SH{ship_uid_counter}"
                    synthetic = copy.deepcopy(branch)
                    entries.append(
                        EventNodeEntry(
                            uid=uid,
                            name=f"{ship_name}:{btag}",
                            file=fp,
                            el=synthetic,
                            text=branch_text,
                            text_nows=re.sub(r"\s+", "", branch_text),
                            ancestors=list(stack),
                        )
                    )
            for ch in list(node):
                visit(ch)
            if is_event:
                try:
                    stack.pop()
                except Exception:
                    pass

        visit(root)

    return entries
