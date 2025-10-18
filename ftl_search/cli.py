"""命令行入口与交互循环。"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
import threading
from typing import Optional, List, Set, Dict, Tuple

from .registry import index_events_expanded as index_events, index_event_nodes, build_registry, EventEntry, Registry, EventNodeEntry
from .summarize import show_single_event_detail, _parse_xml_etree, _iter_named_events_etree, _summarize_event
from .text_utils import clip_line
from .mem import get_memory_usage

_DEBUG_TIMING = os.environ.get("FTL_SEARCH_TIMING", "").lower() not in {"", "0", "false", "off"}

# 缓存可通过环境变量关闭：设置 FTL_SEARCH_DISABLE_CACHE 为非空/true 即可。
_CACHE_DISABLED = os.environ.get("FTL_SEARCH_DISABLE_CACHE", "").lower() in {"1", "true", "yes", "on"}
_CACHE_ENABLED = not _CACHE_DISABLED


def _timing_report(stage: str, elapsed: float, total: Optional[float] = None) -> None:
    if not _DEBUG_TIMING:
        return
    msg = f"[ftl_search timing] {stage}: {elapsed:.3f}s"
    if total is not None:
        msg += f" (total {total:.3f}s)"
    print(msg, flush=True)


_CACHE_LOCK = threading.Lock()
_REGISTRY_CACHE: Dict[str, Registry] = {}
_ENTRIES_CACHE: Dict[Tuple[str, int], List[EventEntry]] = {}
_NODES_CACHE: Dict[str, List[EventNodeEntry]] = {}


def _cache_key_for_data_dir(data_dir: Path) -> str:
    try:
        return str(data_dir.resolve())
    except Exception:
        return str(data_dir)


def _ensure_registry(data_dir: Path) -> Tuple[Registry, bool, float]:
    """Return (registry, built_now, elapsed)."""
    if not _CACHE_ENABLED:
        t0 = time.perf_counter()
        reg = build_registry(data_dir)
        return reg, True, time.perf_counter() - t0

    key = _cache_key_for_data_dir(data_dir)
    with _CACHE_LOCK:
        cached = _REGISTRY_CACHE.get(key)
    if cached is not None:
        return cached, False, 0.0

    t0 = time.perf_counter()
    reg = build_registry(data_dir)
    elapsed = time.perf_counter() - t0
    with _CACHE_LOCK:
        _REGISTRY_CACHE[key] = reg
        # 同步清理依赖缓存，以免引用旧 registry 的数据
        stale_entries = [ek for ek in _ENTRIES_CACHE if ek[0] == key]
        for ek in stale_entries:
            _ENTRIES_CACHE.pop(ek, None)
        _NODES_CACHE.pop(key, None)
    return reg, True, elapsed


def _ensure_entries(data_dir: Path, reg: Registry, max_depth: int) -> Tuple[List[EventEntry], bool, float]:
    if not _CACHE_ENABLED:
        t0 = time.perf_counter()
        entries = index_events(data_dir, reg, max_expand_depth=max_depth)
        return entries, True, time.perf_counter() - t0

    key = (_cache_key_for_data_dir(data_dir), max_depth)
    with _CACHE_LOCK:
        cached = _ENTRIES_CACHE.get(key)
    if cached is not None:
        return cached, False, 0.0

    t0 = time.perf_counter()
    entries = index_events(data_dir, reg, max_expand_depth=max_depth)
    elapsed = time.perf_counter() - t0
    with _CACHE_LOCK:
        _ENTRIES_CACHE[key] = entries
    return entries, True, elapsed


def _ensure_nodes(data_dir: Path, reg: Registry) -> Tuple[List[EventNodeEntry], bool, float]:
    if not _CACHE_ENABLED:
        t0 = time.perf_counter()
        nodes = index_event_nodes(data_dir, reg)
        return nodes, True, time.perf_counter() - t0

    key = _cache_key_for_data_dir(data_dir)
    with _CACHE_LOCK:
        cached = _NODES_CACHE.get(key)
    if cached is not None:
        return cached, False, 0.0

    t0 = time.perf_counter()
    nodes = index_event_nodes(data_dir, reg)
    elapsed = time.perf_counter() - t0
    with _CACHE_LOCK:
        _NODES_CACHE[key] = nodes
    return nodes, True, elapsed


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """解析命令行参数。返回 argparse.Namespace。"""
    ap = argparse.ArgumentParser(description="Search FTL MV event texts and print event names.")
    ap.add_argument("--data", dest="data", default=str(Path("data")), help="Path to data directory (default: ./data)")
    ap.add_argument("--max-depth", dest="max_depth", type=int, default=16, help="Max recursion depth for branch expansion")
    ap.add_argument("--only-outcomes", dest="only_outcomes", action="store_true", help="Only show combat outcomes and rewards (hide pre-battle choices/text)")
    ap.add_argument("--show-mem", dest="show_mem", action="store_true", help="显示内存占用 (RSS 或 tracemalloc)")
    return ap.parse_args(argv)


def run_interactive(args: argparse.Namespace) -> int:
    """交互式检索主循环。返回退出码。"""
    data_dir = Path(args.data)
    if not data_dir.is_dir():
        print(f"[错误] 数据目录不存在: {data_dir}", file=sys.stderr)
        return 2

    print(f"索引构建中: {data_dir} ...", flush=True)
    # 先构建注册表（含祖先关系），再建立文本索引
    if args.show_mem:
        _maybe_start_tracemalloc()
    reg = build_registry(data_dir)
    if args.show_mem:
        _print_mem("after build_registry")
    entries = index_events(data_dir, reg, max_expand_depth=args.max_depth)
    nodes = index_event_nodes(data_dir, reg)
    uid_lookup = {n.uid: n for n in nodes if getattr(n, "uid", None)}
    if args.show_mem:
        _print_mem("after index_events + index_event_nodes")
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
            # Try exact event-id match first (case-insensitive)
            ql = q.lower()
            entry_by_id = None
            for e in entries:
                try:
                    if e.name == q or e.name.lower() == ql:
                        entry_by_id = e
                        break
                except Exception:
                    continue
            if entry_by_id is not None:
                print("命中事件ID，解析分支中…")
                if args.show_mem:
                    _print_mem("before summarize")
                # 直接完整展开（跳过定位步骤）
                print(f"匹配事件: {entry_by_id.name} ({entry_by_id.file})")
                print("定位文本: 未能在事件内部精确定位（可能匹配来自子事件/列表）。")
                root = _parse_xml_etree(entry_by_id.file)
                if root is None:
                    print("[警告] 无法解析该事件文件，跳过详细展开。")
                else:
                    # 在同一文件可能出现多处 <event name=.../>（如列表中的占位/引用）。
                    # 优先选择“有内容”的定义（子节点更多者），避免选到自闭和占位节点。
                    target_event_el = None
                    best_children = -1
                    for name, el in _iter_named_events_etree(root):
                        if name != entry_by_id.name:
                            continue
                        ch_cnt = len(list(el))
                        if ch_cnt > best_children:
                            best_children = ch_cnt
                            target_event_el = el
                    if target_event_el is None:
                        print("[警告] 在文件中未找到目标事件。")
                    else:
                        lines: List[str] = []
                        _summarize_event(target_event_el, reg, depth=0, max_depth=args.max_depth, visited=set(), out_lines=lines, expanded=set())
                        if args.only_outcomes:
                            keys = ("战斗", "投降", "摧毁", "船员全灭", "逃跑", "敌舰逃走")
                            start_idx = None
                            original_lines = list(lines)
                            for i2, ln in enumerate(lines):
                                if any(k in ln for k in keys):
                                    start_idx = i2
                                    break
                            if start_idx is not None:
                                lines = lines[start_idx:]
                            else:
                                lines = [ln for ln in lines if any(k in ln for k in keys)]
                            if not lines:
                                lines = original_lines
                        for ln in lines:
                            print(clip_line(ln, 80))
                if args.show_mem:
                    _print_mem("after summarize")
                continue
            hit_nodes = _search_nodes(nodes, q)
            # 仅保留“最小事件”：去掉任何作为其它命中事件祖先的事件
            hit_nodes = _minimal_event_nodes(hit_nodes)
            # 若仅命中一个事件节点（含匿名），直接从该最小节点展开
            if len(hit_nodes) == 1:
                n = hit_nodes[0]
                print("仅定位到 1 个事件，解析分支中…")
                if args.show_mem:
                    _print_mem("before summarize")
                lines: List[str] = []
                _summarize_event(n.el, reg, depth=0, max_depth=args.max_depth, visited=set(), out_lines=lines, expanded=set())
                if args.only_outcomes:
                    keys = ("战斗", "投降", "摧毁", "船员全灭", "逃跑", "敌舰逃走")
                    start_idx = None
                    original_lines = list(lines)
                    for i2, ln in enumerate(lines):
                        if any(k in ln for k in keys):
                            start_idx = i2
                            break
                    if start_idx is not None:
                        lines = lines[start_idx:]
                    else:
                        lines = [ln for ln in lines if any(k in ln for k in keys)]
                    if not lines:
                        lines = original_lines
                header = (f"匹配事件: {n.name} ({n.file})" if n.name else f"匹配匿名事件 ({n.file})")
                print(header)
                for ln in lines:
                    print(clip_line(ln, 80))
                if args.show_mem:
                    _print_mem("after summarize")
                continue
            # 多个事件节点命中：直接列出（匿名显示文件+摘要），并提示收窄关键词
            if len(hit_nodes) > 1:
                if len(hit_nodes) > 5:
                    print(f"匹配事件过多：{len(hit_nodes)} 个（请提供更具体的关键词）")
                else:
                    labels = _format_node_match_labels(hit_nodes, uid_lookup, data_dir)
                    print("匹配到少量事件，事件ID分别如下:")
                    for label in labels:
                        print(label)
                continue
            # 兼容旧逻辑：无命中或仅命中具名列表
            names = [n.name for n in hit_nodes if getattr(n, 'name', None)]
            if not names:
                print("无匹配事件。")
                continue
            if len(names) == 1:
                print("仅定位到 1 个事件，解析分支中……")
                entry = next(e for e in entries if e.name == names[0])
                if args.show_mem:
                    _print_mem("before summarize")
                show_single_event_detail(
                    entry,
                    q,
                    reg,
                    max_depth=args.max_depth,
                    only_outcomes=args.only_outcomes,
                )
                if args.show_mem:
                    _print_mem("after summarize")
            else:
                if len(names) > 5:
                    print(f"匹配事件过多：{len(names)} 个（请提供更具体的关键词）")
                else:
                    for name in names:
                        print(name)
    except KeyboardInterrupt:
        print()
    return 0


def _search(entries: List[EventEntry], query: str) -> List[str]:
    q = query.strip()
    if not q:
        return []
    q_nows = "".join(q.split())
    seen = set()
    results: List[str] = []
    for e in entries:
        if q in e.text or (q_nows and q_nows in e.text_nows):
            if e.name not in seen:
                seen.add(e.name)
                results.append(e.name)
    return results


def _minimal_events(names: List[str], reg: Registry) -> List[str]:
    """在一组匹配事件中，仅保留“最小事件”。

    规则: 若 A 是 B 的祖先（A 出现在 reg.event_ancestors[B] 中），则丢弃 A。
    这样当父事件与其嵌套的子事件同时匹配时，仅保留子事件。
    """
    name_set = set(names)
    drop = set()
    for m in names:
        ancestors = reg.event_ancestors.get(m, [])
        for a in ancestors:
            if a in name_set:
                drop.add(a)
    return [n for n in names if n not in drop]


def _search_nodes(nodes: List[EventNodeEntry], query: str) -> List[EventNodeEntry]:
    q = query.strip()
    if not q:
        return []
    q_nows = "".join(q.split())
    results: List[EventNodeEntry] = []
    seen: Set[str] = set()
    for n in nodes:
        try:
            if q in n.text or (q_nows and q_nows in n.text_nows):
                if n.uid not in seen:
                    seen.add(n.uid)
                    results.append(n)
        except Exception:
            continue
    return results


def _minimal_event_nodes(nodes: List[EventNodeEntry]) -> List[EventNodeEntry]:
    if not nodes:
        return []
    by_uid = {n.uid: n for n in nodes}
    uids = set(by_uid.keys())
    drop: Set[str] = set()
    for n in nodes:
        for a in getattr(n, 'ancestors', []) or []:
            if a in uids:
                drop.add(a)
    return [n for n in nodes if n.uid not in drop]


def _nearest_named_ancestor(node: EventNodeEntry, uid_lookup: Dict[str, EventNodeEntry]) -> Optional[str]:
    """Return the closest ancestor event name, if any."""
    for anc_uid in reversed(getattr(node, "ancestors", []) or []):
        anc = uid_lookup.get(anc_uid)
        if anc and getattr(anc, "name", None):
            return anc.name
    return None


def _relative_to_data(path: Optional[Path], data_dir: Path) -> str:
    if path is None:
        return "(unknown)"
    try:
        return str(path.relative_to(data_dir))
    except Exception:
        return str(path)


def _format_node_match_labels(
    nodes: List[EventNodeEntry],
    uid_lookup: Dict[str, EventNodeEntry],
    data_dir: Path,
) -> List[str]:
    """Build user-facing labels for matched nodes, grouping anonymous children by ancestor."""
    order: List[Tuple[str, str]] = []
    order_set: Set[Tuple[str, str]] = set()
    counts: Dict[Tuple[str, str], int] = {}
    meta: Dict[Tuple[str, str], Dict[str, str]] = {}

    for node in nodes:
        name = getattr(node, "name", None)
        if name:
            key = ("named", name)
            counts[key] = counts.get(key, 0) + 1
            if key not in order_set:
                order.append(key)
                order_set.add(key)
                meta[key] = {"name": name}
            continue

        parent_name = _nearest_named_ancestor(node, uid_lookup)
        if parent_name:
            key = ("parent", parent_name)
            counts[key] = counts.get(key, 0) + 1
            if key not in order_set:
                order.append(key)
                order_set.add(key)
                meta[key] = {"name": parent_name}
            continue

        rel = _relative_to_data(getattr(node, "file", None), data_dir)
        uid = getattr(node, "uid", "")
        key = ("orphan", uid)
        counts[key] = counts.get(key, 0) + 1
        if key not in order_set:
            order.append(key)
            order_set.add(key)
            meta[key] = {"rel": rel, "uid": uid}

    labels: List[str] = []
    for key in order:
        category, _ = key
        info = meta.get(key, {})
        count = counts.get(key, 1)
        if category == "named":
            label = info.get("name", "(unknown)")
            if count > 1:
                label = f"{label} ×{count}"
        elif category == "parent":
            label = info.get("name", "(unknown)")
            suffix = "匿名子事件"
            if count > 1:
                suffix += f"×{count}"
            label = f"{label} ({suffix})"
        else:
            rel = info.get("rel") or "(unknown)"
            uid = info.get("uid")
            label = f"匿名事件（无父事件，文件 {rel}"
            if uid:
                label += f"，节点 {uid}"
            label += ")"
        labels.append(label)

    return labels


def _maybe_start_tracemalloc() -> None:
    try:
        import tracemalloc  # type: ignore
        tracemalloc.start()
    except Exception:
        pass


def _print_mem(label: str) -> None:
    info = get_memory_usage()
    parts = [f"{label}"]
    if "rss_bytes" in info:
        parts.append(f"RSS={info['rss_bytes']/1024/1024:.1f}MB")
    if "tracemalloc_current" in info:
        parts.append(f"PyCur={info['tracemalloc_current']/1024/1024:.1f}MB")
    if "tracemalloc_peak" in info:
        parts.append(f"PyPeak={info['tracemalloc_peak']/1024/1024:.1f}MB")
    print("[内存] " + ", ".join(parts))


def cli_main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    return run_interactive(args)


# Library-style single-shot search entry to keep QQ bot consistent with CLI.

def search_once(
    query: str,
    data_dir: Path,
    *,
    max_depth: int = 16,
    only_outcomes: bool = False,
    mode: str = "auto",
) -> dict:
    """Run one search using the same logic as the CLI interactive flow.

    mode:
      - "auto": behave like the interactive CLI (event-id exact match + text search).
      - "text": skip direct event-id matching and only perform text-based search.
      - "id": only attempt exact event-id expansion; no text search fallback.

    Returns a dict:
    - {kind: 'expand', name: str, text: str}
    - {kind: 'names', names: List[str]}
    - {kind: 'not_found'}
    - {kind: 'empty_query'}
    """
    total_start = time.perf_counter()

    q = (query or "").strip()
    if not q:
        return {"kind": "empty_query"}

    mode_normalized = (mode or "auto").strip().lower()
    if mode_normalized not in {"auto", "text", "id"}:
        raise ValueError(f"invalid search mode: {mode}")

    reg, reg_built, reg_elapsed = _ensure_registry(data_dir)
    total_after_reg = time.perf_counter() - total_start
    _timing_report("build_registry" + ("" if reg_built else " (cached)"), reg_elapsed, total_after_reg)

    entries, entries_built, entries_elapsed = _ensure_entries(data_dir, reg, max_depth)
    total_after_entries = time.perf_counter() - total_start
    _timing_report("index_events" + ("" if entries_built else " (cached)"), entries_elapsed, total_after_entries)

    def _expand_entry(entry: EventEntry) -> dict:
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            print(f"匹配事件: {entry.name} ({entry.file})")
            print("定位文本: 未能在事件内部精确定位（可能匹配来自子事件/列表）")
            root = _parse_xml_etree(entry.file)
            if root is not None:
                target_event_el = None
                best_children = -1
                for name, el in _iter_named_events_etree(root):
                    if name != entry.name:
                        continue
                    ch_cnt = len(list(el))
                    if ch_cnt > best_children:
                        best_children = ch_cnt
                        target_event_el = el
                if target_event_el is not None:
                    lines: List[str] = []
                    _summarize_event(target_event_el, reg, depth=0, max_depth=max_depth, visited=set(), out_lines=lines, expanded=set())
                    if only_outcomes:
                        keys = ("战斗", "投降", "摧毁", "船员全灭", "逃跑", "敌舰逃走")
                        start_idx = None
                        original_lines = list(lines)
                        for i2, ln in enumerate(lines):
                            if any(k in ln for k in keys):
                                start_idx = i2
                                break
                        if start_idx is not None:
                            lines = lines[start_idx:]
                        else:
                            lines = [ln for ln in lines if any(k in ln for k in keys)]
                        if not lines:
                            lines = original_lines
                    for ln in lines:
                        print(clip_line(ln, 100))
        return {"kind": "expand", "name": entry.name, "text": buf.getvalue().strip()}

    if mode_normalized != "text":
        ql = q.lower()
        entry_by_id = None
        for e in entries:
            try:
                if e.name == q or e.name.lower() == ql:
                    entry_by_id = e
                    break
            except Exception:
                continue
        if entry_by_id is not None:
            return _expand_entry(entry_by_id)
        if mode_normalized == "id":
            return {"kind": "not_found"}

    if mode_normalized == "id":
        return {"kind": "not_found"}

    nodes, nodes_built, nodes_elapsed = _ensure_nodes(data_dir, reg)
    uid_lookup = {n.uid: n for n in nodes if getattr(n, "uid", None)}
    total_after_nodes = time.perf_counter() - total_start
    _timing_report("index_event_nodes" + ("" if nodes_built else " (cached)"), nodes_elapsed, total_after_nodes)

    # Locate by node search first (same as CLI)
    hit_nodes = _search_nodes(nodes, q)
    hit_nodes = _minimal_event_nodes(hit_nodes)
    total_node_hits = len(hit_nodes)
    if total_node_hits > 1:
        labels = _format_node_match_labels(hit_nodes, uid_lookup, data_dir)
        if labels:
            note = "匹配到少量事件，事件ID分别如下:" if 1 < total_node_hits <= 5 else None
            return {
                "kind": "names",
                "names": labels,
                "match_count": total_node_hits,
                "note": note,
            }
    # Single node hit: summarize that node directly (handles anonymous events)
    if len(hit_nodes) == 1:
        n = hit_nodes[0]
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            lines: List[str] = []
            _summarize_event(n.el, reg, depth=0, max_depth=max_depth, visited=set(), out_lines=lines, expanded=set())
            if only_outcomes:
                keys = ("战斗", "投降", "摧毁", "船员全灭", "逃跑", "敌舰逃走")
                start_idx = None
                original_lines = list(lines)
                for i2, ln in enumerate(lines):
                    if any(k in ln for k in keys):
                        start_idx = i2
                        break
                if start_idx is not None:
                    lines = lines[start_idx:]
                else:
                    lines = [ln for ln in lines if any(k in ln for k in keys)]
                if not lines:
                    lines = original_lines
            header = (f"匹配事件: {n.name} ({n.file})" if getattr(n, 'name', None) else f"匹配匿名事件 ({n.file})")
            print(header)
            for ln in lines:
                print(clip_line(ln, 100))
        return {"kind": "expand", "name": getattr(n, 'name', None) or "(anonymous)", "text": buf.getvalue().strip()}
    # No useful node hits -> derive names; if still empty, fall back to expanded entry search
    names = [n.name for n in hit_nodes if getattr(n, 'name', None)]
    if not names:
        names = _minimal_events(_search(entries, q), reg)
        if not names:
            return {"kind": "not_found"}
    if len(names) == 1:
        entry = next(e for e in entries if e.name == names[0])
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            show_single_event_detail(entry, q, reg, max_depth=max_depth, only_outcomes=only_outcomes)
        return {"kind": "expand", "name": entry.name, "text": buf.getvalue().strip()}
    else:
        note = "匹配到少量事件，事件ID分别如下:" if 1 < len(names) <= 5 else None
        return {"kind": "names", "names": names, "match_count": len(names), "note": note}
