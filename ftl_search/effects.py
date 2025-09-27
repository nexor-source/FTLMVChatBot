"""效果汇总工具：从事件节点提取奖励/惩罚等摘要。

当前支持:
- autoReward, item_modify/item, weapon/drone/augment, crewMember
- ship(hostile=true) 标记 combat
- status, system, damage/repair
- modifyPursuit 叛军追击调整
- variable 变量变化（含 rep_* 恶名）
"""

from __future__ import annotations

from typing import List

from .registry import _strip_namespace


def extract_effects(event_el) -> List[str]:
    effects: List[str] = []

    def walk(node):
        for ch in list(node):
            tag = _strip_namespace(getattr(ch, "tag", ""))
            if tag == "choice":
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
                        rng = (f"{mn}..{mx}" if mn and mx and mn != mx else (mn or mx or "?"))
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
            elif tag == "upgrade":
                sysname = ch.attrib.get("system") or "?"
                amt = ch.attrib.get("amount") or "?"
                sign = "" if str(amt).startswith("-") else "+"
                effects.append(f"upgrade {sysname} {sign}{amt}")
            elif tag == "quest":
                ev = ch.attrib.get("event") or "?"
                effects.append(f"quest: {ev}")
            elif tag == "removeCrew":
                amt = ch.attrib.get("amount") or "1"
                clone_ok = False
                for gc in list(ch):
                    if _strip_namespace(getattr(gc, "tag", "")) == "clone":
                        if (gc.text or "").strip().lower() in ("true", "1"):
                            clone_ok = True
                            break
                eff = f"crew -{amt}"
                # 展示更清晰：标注可被克隆舱复活
                if clone_ok:
                    eff += " (可克隆复活)"
                effects.append(eff)
            elif tag == "variable":
                name = ch.attrib.get("name")
                op = (ch.attrib.get("op") or "").lower()
                val = ch.attrib.get("val") or ch.attrib.get("amount") or "?"
                if name and name.startswith("rep_"):
                    faction = name[4:]
                    if op == "add":
                        sign = "" if str(val).startswith("-") else "+"
                        effects.append(f"rep_{faction} {sign}{val}")
                    elif op == "sub":
                        effects.append(f"rep_{faction} -{val}")
                    else:
                        effects.append(f"rep_{faction} {op} {val}")
                else:
                    if op == "add":
                        sign = "" if str(val).startswith("-") else "+"
                        effects.append(f"var {name} {sign}{val}")
                    else:
                        effects.append(f"var {name} {op} {val}")
            elif tag == "modifyPursuit":
                amt = ch.attrib.get("amount")
                if amt is None or amt == "":
                    effects.append("pursuit ?")
                else:
                    sign = "" if str(amt).startswith("-") else "+"
                    effects.append(f"pursuit {sign}{amt}")
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
