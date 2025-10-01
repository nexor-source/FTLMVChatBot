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
    if m:
        lines[0] = m.group(1)
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
    try:
        from PIL import Image, ImageDraw
    except Exception:
        _log.info("Pillow 未安装，跳过图片渲染")
        return None

    out_dir = out_dir or (ROOT / "outputs")
    out_dir.mkdir(parents=True, exist_ok=True)

    font = _choose_font()
    pad_x, pad_y = 16, 16
    max_width = 1000
    line_gap = 6

    temp_img = Image.new("RGB", (10, 10), "white")
    draw = ImageDraw.Draw(temp_img)
    try:
        bbox = draw.textbbox((0, 0), 'Ag', font=font)
        line_h = bbox[3] - bbox[1]
    except Exception:
        line_h = 22

    def wrap_line(s: str, width_px: int) -> List[str]:
        if width_px <= 20:
            return [s]
        out: List[str] = []
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

    # wrap all lines
    wrapped: List[str] = []
    for raw in text.splitlines():
        for ln in wrap_line(raw.rstrip("\r\n"), max_width - pad_x * 2):
            wrapped.append(ln)

    height = pad_y * 2 + len(wrapped) * (line_h + line_gap)
    img = Image.new('RGB', (max_width, height), 'white')
    d2 = ImageDraw.Draw(img)
    y = pad_y
    for ln in wrapped:
        d2.text((pad_x, y), ln, font=font, fill=(0, 0, 0))
        y += line_h + line_gap

    ts = time.strftime("%Y%m%d_%H%M%S")
    first_line = text.splitlines()[0] if text.splitlines() else 'output'
    base = unicodedata.normalize('NFKD', first_line)
    base = re.sub(r'[^A-Za-z0-9_-]+', '_', base)[:48] or 'output'
    fp = out_dir / f"find_{ts}_{base}.png"
    try:
        img.save(fp)
        _log.info(f"已保存图片: {fp}")
        return fp
    except Exception as e:
        _log.warning(f"保存图片失败: {e}")
        return None


# Image reply via URL (config driven)
IMAGE_UPLOAD_ENABLED = bool((CFG.get("image_upload") or {}).get("enabled", False))
IMAGE_BASE_URL = None
if CFG.get("image_server") and CFG["image_server"].get("base_url"):
    IMAGE_BASE_URL = str(CFG["image_server"]["base_url"]).rstrip("/")
IMAGE_CLEANUP_DELAY = int((CFG.get("image_cleanup") or {}).get("delay_seconds", 120))


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




async def handle_find_new(api: botpy.BotAPI, message: GroupMessage, query: str):
    q = (query or "").strip()
    if not q:
        await message.reply(content="用法: /find 关键词")
        return

    res = search_once(q, DATA_DIR, max_depth=16, only_outcomes=False)
    kind = res.get("kind")
    if kind == "empty_query":
        await message.reply(content="用法: /find 关键词")
        return
    if kind == "not_found":
        return
    if kind == "names":
        names = list(res.get("names") or [])
        if not names:
            return
        if len(names) > 5:
            return
        await message.reply(content="\n".join(names))
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
        if low.startswith("/find"):
            query = text[5:].strip()
            await handle_find_new(self.api, message, query)
            return
        await message.reply(content=tip)


def run_bot() -> None:
    intents = botpy.Intents(public_messages=True)
    client = MyClient(intents=intents, is_sandbox=True)
    client.run(appid=CFG["appid"], secret=CFG["secret"]) 


if __name__ == "__main__":
    run_bot()

