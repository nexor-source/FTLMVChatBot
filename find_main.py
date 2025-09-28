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
    max_depth: int = 6,
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
    """Render text into a PNG under outputs/ and return path."""
    try:
        from PIL import Image, ImageDraw
    except Exception:
        _log.warning("Pillow 未安装，无法生成图片。请安装: pip install Pillow")
        return None

    out_dir = out_dir or (ROOT / "outputs")
    out_dir.mkdir(parents=True, exist_ok=True)

    font = _choose_font()
    max_width = 1000
    pad_x, pad_y = 16, 16
    line_gap = 8

    temp_img = Image.new("RGB", (10, 10), "white")
    draw = ImageDraw.Draw(temp_img)

    def wrap_line(s: str) -> list[str]:
        if not s:
            return [""]
        out_lines: list[str] = []
        cur = ""
        for ch in s:
            test = cur + ch
            w = draw.textlength(test, font=font)
            if w > max_width - pad_x * 2 and cur:
                out_lines.append(cur)
                cur = ch
            else:
                cur = test
        if cur or not out_lines:
            out_lines.append(cur)
        return out_lines

    src_lines = text.splitlines()
    lines_wrapped: list[str] = []
    for ln in src_lines:
        lines_wrapped.extend(wrap_line(ln.rstrip()))

    widths = [int(draw.textlength(ln, font=font)) for ln in lines_wrapped]
    max_w = min(max_width, (max(widths) if widths else 0) + pad_x * 2)
    try:
        bbox = draw.textbbox((0, 0), "测试Ag", font=font)
        line_h = bbox[3] - bbox[1]
    except Exception:
        line_h = 20
    height = max(pad_y * 2 + len(lines_wrapped) * (line_h + line_gap), pad_y * 2 + line_h)

    img = Image.new("RGB", (max_w, height), "white")
    d2 = ImageDraw.Draw(img)
    y = pad_y
    for ln in lines_wrapped:
        d2.text((pad_x, y), ln, font=font, fill=(0, 0, 0))
        y += line_h + line_gap

    import time, unicodedata
    ts = time.strftime("%Y%m%d_%H%M%S")
    first = (src_lines[0] if src_lines else "output").strip()
    first_norm = unicodedata.normalize("NFKD", first)
    first_ascii = re.sub(r"[^A-Za-z0-9_-]+", "_", first_norm)[:48] or "output"
    fp = out_dir / f"find_{ts}_{first_ascii}.png"
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
