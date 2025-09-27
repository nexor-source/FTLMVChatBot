"""内存占用查询工具。

优先使用 psutil 获取进程 RSS；若不可用且开启了 tracemalloc，则返回 Python 分配的当前/峰值内存。
"""

from __future__ import annotations

from typing import Optional, Dict, Any

def get_memory_usage() -> Dict[str, Any]:
    """获取内存占用信息。

    返回:
    - dict 可能包含键：
      - rss_bytes: 进程常驻内存（需要 psutil）
      - tracemalloc_current: 当前 Python 分配（字节）
      - tracemalloc_peak: 峰值 Python 分配（字节）
    """
    info: Dict[str, Any] = {}
    # psutil 路径
    try:
        import psutil  # type: ignore
        p = psutil.Process()
        mem = p.memory_info()
        info["rss_bytes"] = int(getattr(mem, "rss", 0))
    except Exception:
        pass

    # tracemalloc 路径
    try:
        import tracemalloc  # type: ignore
        cur, peak = tracemalloc.get_traced_memory()
        info["tracemalloc_current"] = int(cur)
        info["tracemalloc_peak"] = int(peak)
    except Exception:
        pass

    return info

