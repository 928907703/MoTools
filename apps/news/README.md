# AI News Hub

本地 Web 工具，每 2 小时自动从 TechCrunch、The Verge、MIT Tech Review、Hacker News、OpenAI/Anthropic/DeepMind/Meta AI 官方博客、机器之心、量子位、36 氪、arXiv 抓取 AI 行业新闻，调用 OpenAI（gpt-4o-mini）生成中文摘要并按主题分类，统一展示在 http://127.0.0.1:8000

## 快速开始

```bash
cd D:\Workspace\ai-news-hub
py -3.14 -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env       # 编辑填 OPENAI_API_KEY
uvicorn app:app --port 8000
```

打开 http://127.0.0.1:8000，等待首轮抓取（1–3 分钟），或点 "立即刷新"。

## 配置

`.env` 支持以下变量：

| 变量 | 说明 |
|---|---|
| `OPENAI_API_KEY` | 必填，未配置时摘要为空、仅按规则分类 |
| `OPENAI_BASE_URL` | 国内代理网关时填对应 URL，默认 `https://api.openai.com/v1` |
| `OPENAI_MODEL` | 默认 `gpt-4o-mini` |
| `SUMMARY_ENABLED` | `false` 关闭 LLM 调用（节省费用 / 调试用） |
| `FETCH_INTERVAL_HOURS` | 默认 `2`，可改小用于调试 |
| `DB_PATH` | 默认 `./data/news.db` |

## 接口

- `GET /` — 主页
- `GET /api/news?hours=168&category=模型发布` — JSON
- `POST /api/refresh` — 手动触发抓取（202 返回，后台执行）
- `GET /api/health`

## 调整新闻源

在 [config.py](config.py) 的 `SOURCES` 列表中增删 `SourceConfig`，重启即可生效。
