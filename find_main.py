"""QQ Bot entry integrated with /find (ftl_search).

行为:
- 消息中包含 /find: 使用 ftl_search 在 data 目录检索并返回结果。
- 其他文本: 简单文字回复（随机追加 ! 或 ?）。
"""
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import io
import random
from pathlib import Path
from contextlib import redirect_stdout

import botpy
from botpy import logging
from botpy.ext.cog_yaml import read
from botpy.message import GroupMessage

from ftl_search.registry import index_events, build_registry
from ftl_search.summarize import show_single_event_detail

# -----------------------------------------------------------------------------
# 配置与索引构建（启动时一次性完成）
# -----------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent
CFG = read(str(ROOT / "QQ_bot" / "config.yaml"))
_log = logging.get_logger()


def _locate_data_dir() -> Path:
    """定位 data 目录：
    1) 环境变量 FTL_DATA_DIR；
    2) 从当前文件夹向上逐级查找含 data 的父目录；
    3) 回退到仓库根 (本文件所在目录)/data。
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


# -----------------------------------------------------------------------------
# 检索实现（复用 CLI 策略）
# -----------------------------------------------------------------------------

def _search(entries, query: str):
    q = (query or "").strip()
    if not q:
        return []
    q_nows = "".join(q.split())
    seen = set()
    results = []
    for e in entries:
        if q in e.text or (q_nows and q_nows in e.text_nows):
            if e.name not in seen:
                seen.add(e.name)
                results.append(e.name)
    return results


def _minimal_events(names, reg):
    name_set = set(names)
    drop = set()
    for m in names:
        ancestors = reg.event_ancestors.get(m, [])
        for a in ancestors:
            if a in name_set:
                drop.add(a)
    return [n for n in names if n not in drop]


def _summarize_single_event_to_text(entry, query: str, max_depth: int = 6, only_outcomes: bool = False, max_len: int = 1600) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        show_single_event_detail(entry, query, REG, max_depth=max_depth, only_outcomes=only_outcomes, max_line_len=100)
    out = buf.getvalue().strip()
    # 去掉首行中的文件路径（如: 匹配事件: NAME (C:\path\file.xml)）
    if out:
        lines = out.splitlines()
        if lines and lines[0].startswith("匹配事件:"):
            # 简单地按第一次出现的 " (" 切割，保留事件名
            head = lines[0]
            cut = head.find(" (")
            if cut != -1:
                lines[0] = head[:cut]
            out = "\n".join(lines)
    if len(out) > max_len:
        return out[: max_len] + "\n…(输出过长，已截断)"
    return out or "(无输出)"


def _choose_font():
    from PIL import ImageFont
    candidates = [
        os.environ.get("FONT_PATH"),
        "C:/Windows/Fonts/msyh.ttc",  # 微软雅黑（Win 常见中文字体）
        "C:/Windows/Fonts/simhei.ttf",  # 黑体
        "/System/Library/Fonts/PingFang.ttc",  # macOS 常见中文字体
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
    """将多行文本渲染为图片并保存。返回保存路径，失败返回 None。"""
    try:
        from PIL import Image, ImageDraw
    except Exception:
        _log.warning("Pillow 未安装，无法生成图片。请安装: pip install Pillow")
        return None

    out_dir = out_dir or (ROOT / "outputs")
    out_dir.mkdir(parents=True, exist_ok=True)

    font = _choose_font()
    # 预计算行折行与尺寸
    max_width = 1000
    pad_x, pad_y = 16, 16
    line_gap = 8

    # 用于测量文本宽度
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

    # 计算整体宽高
    widths = [int(draw.textlength(ln, font=font)) for ln in lines_wrapped]
    max_w = min(max_width, (max(widths) if widths else 0) + pad_x * 2)
    # 估算行高
    try:
        bbox = draw.textbbox((0, 0), "测试Ag", font=font)
        line_h = bbox[3] - bbox[1]
    except Exception:
        line_h = 20
    height = pad_y * 2 + len(lines_wrapped) * (line_h + line_gap)
    if height < pad_y * 2 + line_h:
        height = pad_y * 2 + line_h

    img = Image.new("RGB", (max_w, height), "white")
    d2 = ImageDraw.Draw(img)
    y = pad_y
    for ln in lines_wrapped:
        d2.text((pad_x, y), ln, font=font, fill=(0, 0, 0))
        y += line_h + line_gap

    # 生成文件名
    import time, re
    ts = time.strftime("%Y%m%d_%H%M%S")
    # 取文本首行作为名的一部分，去掉特殊字符
    first = (src_lines[0] if src_lines else "output").strip()
    first = re.sub(r"[^\w\u4e00-\u9fa5]+", "_", first)[:32] or "output"
    fp = out_dir / f"find_{ts}_{first}.png"
    try:
        img.save(fp)
        _log.info(f"已保存文本图片: {fp}")
        return fp
    except Exception as e:
        _log.warning(f"保存图片失败: {e}")
        return None


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
        text = _summarize_single_event_to_text(entry, q)
        # 同步将完整文本保存为本地图像，便于后续切换为图片回复
        try:
            full_text = _summarize_single_event_to_text(entry, q, max_len=10_000_000)
            save_text_as_image(full_text)
        except Exception:
            pass
        # 分段发送，避免消息过长
        chunks = []
        cur = []
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

    # 多个结果（≤5）逐行返回；过多时提示更精确关键词
    if len(names) > 5:
        await message.reply(content=f"匹配事件过多：{len(names)} 个（请提供更具体的关键词）")
    else:
        await message.reply(content="\n".join(names))


# -----------------------------------------------------------------------------
# Bot 客户端
# -----------------------------------------------------------------------------

class MyClient(botpy.Client):
    async def on_ready(self):
        _log.info(f'robot "{self.robot.name}" is ready!')

    async def on_group_at_message_create(self, message: GroupMessage):
        content = (message.content or "").strip()
        low = content.lower()
        idx = low.find("/find")
        if idx >= 0:
            query = content[idx + 5 :].strip()
            await handle_find(self.api, message, query)
            return

        # 默认：简单文字回复
        responses = ["!", "?"]
        random_response = random.choice(responses)
        await message.reply(content=content + random_response)


def run_bot() -> None:
    intents = botpy.Intents(public_messages=True)
    client = MyClient(intents=intents, is_sandbox=True)
    client.run(appid=CFG["appid"], secret=CFG["secret"]) 


if __name__ == "__main__":
    run_bot()
