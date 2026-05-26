# CA Tracker

本地合约地址（CA）分析管理工具。手动录入 CA → 自动抓 DexScreener 链上数据 → 去重 → 手动 + LLM 辅助打标签 → 按维度筛选回顾。完全独立于飞书。

## 技术栈

FastAPI + aiosqlite + httpx + Jinja2。可选 Anthropic Claude（无 API key 时优雅停用）。

## 启动

```bash
cd /d/Workspace/ca-tracker
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
cp .env.example .env   # 可选：填 ANTHROPIC_API_KEY 启用 LLM 辅助标签
.venv/Scripts/uvicorn main:app --reload
```

浏览器打开 <http://localhost:8000>。

## 功能

- **列表 `/`**：表格 + 按链/标签/评分/搜索筛选
- **录入 `/new`**：粘 CA + 选链 → 自动抓名称/价格/市值 → 查重提示 → (可选) LLM 推荐标签 → 填来源/分析/评分 → 保存
- **详情 `/token/{id}`**：完整信息 + 链上快照历史 + 来源历史 + 编辑 + 重新抓快照
- **标签 `/tags`**：新增/重命名/删除

数据保存在 `data/ca.db`（已 gitignore）。
