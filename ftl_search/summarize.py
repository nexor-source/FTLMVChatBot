"""事件展开：打印事件文本、效果、选择与随机分支。

核心函数:
- show_single_event_detail: 对单一命中的事件定位文本，并展开分支
"""

from __future__ import annotations

from typing import List, Optional, Tuple, Set, Dict
import xml.etree.ElementTree as ET

from .registry import (
    Registry,
    _strip_namespace,
    _iter_named_events_etree,
    _parse_xml_etree,
)
from .effects import extract_effects
from .text_utils import abbrev_text, clip_line

# Invisible sentinel to carry blue=false without showing in output text
_BLUE_FALSE_SENTINEL = "\u2063\u2063\u2063\u2063"


def _text_from_text_element(el, reg: Optional[Registry]) -> str:
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
            s = "".join(el.itertext())
            if q in s:
                return el, s
        else:
            s = (el.text or "")
            if s and q in s:
                return el, s
    return None, None


def _find_ancestor_path(root, target) -> List[object]:
    path: List[object] = []

    def dfs(node) -> bool:
        if node is target:
            return True
        for ch in list(node):
            if dfs(ch):
                path.append(node)
                return True
        return False

    dfs(root)
    return list(reversed(path))


def _handle_event_ref(
    ev_el,
    reg: Registry,
    depth: int,
    max_depth: int,
    visited: Set[str],
    out_lines: List[str],
    expanded: Set[str],
):

    load = ev_el.attrib.get("load")
    if not load:
        _summarize_event(ev_el, reg, depth + 1, max_depth, visited, out_lines, expanded)
        return
    if load == "COMBAT_CHECK":
        return
    if load in reg.events:
        key = f"E:{load}"
        indent = "  " * (depth + 1)
        label = f"→ 事件 {load}"
        if key in visited:
            out_lines.append(indent + f"{label} (已在当前路径前文展开，检测到循环，略)")
            return
        if key in expanded:
            out_lines.append(indent + f"{label} （已在当前路径前文展开，略）")
            return
        _, ref_el = reg.events[load]
        visited.add(key)
        expanded.add(key)
        out_lines.append(indent + label)
        _summarize_event(ref_el, reg, depth + 2, max_depth, visited, out_lines, expanded)
        visited.discard(key)
        return
    if load in reg.event_lists:
        key = f"L:{load}"
        indent = "  " * (depth + 1)
        label = f"→ 事件列表 {load}"
        if key in visited:
            out_lines.append(indent + f"{label} (已在当前路径前文展开，检测到循环，略)")
            return
        if key in expanded:
            out_lines.append(indent + f"{label} （已在当前路径前文展开，略）")
            return
        visited.add(key)
        expanded.add(key)
        out_lines.append(indent + f"{label}：")
        items = reg.event_lists.get(load) or []
        if not items:
            out_lines.append("  " * (depth + 2) + "(空)")
        else:
            groups: Dict[Tuple[str, str], Tuple[object, int]] = {}
            order: List[Tuple[str, str]] = []
            for it in items:
                if it.attrib.get("load"):
                    key_item = ("load", it.attrib.get("load") or "")
                else:
                    try:
                        key_item = ("inline", ET.tostring(it, encoding="utf-8").decode("utf-8", errors="ignore"))
                    except Exception:
                        key_item = ("inline_text", "".join(list(it.itertext())))
                if key_item not in groups:
                    groups[key_item] = (it, 1)
                    order.append(key_item)
                else:
                    rep, cnt = groups[key_item]
                    groups[key_item] = (rep, cnt + 1)
            total = len(items) if len(items) > 0 else 1
            for idx, key_item in enumerate(order, 1):
                rep, cnt = groups[key_item]
                p = cnt * 100.0 / total
                p_str = f"{p:.0f}%" if abs(p - round(p)) < 0.05 else f"{p:.1f}%"
                out_lines.append("  " * (depth + 2) + f"随机分支{idx} (p={p_str})：")
                if getattr(rep, "attrib", {}).get("load"):
                    _handle_event_ref(rep, reg, depth + 2, max_depth, visited, out_lines, expanded)
                else:
                    _summarize_event(rep, reg, depth + 3, max_depth, visited, out_lines, expanded)
        visited.discard(key)
        return
    out_lines.append("  " * (depth + 1) + f"→ 未知 {load} (未找到作为事件或事件列表)")



def _summarize_event(event_el, reg: Registry, depth: int, max_depth: int, visited: Set[str], out_lines: List[str], expanded: Set[str]):
    if depth > max_depth:
        out_lines.append("  " * depth + "…")
        return

    # 事件文本（缩写）
    txt_nodes = [ch for ch in list(event_el) if _strip_namespace(getattr(ch, "tag", "")) == "text"]
    txt_raw = _text_from_text_element(txt_nodes[0], reg) if txt_nodes else ""
    if txt_raw:
        out_lines.append("  " * depth + f"文本: {abbrev_text(txt_raw)}")

    # 效果
    eff = extract_effects(event_el)
    if eff:
        out_lines.append("  " * depth + f"效果: {', '.join(eff)}")

    # 选择与后续
    for ch in list(event_el):
        if _strip_namespace(getattr(ch, "tag", "")) != "choice":
            continue
        ctext_nodes = [t for t in list(ch) if _strip_namespace(getattr(t, "tag", "")) == "text"]
        ctext = _text_from_text_element(ctext_nodes[0], reg) if ctext_nodes else ""
        meta = []
        b = ch.attrib.get("blue")

        if b in ("true", "blue", "1"):
            pass
        elif b in ("false", "0"):
            pass
        if ch.attrib.get("req"):
            meta.append(f"req={ch.attrib.get('req')}")
        if ch.attrib.get("hidden") in ("true", "1"):
            meta.append("hidden")
        suffix = f" [{'; '.join(meta)}]" if meta else ""
        if b in ("false", "0"):
            suffix = suffix + _BLUE_FALSE_SENTINEL
        out_lines.append("  " * depth + f"选择: {abbrev_text(ctext)}{suffix}")

        nested_events = [t for t in list(ch) if _strip_namespace(getattr(t, "tag", "")) == "event"]
        nested_lists = [t for t in list(ch) if _strip_namespace(getattr(t, "tag", "")) == "eventList"]
        load_events = [t for t in list(ch) if _strip_namespace(getattr(t, "tag", "")) == "loadEvent"]
        load_eventlists = [t for t in list(ch) if _strip_namespace(getattr(t, "tag", "")) == "loadEventList"]

        # [1] If immediate next level (x+1) contains OPTION_INVALID, skip this choice entirely.
        try:
            invalid = False
            for ev in nested_events:
                if getattr(ev, 'attrib', {}).get('load') == 'OPTION_INVALID':
                    invalid = True
                    break
            if not invalid:
                for le in load_events:
                    if (le.text or "").strip() == 'OPTION_INVALID':
                        invalid = True
                        break
            if not invalid:
                for evl in nested_lists:
                    load = evl.attrib.get("load")
                    if load:
                        for it in (reg.event_lists.get(load) or []):
                            if getattr(it, 'attrib', {}).get('load') == 'OPTION_INVALID':
                                invalid = True
                                break
                    else:
                        for it in [t for t in list(evl) if _strip_namespace(getattr(t, "tag", "")) == "event"]:
                            if getattr(it, 'attrib', {}).get('load') == 'OPTION_INVALID':
                                invalid = True
                                break
                    if invalid:
                        break
            if not invalid:
                for lel in load_eventlists:
                    name = (lel.text or "").strip()
                    if not name:
                        continue
                    for it in (reg.event_lists.get(name) or []):
                        if getattr(it, 'attrib', {}).get('load') == 'OPTION_INVALID':
                            invalid = True
                            break
                    if invalid:
                        break
            if invalid:
                # remove the last appended line for this choice (which we emitted above)
                if out_lines and out_lines[-1].startswith("  " * depth + "ѡ��:"):
                    out_lines.pop()
                continue
        except Exception:
            pass

        if len(nested_events) > 1:
            for idx, ev in enumerate(nested_events, 1):
                out_lines.append("  " * (depth + 1) + f"→ 随机分支{idx}：")
                _handle_event_ref(ev, reg, depth + 1, max_depth, visited, out_lines, expanded)
        else:
            for ev in nested_events:
                # 单个内联事件：内容只比“选择”深一级，避免看起来跨两级
                _handle_event_ref(ev, reg, depth, max_depth, visited, out_lines, expanded)

        for evl in nested_lists:
            load = evl.attrib.get("load")
            if load:
                out_lines.append("  " * (depth + 1) + f"→ 事件列表 {load}：")
                items = reg.event_lists.get(load) or []
                if not items:
                    out_lines.append("  " * (depth + 2) + "(空)")
                else:
                    groups: Dict[Tuple[str, str], Tuple[object, int]] = {}
                    order: List[Tuple[str, str]] = []
                    for it in items:
                        if it.attrib.get("load"):
                            key = ("load", it.attrib.get("load") or "")
                        else:
                            try:
                                key = ("inline", ET.tostring(it, encoding="utf-8").decode("utf-8", errors="ignore"))
                            except Exception:
                                key = ("inline_text", "".join(list(it.itertext())))
                        if key not in groups:
                            groups[key] = (it, 1)
                            order.append(key)
                        else:
                            rep, cnt = groups[key]
                            groups[key] = (rep, cnt + 1)
                    total = len(items) if len(items) > 0 else 1
                    for idx, key in enumerate(order, 1):
                        rep, cnt = groups[key]
                        p = cnt * 100.0 / total
                        p_str = f"{p:.0f}%" if abs(p - round(p)) < 0.05 else f"{p:.1f}%"
                        out_lines.append("  " * (depth + 2) + f"随机分支{idx} (p={p_str})：")
                        if getattr(rep, 'attrib', {}).get("load"):
                            _handle_event_ref(rep, reg, depth + 2, max_depth, visited, out_lines, expanded)
                        else:
                            _summarize_event(rep, reg, depth + 3, max_depth, visited, out_lines, expanded)
            else:
                children = [t for t in list(evl) if _strip_namespace(getattr(t, "tag", "")) == "event"]
                out_lines.append("  " * (depth + 1) + f"→ 事件列表（内联）共 {len(children)} 个：")
                if not children:
                    out_lines.append("  " * (depth + 2) + "(空)")
                else:
                    groups: Dict[Tuple[str, str], Tuple[object, int]] = {}
                    order: List[Tuple[str, str]] = []
                    for it in children:
                        if it.attrib.get("load"):
                            key = ("load", it.attrib.get("load") or "")
                        else:
                            try:
                                key = ("inline", ET.tostring(it, encoding="utf-8").decode("utf-8", errors="ignore"))
                            except Exception:
                                key = ("inline_text", "".join(list(it.itertext())))
                        if key not in groups:
                            groups[key] = (it, 1)
                            order.append(key)
                        else:
                            rep, cnt = groups[key]
                            groups[key] = (rep, cnt + 1)
                    total = len(children) if len(children) > 0 else 1
                    for idx, key in enumerate(order, 1):
                        rep, cnt = groups[key]
                        p = cnt * 100.0 / total
                        p_str = f"{p:.0f}%" if abs(p - round(p)) < 0.05 else f"{p:.1f}%"
                        out_lines.append("  " * (depth + 2) + f"随机分支{idx} (p={p_str})：")
                        if getattr(rep, 'attrib', {}).get("load"):
                            _handle_event_ref(rep, reg, depth + 2, max_depth, visited, out_lines, expanded)
                        else:
                            _summarize_event(rep, reg, depth + 3, max_depth, visited, out_lines, expanded)

        for le in load_events:
            name = (le.text or "").strip()
            if not name:
                continue
            dummy = type(event_el)('event')
            dummy.attrib['load'] = name
            out_lines.append("  " * (depth + 1) + f"→ 事件 {name}")
            _handle_event_ref(dummy, reg, depth + 1, max_depth, visited, out_lines, expanded)

        for lel in load_eventlists:
            name = (lel.text or "").strip()
            if not name:
                continue
            out_lines.append("  " * (depth + 1) + f"→ 事件列表 {name}：")
            items = reg.event_lists.get(name) or []
            if not items:
                out_lines.append("  " * (depth + 2) + "(空)")
            else:
                groups: Dict[Tuple[str, str], Tuple[object, int]] = {}
                order: List[Tuple[str, str]] = []
                for it in items:
                    if it.attrib.get("load"):
                        key = ("load", it.attrib.get("load") or "")
                    else:
                        try:
                            key = ("inline", ET.tostring(it, encoding="utf-8").decode("utf-8", errors="ignore"))
                        except Exception:
                            key = ("inline_text", "".join(list(it.itertext())))
                    if key not in groups:
                        groups[key] = (it, 1)
                        order.append(key)
                    else:
                        rep, cnt = groups[key]
                        groups[key] = (rep, cnt + 1)
                total = len(items) if len(items) > 0 else 1
                for idx, key in enumerate(order, 1):
                    rep, cnt = groups[key]
                    p = cnt * 100.0 / total
                    p_str = f"{p:.0f}%" if abs(p - round(p)) < 0.05 else f"{p:.1f}%"
                    out_lines.append("  " * (depth + 2) + f"随机分支{idx} (p={p_str})：")
                    if getattr(rep, 'attrib', {}).get("load"):
                        _handle_event_ref(rep, reg, depth + 2, max_depth, visited, out_lines, expanded)
                    else:
                        _summarize_event(rep, reg, depth + 3, max_depth, visited, out_lines, expanded)

    # 嵌入战斗：展示败亡结果（优先内联，其次回退全局映射）
    for node in list(event_el):
        try:
            if _strip_namespace(getattr(node, "tag", "")) != "ship":
                continue
            if node.attrib.get("hostile", "false").lower() != "true":
                continue
            ship_key = node.attrib.get("load") or node.attrib.get("name") or "(未知)"
            out_lines.append("  " * depth + f"战斗: {ship_key}")

            inline = {k: [t for t in list(node) if _strip_namespace(getattr(t, "tag", "")) == k]
                      for k in ("surrender", "destroyed", "deadCrew", "escape", "gotaway")}
            defs = reg.ship_defs.get(ship_key) if hasattr(reg, 'ship_defs') else None

            def nodes_for(key: str):
                if inline.get(key):
                    return inline[key]
                if not defs:
                    return []
                ent = defs.get(key)
                if not ent:
                    return []
                if 'load' in ent:
                    d = type(event_el)('event')
                    d.attrib['load'] = ent['load']
                    return [d]
                if 'el' in ent:
                    return [ent['el']]
                return []

            for k, label in (("surrender", "投降"), ("destroyed", "摧毁(胜利)"), ("deadCrew", "船员全灭(胜利)"), ("escape", "尝试逃跑"), ("gotaway", "敌舰逃走")):
                subs = nodes_for(k)
                if not subs:
                    continue
                out_lines.append("  " * (depth + 1) + f"{label}:")
                for sub in subs:
                    if hasattr(sub, 'attrib') and sub.attrib.get('load'):
                        if sub.attrib.get('load') == 'COMBAT_CHECK':
                            continue
                        _handle_event_ref(sub, reg, depth + 1, max_depth, visited, out_lines, expanded)
                    else:
                        _summarize_event(sub, reg, depth + 2, max_depth, visited, out_lines, expanded)
        except Exception:
            continue


def show_single_event_detail(entry, query: str, reg: Registry, max_depth: int = 3, only_outcomes: bool = False, max_line_len: int = 80):
    """展开单一事件: 定位文本、展示分支与效果。

    输入:
    - entry: 事件条目
    - query: 命中的中文子串
    - reg: Registry
    - max_depth: 最大递归展开深度
    - only_outcomes: 仅显示战斗结算（无结算则回退完整分支）
    - max_line_len: 单行最大长度
    """
    if getattr(entry, "kind", "event") == "eventList":
        print(f"匹配事件列表: {entry.name} ({entry.file})")
        if entry.name not in getattr(reg, "event_lists", {}):
            print("未找到该事件列表的定义。")
            return
        fake_event = ET.Element("event")
        load_el = ET.Element("loadEventList")
        load_el.text = entry.name
        fake_event.append(load_el)
        lines: List[str] = []
        expanded: Set[str] = set()
        _summarize_event(fake_event, reg, depth=0, max_depth=max_depth, visited=set(), out_lines=lines, expanded=expanded)
        if only_outcomes:
            keys = ("战斗", "投降", "摧毁", "船员全灭", "奖励", "敌舰逃走")
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
            print(clip_line(ln, max_line_len))
        return

    root = _parse_xml_etree(entry.file)
    if root is None:
        print("[警告] 无法解析该事件文件，跳过详细展开。")
        return

    # Prefer the richest definition when multiple <event name=...> exist
    # (e.g., event-list placeholder items vs the full event body).
    target_event_el = None
    best_children = -1
    for name, el in _iter_named_events_etree(root):
        if name != entry.name:
            continue
        try:
            ch_cnt = len(list(el))
        except Exception:
            ch_cnt = 0
        if ch_cnt > best_children:
            best_children = ch_cnt
            target_event_el = el
    if target_event_el is None:
        print("[警告] 在文件中未找到目标事件。")
        return

    match_node, s = _first_matching_node(target_event_el, query)
    print(f"匹配事件: {entry.name} ({entry.file})")
    if match_node is None or not s:
        print("定位文本: 未能在事件内部精确定位（可能匹配来自子事件/列表）。")
        lines: List[str] = []
        expanded: Set[str] = set()
        _summarize_event(target_event_el, reg, depth=0, max_depth=max_depth, visited=set(), out_lines=lines, expanded=expanded)
    else:
        i = s.find(query)
        pre = s[max(0, i - 20):i]
        post = s[i + len(query): i + len(query) + 20]
        print(f"定位文本: …{pre}[{query}]{post}…")
        path = _find_ancestor_path(target_event_el, match_node)
        choice_ancestor = None
        for anc in reversed(path):
            if _strip_namespace(getattr(anc, "tag", "")) == "choice":
                choice_ancestor = anc
                break
        lines: List[str] = []
        expanded: Set[str] = set()
        if choice_ancestor is not None:
            fake_event = type(target_event_el)('event')
            fake_event.append(choice_ancestor)
            _summarize_event(fake_event, reg, depth=0, max_depth=max_depth, visited=set(), out_lines=lines, expanded=expanded)
        else:
            _summarize_event(target_event_el, reg, depth=0, max_depth=max_depth, visited=set(), out_lines=lines, expanded=expanded)

    # 仅显示战斗结算（如没有，则回退完整）
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
        print(clip_line(ln, max_line_len))

