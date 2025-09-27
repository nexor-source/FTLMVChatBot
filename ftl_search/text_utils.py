"""文本展示工具。

函数:
- clip_line: 将整行裁剪到给定长度
- abbrev_text: 对 <text> 文本做首尾截断，仅保留开头/结尾若干字符
"""

from __future__ import annotations

def clip_line(s: str, max_len: int) -> str:
    """将整行裁剪到给定长度。

    输入:
    - s: 原始文本行
    - max_len: 最大长度（字符数）

    返回: 裁剪后的文本，超长以省略号结尾。
    """
    try:
        if max_len and max_len > 0 and len(s) > max_len:
            return s[: max_len - 1] + "…"
        return s
    except Exception:
        return s


def abbrev_text(s: str, head: int = 10, tail: int = 10) -> str:
    """对 <text> 文本做首尾截断，仅保留开头 head 与结尾 tail 个字符。

    输入:
    - s: 文本内容
    - head: 开头保留长度
    - tail: 结尾保留长度

    返回: 形如“前缀…后缀”的简写文本。
    """
    try:
        if s is None:
            return ""
        if head < 0 or tail < 0:
            return s
        if len(s) <= head + tail:
            return s
        return s[:head] + "..." + s[-tail:]
    except Exception:
        return s

