"""命令行入口与交互循环。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, List

from .registry import index_events, build_registry, EventEntry, Registry
from .summarize import show_single_event_detail
from .mem import get_memory_usage


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """解析命令行参数。返回 argparse.Namespace。"""
    ap = argparse.ArgumentParser(description="Search FTL MV event texts and print event names.")
    ap.add_argument("--data", dest="data", default=str(Path("data")), help="Path to data directory (default: ./data)")
    ap.add_argument("--max-depth", dest="max_depth", type=int, default=10, help="Max recursion depth for branch expansion")
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
    entries = index_events(data_dir)
    if args.show_mem:
        _print_mem("after index_events")
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
            names = _search(entries, q)
            # 仅保留“最小事件”：去掉任何作为其它命中事件祖先的事件
            names = _minimal_events(names, reg)
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
