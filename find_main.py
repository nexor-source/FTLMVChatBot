# -*- coding: utf-8 -*-
"""QQ bot entry for /find (FTL MV).

Simplified per requested logic:
- Exact event-id match (case-insensitive) short-circuits and expands only that event.
- Text search scans each <event> node's own text only:
  - Skip nested <event>, <loadEvent>, <loadEventList> entirely.
  - Include text from <text load="..."> (textList) only.
- If a match lies in an anonymous event, map to the nearest named ancestor event.
"""
from __future__ import annotations

import os
import re
import sys
import time
import asyncio
import unicodedata
from pathlib import Path
from typing import List

import botpy
from botpy import logging
from botpy.ext.cog_yaml import read
from botpy.message import GroupMessage

# No need to prebuild registry here; search_once constructs what it needs
from ftl_search.cli import search_once


# Ensure console prints UTF-8 (best effort)
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass


ROOT = Path(__file__).resolve().parent
CFG = read(str(ROOT / "QQ_bot" / "config.yaml"))
_log = logging.get_logger()

# Invisible sentinel to carry blue=false without showing in output text
_BLUE_FALSE_SENTINEL = "\u2063\u2063\u2063\u2063"

_DEBUG_TIMING = os.environ.get("FTL_SEARCH_TIMING", "").lower() not in {"", "0", "false", "off"}


def _locate_data_dir() -> Path:
    env = os.environ.get("FTL_DATA_DIR")
    if env and Path(env).is_dir():
        return Path(env)
    base = ROOT
    for b in [base] + list(base.parents):
        cand = b / "data"
        if cand.is_dir():
            return cand
    return base / "data"


DATA_DIR = _locate_data_dir()
print(f"索引路径: {DATA_DIR} ...", flush=True)
print(f"索引路径: {DATA_DIR} ...", flush=True)



def _strip_file_path_from_header(text: str) -> str:
    if not text:
        return text
    lines = text.splitlines()
    if not lines:
        return text
    # Try to drop "(path)" in first line if present
    header = lines[0]
    m = re.match(r"^\(([^)]+)\)\s*(.*)$", header)
    if m:
        remainder = m.group(2).strip()
        lines[0] = remainder or m.group(1)
        return "\n".join(lines)
    return text


def _choose_font():
    from PIL import ImageFont
    candidates = [
        os.environ.get("FONT_PATH"),
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "/System/Library/Fonts/PingFang.ttc",
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
        _log.info("Pillow 未安装，跳过图片渲染")
        return None

    out_dir = out_dir or (ROOT / "outputs")
    out_dir.mkdir(parents=True, exist_ok=True)

    font = _choose_font()
    pad_x, pad_y = 18, 18
    max_width = 1000
    indent_w = 26
    line_gap = 6
    guide_color = (210, 210, 210)

    temp_img = Image.new("RGB", (10, 10), "white")
    draw = ImageDraw.Draw(temp_img)
    try:
        bbox = draw.textbbox((0, 0), "Ag", font=font)
        line_h = bbox[3] - bbox[1]
    except Exception:
        line_h = 22

    palette = {
        "text":   ((240, 240, 240), (110, 110, 110)),
        "effect": ((234, 255, 234), (34, 120, 34)),
        "choice": ((255, 242, 204), (180, 120, 0)),
        "event":  ((243, 229, 245), (140, 30, 150)),
        "elist":  ((237, 231, 246), (120, 70, 160)),
        "branch": ((230, 240, 255), (30, 80, 160)),
        "combat": ((255, 235, 238), (198, 40, 40)),
        "title":  ((224, 247, 250), (0, 121, 107)),
        "loc":    ((224, 242, 241), (0, 121, 107)),
        "normal": (None, (0, 0, 0)),
    }

    def classify(raw: str) -> tuple[int, str, str | None, str]:
        s = raw.rstrip("\r\n")
        # Strip invisible sentinel markers from visible content
        try:
            s = s.replace(_BLUE_FALSE_SENTINEL, "")
        except Exception:
            pass

        depth = 0
        while s.startswith("  "):
            depth += 1
            s = s[2:]
        kind = "normal"
        label: str | None = None
        content = s
        if s.startswith("文本:"):
            kind, label, content = "text", "文本", s[3:].strip()
        elif s.startswith("效果:"):
            kind, label, content = "effect", "效果", s[3:].strip()
        elif s.startswith("选择:"):
            kind, label, content = "choice", "选择", s[3:].strip()
        elif s.startswith("→ 事件列表"):
            kind, label, content = "elist", "列表", s.replace("→ 事件列表", "", 1).strip(" ：:")
        elif s.startswith("→ 事件"):
            kind, label, content = "event", "事件", s.replace("→ 事件", "", 1).strip(" ：:")
        elif s.startswith("随机分支"):
            kind, label, content = "branch", "分支", s
        elif s.startswith("战斗:"):
            kind, label, content = "combat", "战斗", s.split(":", 1)[1].strip()
        elif s.startswith("匹配事件:"):
            depth, kind, label, content = 0, "title", "匹配", s.split(":", 1)[1].strip()
        elif s.startswith("定位文本:"):
            depth, kind, label, content = 0, "loc", "定位", s.split(":", 1)[1].strip()
        return depth, kind, label, content

    def wrap_to_width(s: str, width_px: int) -> list[str]:
        if width_px <= 20:
            return [s]
        out: list[str] = []
        cur = ""
        for ch in s:
            candidate = cur + ch
            if draw.textlength(candidate, font=font) > width_px and cur:
                out.append(cur)
                cur = ch
            else:
                cur = candidate
        if cur or not out:
            out.append(cur)
        return out

    items: list[tuple[int, str, str | None, list[str], bool]] = []
    for raw in text.splitlines():
        depth, kind, label, content = classify(raw)
        base_x = pad_x + depth * indent_w
        chip_w = int(draw.textlength(label, font=font) + 12) if label else 0
        gap = 8 if label else 0
        avail = max_width - base_x - chip_w - gap - pad_x
        if avail <= 40:
            avail = max_width - pad_x * 2
        lines = wrap_to_width(content, int(avail)) if content else [""]
        has_req = kind == "choice" and "[req=" in raw and (_BLUE_FALSE_SENTINEL not in raw)
        items.append((depth, kind, label, lines, has_req))

    total_rows = sum(len(block) for _, _, _, block, _ in items)
    height = pad_y * 2 + total_rows * (line_h + line_gap)
    img = Image.new("RGB", (max_width, height), "white")
    painter = ImageDraw.Draw(img)

    y = pad_y
    for depth, kind, label, lines, has_req in items:
        base_x = pad_x + depth * indent_w
        block_h = (line_h + line_gap) * len(lines) - line_gap
        for i in range(depth):
            xg = pad_x + i * indent_w + 2
            painter.line([(xg, y + 2), (xg, y + block_h)], fill=guide_color, width=1)

        bg, fg = palette.get(kind, palette["normal"])
        first_line = True
        for ln in lines:
            x = base_x
            if first_line and label:
                chip_label = label
                if bg is not None:
                    chip_label = f"{label}-{depth}"
                chip_w = int(draw.textlength(chip_label, font=font) + 12)
                chip_h = line_h
                if bg:
                    try:
                        painter.rounded_rectangle([x, y, x + chip_w, y + chip_h], radius=6, fill=bg, outline=None)
                    except Exception:
                        painter.rectangle([x, y, x + chip_w, y + chip_h], fill=bg)
                painter.text((x + 6, y), chip_label, font=font, fill=fg)
                x += chip_w + 8
            if ln and kind in {"effect", "combat"} and bg is not None:
                try:
                    content_w = int(draw.textlength(ln, font=font)) + 8
                    content_h = line_h
                    try:
                        painter.rounded_rectangle([x, y, x + content_w, y + content_h], radius=6, fill=bg, outline=None)
                    except Exception:
                        painter.rectangle([x, y, x + content_w, y + content_h], fill=bg)
                except Exception:
                    pass
            text_color = (0, 0, 0)
            if has_req:
                text_color = (0, 102, 255)
            painter.text((x, y), ln, font=font, fill=text_color)
            y += line_h + line_gap
            first_line = False

    ts = time.strftime("%Y%m%d_%H%M%S")
    first_line_text = text.splitlines()[0] if text.splitlines() else "output"
    base = unicodedata.normalize("NFKD", first_line_text)
    base = re.sub(r"[^A-Za-z0-9_-]+", "_", base)[:48] or "output"
    fp = out_dir / f"find_{ts}_{base}.png"
    try:
        img.save(fp)
        _log.info(f"已保存图片: {fp}")
        return fp
    except Exception as exc:
        _log.warning(f"保存图片失败: {exc}")
        return None


# Image reply via URL (config driven)
IMAGE_UPLOAD_ENABLED = bool((CFG.get("image_upload") or {}).get("enabled", False))
IMAGE_BASE_URL = None
if CFG.get("image_server") and CFG["image_server"].get("base_url"):
    IMAGE_BASE_URL = str(CFG["image_server"]["base_url"]).rstrip("/")
IMAGE_CLEANUP_DELAY = int((CFG.get("image_cleanup") or {}).get("delay_seconds", 120))


TIP_FIND = "用法: /find 关键字 —— 查询 FTL MV 事件"
TIP_FIND_ID = "用法: /findid 事件ID —— 展开指定事件"
HELP_TEXT = "\n".join(
    [
        "可用指令：",
        "/find 关键字 —— 按文本关键字检索事件",
        "/findid 事件ID —— 直接展开指定事件",
        "/help —— 查看全部指令说明",
    ]
)


async def _send_image_if_configured(api: botpy.BotAPI, message: GroupMessage, image_path: Path | None) -> bool:
    if not image_path:
        return False
    if not IMAGE_UPLOAD_ENABLED or not IMAGE_BASE_URL:
        return False
    try:
        from urllib.parse import quote
        pic_url = f"{IMAGE_BASE_URL}/{quote(image_path.name)}"
        upload_media = await api.post_group_file(
            group_openid=message.group_openid,
            file_type=1,
            url=pic_url,
        )
        await message.reply(msg_type=7, media=upload_media)
        return True
    except Exception:
        return False


async def _cleanup_image_later(image_path: Path, delay: int = IMAGE_CLEANUP_DELAY) -> None:
    try:
        await asyncio.sleep(delay)
        image_path.unlink(missing_ok=True)
    except Exception:
        pass


async def handle_find_new(
    api: botpy.BotAPI,
    message: GroupMessage,
    query: str,
    *,
    mode: str = "text",
) -> None:
    q = (query or "").strip()
    tip = TIP_FIND_ID if mode == "id" else TIP_FIND
    if not q:
        await message.reply(content=tip)
        return

    timing_start = time.perf_counter() if _DEBUG_TIMING else None
    res = search_once(q, DATA_DIR, max_depth=16, only_outcomes=False, mode=mode)
    if timing_start is not None:
        elapsed = time.perf_counter() - timing_start
        sample = q if len(q) <= 30 else q[:27] + "..."
        print(f"[timing] search_once {elapsed:.3f}s (mode={mode}, query={sample})", flush=True)
    kind = res.get("kind")
    if kind == "empty_query":
        await message.reply(content=tip)
        return
    if kind == "not_found":
        not_found_msg = "未找到匹配的事件ID。" if mode == "id" else "未找到匹配事件。"
        await message.reply(content=not_found_msg)
        return
    if kind == "names":
        names = list(res.get("names") or [])
        if not names:
            return
        if len(names) > 5:
            reply_text = f"匹配到 {len(names)} 个事件，请提供更具体的关键词。"
            _log.info("即将回复(>5 matches): %s", reply_text)
            await message.reply(content=reply_text)
            return
        reply_text = "\n".join(names)
        _log.info("即将回复(names): %s", reply_text.replace("\n", "\\n"))
        await message.reply(content=reply_text)
        return
    if kind == "expand":
        text = str(res.get("text") or "").strip()
        text = _strip_file_path_from_header(text)
        if not text:
            await message.reply(content="(空)")
            return
        try:
            img_path = save_text_as_image(text)
            sent = await _send_image_if_configured(api, message, img_path)
            if img_path:
                asyncio.create_task(_cleanup_image_later(img_path))
            if sent:
                return
        except Exception:
            pass
        await message.reply(content=text if len(text) <= 1800 else text[:1800])
        return


class MyClient(botpy.Client):
    async def on_ready(self):
        _log.info(f'robot "{self.robot.name}" is ready!')

    async def on_group_at_message_create(self, message: GroupMessage):
        content = (message.content or "").strip()
        text = content.strip()
        low = text.lower()
        if low.startswith("/help"):
            await message.reply(content=HELP_TEXT)
            return
        if low.startswith("/say"):
            say_text = text[len("/say"):].strip()
            if not say_text:
                await message.reply(content="请在 /say 后添加要发送的内容。")
                return
            _log.info("即将通过 /say 回复: %s", say_text)
            await message.reply(content=say_text)
            return
        if low.startswith("/findid"):
            query = text[len("/findid"):].strip()
            await handle_find_new(self.api, message, query, mode="id")
            return
        if low.startswith("/find"):
            query = text[len("/find"):].strip()
            await handle_find_new(self.api, message, query, mode="text")
            return
        await message.reply(content=f"{TIP_FIND}\n{TIP_FIND_ID}")


def run_bot() -> None:
    intents = botpy.Intents(public_messages=True)
    client = MyClient(intents=intents)
    client.run(appid=CFG["appid"], secret=CFG["secret"]) 


if __name__ == "__main__":
    run_bot()

