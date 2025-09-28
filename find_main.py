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
    if len(out) > max_len:
        return out[: max_len] + "\n…(输出过长，已截断)"
    return out or "(无输出)"


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

