# MoTools

个人工具集总仓库。

## 模块

- `apps/news`：AI News Hub，AI 行业新闻聚合。
- `apps/ca-tracker`：CA Tracker，合约地址分析管理。
- `portal`：统一入口和 Caddy 反向代理配置。

## 本地/服务器启动

先分别创建环境文件：

```bash
cp apps/news/.env.example apps/news/.env
cp apps/ca-tracker/.env.example apps/ca-tracker/.env
```

然后启动：

```bash
docker compose up -d --build
```

默认入口：

- `https://moshimo.fun/`
- `https://moshimo.fun/news/`
- `https://moshimo.fun/ca/`
