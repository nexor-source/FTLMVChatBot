# -*- coding: utf-8 -*-
"""QQ bot entry integrated with /find (ftl_search).

Features
- @bot + "/find <关键词>": search FTL MV events and reply.
- Single match: try to send an image (via configured URL) or fallback to text.
- Multiple matches: list names or ask for narrower keywords.
"""
from __future__ import annotations

import io
import os
import re
import sys
import asyncio
import random
from pathlib import Path
from contextlib import redirect_stdout

import botpy
from botpy import logging
from botpy.ext.cog_yaml import read
from botpy.message import GroupMessage

from ftl_search.registry import index_events, build_registry, EventEntry, Registry
from ftl_search.summarize import show_single_event_detail


# Ensure console prints UTF-8 (best effort)
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass


ROOT = Path(__file__).resolve().parent
CFG = read(str(ROOT / "QQ_bot" / "config.yaml"))
_log = logging.get_logger()


def _locate_data_dir() -> Path:
    """Locate data directory in this order:
    1) env FTL_DATA_DIR; 2) parents that contain ./data; 3) ./data.
    """
    env = os.environ.get("FTL_DATA_DIR")
    if env:
        p = Path(env)
        if p.is_dir():
            return p
    base = ROOT
    for b in [base] + list(base.parents):
        cand = b / "data"
        if cand.is_dir():
            return cand
    return base / "data"


DATA_DIR = _locate_data_dir()
print(f"索引构建: {DATA_DIR} ...", flush=True)
REG = build_registry(DATA_DIR)
ENTRIES = index_events(DATA_DIR)
print(f"索引完成: 事件 {len(ENTRIES)} 条（仅具名 <event>）", flush=True)


def _search(entries: list[EventEntry], query: str) -> list[str]:
    q = (query or "").strip()
    if not q:
        return []
    q_nows = "".join(q.split())
    seen = set()
    results: list[str] = []
    for e in entries:
        # Prioritize exact event-id match (case-insensitive), like CLI
        try:
            if e.name == q or e.name.lower() == q.lower():
                if e.name not in seen:
                    seen.add(e.name)
                    results.append(e.name)
                continue
        except Exception:
            pass
        # Substring match against event text
        if q in e.text or (q_nows and q_nows in e.text_nows):
            if e.name not in seen:
                seen.add(e.name)
                results.append(e.name)
    return results


def _minimal_events(names: list[str], reg: Registry) -> list[str]:
    name_set = set(names)
    drop = set()
    for m in names:
        for a in reg.event_ancestors.get(m, []):
            if a in name_set:
                drop.add(a)
    return [n for n in names if n not in drop]


def _strip_file_path_from_header(text: str) -> str:
    if not text:
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("匹配事件:"):
        m = re.match(r"^(匹配事件:\s*[^\(]+)\s*\(.*\)\s*$", lines[0])
        if m:
            lines[0] = m.group(1)
        return "\n".join(lines)
    return text


def _summarize_single_event_to_text(
    entry: EventEntry,
    query: str,
    max_depth: int = 16,
    only_outcomes: bool = False,
    max_len: int = 1600,
) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        show_single_event_detail(entry, query, REG, max_depth=max_depth, only_outcomes=only_outcomes, max_line_len=100)
    out = _strip_file_path_from_header(buf.getvalue().strip())
    if max_len and len(out) > max_len:
        return out[: max_len] + "\n…(输出过长，已截断)"
    return out or "(无输出)"


# (removed) event-id exact match lookup


def _choose_font():
    from PIL import ImageFont
    candidates = [
        os.environ.get("FONT_PATH"),
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "arial.ttf",
    ]
    for path in candidates:
        if not path:
            continue
        try:
            return ImageFont.truetype(path, 18)
        except Exception:
            continue
    return ImageFont.load_default()


def save_text_as_image(text: str, out_dir: Path | None = None) -> Path | None:
    """Render styled text (chips + indent guides) into PNG and return path."""
    try:
        from PIL import Image, ImageDraw
    except Exception:
        _log.warning("Pillow 未安装，无法生成图片。请安装: pip install Pillow")
        return None

    out_dir = out_dir or (ROOT / "outputs")
    out_dir.mkdir(parents=True, exist_ok=True)

    font = _choose_font()
    pad_x, pad_y = 18, 18
    max_width = 1000
    indent_w = 26
    line_gap = 6
    guide_color = (210, 210, 210)

    # helper canvas
    temp_img = Image.new("RGB", (10, 10), "white")
    draw = ImageDraw.Draw(temp_img)

    # palette for chips
    palette = {
        'text':   ((240, 240, 240), (110, 110, 110)),
        'effect': ((234, 255, 234), (34, 120, 34)),
        'choice': ((255, 242, 204), (180, 120, 0)),
        'event':  ((243, 229, 245), (140, 30, 150)),
        'elist':  ((237, 231, 246), (120, 70, 160)),
        'branch': ((230, 240, 255), (30, 80, 160)),
        'combat': ((255, 235, 238), (198, 40, 40)),
        'title':  ((224, 247, 250), (0, 121, 107)),
        'loc':    ((224, 242, 241), (0, 121, 107)),
        'normal': (None, (0, 0, 0)),
    }

    # classify a single raw line
    def classify(raw: str):
        s = raw.rstrip("\r\n")
        depth = 0
        while s.startswith("  "):
            depth += 1
            s = s[2:]
        kind = 'normal'; label = None; content = s
        if s.startswith("文本:"):
            kind, label, content = 'text', '文本', s[3:].strip()
        elif s.startswith("效果:"):
            kind, label, content = 'effect', '效果', s[3:].strip()
        elif s.startswith("选择:"):
            kind, label, content = 'choice', '选择', s[3:].strip()
        elif s.startswith("→ 事件列表"):
            kind, label, content = 'elist', '列表', s.replace("→ 事件列表", "").strip(" ：:")
        elif s.startswith("→ 事件"):
            kind, label, content = 'event', '事件', s.replace("→ 事件", "", 1).strip(" ：:")
        elif s.startswith("随机分支"):
            kind, label, content = 'branch', '分支', s
        elif s.startswith("战斗:"):
            kind, label, content = 'combat', '战斗', s.split(":", 1)[1].strip()
        elif s.startswith("匹配事件:"):
            depth, kind, label, content = 0, 'title', '匹配', s.split(":", 1)[1].strip()
        elif s.startswith("定位文本:"):
            depth, kind, label, content = 0, 'loc', '定位', s.split(":", 1)[1].strip()
        return depth, kind, label, content

    # measure line height
    try:
        bbox = draw.textbbox((0, 0), 'Ag', font=font)
        line_h = bbox[3] - bbox[1]
    except Exception:
        line_h = 22

    def wrap_to_width(s: str, width_px: int) -> list[str]:
        if width_px <= 20:
            return [s]
        out: list[str] = []
        cur = ''
        for ch in s:
            t = cur + ch
            if draw.textlength(t, font=font) > width_px and cur:
                out.append(cur)
                cur = ch
            else:
                cur = t
        if cur or not out:
            out.append(cur)
        return out

    # parse and layout
    items = []  # (depth, kind, label, [wrapped lines])
    for raw in text.splitlines():
        depth, kind, label, content = classify(raw)
        base_x = pad_x + depth * indent_w
        chip_w = (draw.textlength(label, font=font) + 12) if label else 0
        gap = 8 if label else 0
        avail = max_width - base_x - chip_w - gap - pad_x
        lines = wrap_to_width(content, int(avail)) if content else ['']
        items.append((depth, kind, label, lines))

    total_rows = sum(len(it[3]) for it in items)
    height = pad_y * 2 + total_rows * (line_h + line_gap)
    img = Image.new('RGB', (max_width, height), 'white')
    d2 = ImageDraw.Draw(img)

    y = pad_y
    for depth, kind, label, lines in items:
        base_x = pad_x + depth * indent_w
        # indent guides covering this block height
        block_h = (line_h + line_gap) * len(lines) - line_gap
        for i in range(depth):
            xg = pad_x + i * indent_w + 2
            d2.line([(xg, y + 2), (xg, y + block_h)], fill=guide_color, width=1)

        bg, fg = palette.get(kind, palette['normal'])
        first = True
        for ln in lines:
            x = base_x
            if first and label:
                # 为有颜色的标签附加 "-depth"
                chip_label = label
                if bg is not None:
                    chip_label = f"{label}-{depth}"
                chip_w = int(draw.textlength(chip_label, font=font) + 12)
                chip_h = line_h
                if bg:
                    try:
                        d2.rounded_rectangle([x, y, x + chip_w, y + chip_h], radius=6, fill=bg, outline=None)
                    except Exception:
                        d2.rectangle([x, y, x + chip_w, y + chip_h], fill=bg)
                d2.text((x + 6, y), chip_label, font=font, fill=fg)
                x += chip_w + 8
            # “效果/战斗”行：为内容区域绘制同标签色的背景
            if ln and kind in ("effect", "combat") and palette.get(kind, (None, None))[0] is not None:
                try:
                    content_w = int(draw.textlength(ln, font=font)) + 8
                    content_h = line_h
                    try:
                        d2.rounded_rectangle([x, y, x + content_w, y + content_h], radius=6, fill=bg, outline=None)
                    except Exception:
                        d2.rectangle([x, y, x + content_w, y + content_h], fill=bg)
                except Exception:
                    pass
            d2.text((x, y), ln, font=font, fill=(0, 0, 0))
            y += line_h + line_gap
            first = False

    # save file
    import time, unicodedata
    ts = time.strftime("%Y%m%d_%H%M%S")
    first_line = text.splitlines()[0] if text.splitlines() else 'output'
    base = unicodedata.normalize('NFKD', first_line)
    base = re.sub(r'[^A-Za-z0-9_-]+', '_', base)[:48] or 'output'
    fp = out_dir / f"find_{ts}_{base}.png"
    try:
        img.save(fp)
        _log.info(f"已保存文本图片: {fp}")
        return fp
    except Exception as e:
        _log.warning(f"保存图片失败: {e}")
        return None


# Image reply via URL (config driven)
IMAGE_UPLOAD_ENABLED = bool((CFG.get("image_upload") or {}).get("enabled", False))
IMAGE_BASE_URL = None
if CFG.get("image_server") and CFG["image_server"].get("base_url"):
    IMAGE_BASE_URL = str(CFG["image_server"]["base_url"]).rstrip("/")
IMAGE_CLEANUP_DELAY = 120
try:
    IMAGE_CLEANUP_DELAY = int((CFG.get("image_cleanup") or {}).get("delay_seconds", IMAGE_CLEANUP_DELAY))
except Exception:
    pass


async def _send_image_if_configured(api: botpy.BotAPI, message: GroupMessage, image_path: Path | None) -> bool:
    if not image_path:
        _log.info("图片路径为空，跳过发送")
        return False
    if not IMAGE_UPLOAD_ENABLED or not IMAGE_BASE_URL:
        _log.info("图片发送未启用或缺少 base_url，改为文本回退")
        return False
    try:
        from urllib.parse import quote
        pic_url = f"{IMAGE_BASE_URL}/{quote(image_path.name)}"
        _log.info(f"尝试图片 URL: {pic_url}")
        upload_media = await api.post_group_file(
            group_openid=message.group_openid,
            file_type=1,
            url=pic_url,
        )
        await message.reply(msg_type=7, media=upload_media)
        _log.info("图片已通过 URL 回复")
        return True
    except Exception as e:
        _log.warning(f"图片富媒体回复失败: {e}")
        return False


async def _cleanup_image_later(image_path: Path, delay: int = IMAGE_CLEANUP_DELAY) -> None:
    try:
        await asyncio.sleep(delay)
        image_path.unlink(missing_ok=True)
        _log.info(f"已删除图片: {image_path}")
    except Exception:
        pass


async def handle_find(api: botpy.BotAPI, message: GroupMessage, query: str):
    q = (query or "").strip()
    if not q:
        await message.reply(content="用法: /find 关键词")
        return

    names = _search(ENTRIES, q)
    names = _minimal_events(names, REG)
    if not names:
        await message.reply(content="未找到匹配事件")
        return

    if len(names) == 1:
        entry = next(e for e in ENTRIES if e.name == names[0])
        # Try image reply first
        try:
            full_text = _summarize_single_event_to_text(entry, q, max_len=10_000_000)
            img_path = save_text_as_image(full_text)
            sent = await _send_image_if_configured(api, message, img_path)
            # Always schedule cleanup to avoid lingering files
            if img_path:
                asyncio.create_task(_cleanup_image_later(img_path))
            if sent:
                return
        except Exception:
            pass

        # Fallback to text (chunked)
        text = _summarize_single_event_to_text(entry, q)
        chunks: list[str] = []
        cur: list[str] = []
        cur_len = 0
        for line in text.splitlines():
            ln = line.rstrip()
            if cur_len + len(ln) + 1 > 1800:
                chunks.append("\n".join(cur))
                cur = [ln]
                cur_len = len(ln) + 1
            else:
                cur.append(ln)
                cur_len += len(ln) + 1
        if cur:
            chunks.append("\n".join(cur))
        for ch in chunks:
            await message.reply(content=ch)
        return

    # Multiple results
    if len(names) > 5:
        await message.reply(content=f"匹配事件过多：{len(names)} 个（请提供更具体的关键词）")
    else:
        await message.reply(content="\n".join(names))


class MyClient(botpy.Client):
    async def on_ready(self):
        _log.info(f'robot "{self.robot.name}" is ready!')

    async def on_group_at_message_create(self, message: GroupMessage):
        content = (message.content or "").strip()
        text = content.strip()
        low = text.lower()
        if low.startswith("/find"):
            query = text[5:].strip()
            await handle_find(self.api, message, query)
            return

        # Not a /find command: send usage hint
        tip = "使用说明：发送 /find 关键词 来检索事件。例如：/find 维修"
        await message.reply(content=tip)


def run_bot() -> None:
    intents = botpy.Intents(public_messages=True)
    client = MyClient(intents=intents, is_sandbox=True)
    client.run(appid=CFG["appid"], secret=CFG["secret"]) 


if __name__ == "__main__":
    run_bot()
