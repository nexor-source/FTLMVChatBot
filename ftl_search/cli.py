"""命令行入口与交互循环。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, List

from .registry import index_events, build_registry, EventEntry
from .summarize import show_single_event_detail


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """解析命令行参数。返回 argparse.Namespace。"""
    ap = argparse.ArgumentParser(description="Search FTL MV event texts and print event names.")
    ap.add_argument("--data", dest="data", default=str(Path("data")), help="Path to data directory (default: ./data)")
    ap.add_argument("--max-depth", dest="max_depth", type=int, default=10, help="Max recursion depth for branch expansion")
    ap.add_argument("--only-outcomes", dest="only_outcomes", action="store_true", help="Only show combat outcomes and rewards (hide pre-battle choices/text)")
    ap.add_argument("--max-line-len", dest="max_line_len", type=int, default=120, help="Max length per output line before truncation (adds …)")
    return ap.parse_args(argv)


def run_interactive(args: argparse.Namespace) -> int:
    """交互式检索主循环。返回退出码。"""
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
            names = _search(entries, q)
            if not names:
                print("无匹配事件。")
                continue
            if len(names) == 1:
                print("仅定位到 1 个事件，解析分支中……")
                reg = build_registry(data_dir)
                entry = next(e for e in entries if e.name == names[0])
                show_single_event_detail(
                    entry,
                    q,
                    reg,
                    max_depth=args.max_depth,
                    only_outcomes=args.only_outcomes,
                    max_line_len=args.max_line_len,
                )
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


def cli_main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    return run_interactive(args)

