"""效果汇总工具：从事件节点提取奖励/惩罚等摘要。

当前支持:
- autoReward, item_modify/item, weapon/drone/augment, crewMember
- ship(hostile=true) 标记 combat
- status, system, damage/repair
- modifyPursuit 叛军追击调整
- variable 变量变化（含 rep_* 恶名）
"""

from __future__ import annotations

import re
from typing import List

from .registry import _strip_namespace

_TERM_TRANSLATIONS = {
    "autoreward": "自动奖励",
    "scrap": "~",
    "scrap_only": "~奖励",
    "fuel": "{",
    "fuel_only": "{奖励",
    "missile": "导弹",
    "missiles": "}",
    "missle": "}",
    "missile_only": "}奖励",
    "missiles_only": "}奖励",
    "missle_only": "}奖励",
    "droneparts": "|",
    "drone_parts": "|",
    "drone-part": "|",
    "drone": "无人机",
    "droneparts_only": "|奖励",
    "crew": "船员",
    "boarders": "敌方登舰",
    "pursuit": "追击进度",
    "status": "状态",
    "upgrade": "系统升级",
    "system": "系统",
    "quest": "任务",
    "store": "商店",
    "unlock": "解锁",
    "weapon": "武器",
    "augment": "部件",
    "var": "变量",
    "damage": "损伤",
    "repair": "修复",
    "level": "等级",
    "standard": "标准奖励",
    "low": "低级",
    "med": "中级",
    "high": "高级",
}

_TERM_PATTERNS = [
    (re.compile(rf"\b{re.escape(src)}\b", re.IGNORECASE), dst)
    for src, dst in _TERM_TRANSLATIONS.items()
]


def _localize_effect_text(text: str) -> str:
    """Replace common outcome tokens with Chinese equivalents."""
    localized = text
    for pattern, repl in _TERM_PATTERNS:
        localized = pattern.sub(repl, localized)
    return localized


def extract_effects(event_el) -> List[str]:
    effects: List[str] = []

    def walk(node):
        for ch in list(node):
            tag = _strip_namespace(getattr(ch, "tag", ""))
            if tag == "choice":
                continue
            if tag == "autoReward":
                val = (ch.text or "").strip()
                lvl = (ch.attrib.get("level") or "").strip()
                if lvl:
                    lvl_cn = {
                        "standard": "标准",
                        "low": "低级",
                        "med": "中级",
                        "medium": "中级",
                        "mid": "中级",
                        "high": "高级",
                    }.get(lvl.lower(), lvl)
                    if val:
                        effects.append(f"{lvl_cn}奖励（{val}）")
                    else:
                        effects.append(f"{lvl_cn}奖励")
                else:
                    if val:
                        effects.append(f"奖励（{val}）")
                    else:
                        effects.append("奖励")
            elif tag == "item_modify":
                for it in list(ch):
                    if _strip_namespace(getattr(it, "tag", "")) == "item":
                        typ = it.attrib.get("type")
                        mn = it.attrib.get("min")
                        mx = it.attrib.get("max")
                        rng = (f"{mn}到{mx}" if mn and mx and mn != mx else (mn or mx or "?"))
                        effects.append(f"{typ} {rng}")
            elif tag in ("weapon", "drone", "augment"):
                nm = ch.attrib.get("name")
                effects.append(f"+{tag}:{nm}")
            elif tag == "crewMember":
                amt = ch.attrib.get("amount") or ch.attrib.get("count") or "1"
                cls = ch.attrib.get("class") or ch.attrib.get("type")
                # optional skill hints
                extras = []
                for k in ("pilot", "combat", "repair", "shields", "engines", "weapons", "all_skills"):
                    v = ch.attrib.get(k)
                    if v:
                        if k == "all_skills":
                            extras.append(f"all_skills={v}")
                        else:
                            extras.append(f"{k}+{v}")
                name_txt = (ch.text or "").strip()
                name_part = f" name={name_txt}" if name_txt else ""
                extra_part = f" ({', '.join(extras)})" if extras else ""
                effects.append(f"+crew x{amt}{(' '+cls) if cls else ''}{name_part}{extra_part}")
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
                # 展示更清晰：标注可由克隆舱复活
                if clone_ok:
                    eff += " (可由克隆舱复活)"
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
            elif tag == "boarders":
                # 入侵者：min/max 或 amount，class 表示类别
                cls = ch.attrib.get("class") or ch.attrib.get("race") or "?"
                mn = ch.attrib.get("min")
                mx = ch.attrib.get("max")
                amt = ch.attrib.get("amount")
                if mn or mx:
                    rng = f"{mn}到{mx}" if mn and mx else (mn or mx or "?")
                else:
                    rng = amt or "?"
                effects.append(f"boarders {cls} {rng}")
            elif tag == "system":
                nm = ch.attrib.get("name")
                effects.append(f"system:{nm}")
            elif tag in ("damage", "repair"):
                amt = ch.attrib.get("amount")
                effects.append(f"{tag}:{amt}")
            elif tag == "store":
                sid = (ch.text or "").strip()
                if sid:
                    effects.append(f"store: {sid}")
                else:
                    effects.append("store")
            elif tag == "unlockCustomShip":
                ship = (ch.text or "").strip() or ch.attrib.get("id") or "?"
                effects.append(f"unlock: {ship}")
            elif tag == "removeItem":
                item = (ch.text or "").strip()
                if item:
                    effects.append(f"-item: {item}")
                else:
                    effects.append("-item")
            walk(ch)

    walk(event_el)
    seen = set()
    out: List[str] = []
    for e in effects:
        localized = _localize_effect_text(e)
        if localized not in seen:
            seen.add(localized)
            out.append(localized)
    return out
