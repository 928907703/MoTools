"""一次性脚本：把所有还没有 AI 摘要的条目（summary 为空）重置为待总结，
然后立刻调用 LLM 重新总结。

用法：
  .\.venv\Scripts\python.exe resummarize.py
"""
from __future__ import annotations

import asyncio
import sqlite3

from config import DB_PATH
from llm.summarize import summarize_pending


def reset_empty_summaries() -> int:
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(
            "UPDATE news_items SET summarized = 0 "
            "WHERE summary IS NULL OR summary = ''"
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


async def main() -> None:
    n = reset_empty_summaries()
    print(f"已重置 {n} 条待总结条目")
    if n == 0:
        return
    # 分批处理，避免一次太多
    total = 0
    while True:
        batch = await summarize_pending(limit=80)
        if batch == 0:
            break
        total += batch
        print(f"  已总结 {total}/{n}")
    print(f"完成，总结 {total} 条")


if __name__ == "__main__":
    asyncio.run(main())
