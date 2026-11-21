# 档期预订

> 遵守 `ops/底座生成前必读_效率与技术栈.md`（≥1000 行像产品）。
> FastAPI + Vue 3 + SQLite；`docker compose up -d --build` 一键起。

## 骨架一句话

档期先占位后确认才进正式册，冲突进候补，确认快照与档期对账。

## 基本信息

| 项 | 值 |
|----|-----|
| 产品名 | 档期预订 |
| 目录 | `09-slotbook` |
| 接口 | http://127.0.0.1:11122 |
| 库 | `data/app.db` |
| 账号 | admin / 123456 |

## 厚度

| 项 | 内容 |
|----|------|
| 后端模块 | db / auth / locks / slots_svc / hold_svc / confirm_svc / waitlist_svc / reconcile_svc / audit_svc / release_svc / transfer_svc |
| 前端视图 | 档期盘 / 占位单 / 候补队列 / 确认入册 / 对账互证 / 审计争用 |
| 空桩 | release（释放重开）、transfer（跨档迁移） |

## 互斥切口候选

- 冲突拒占 vs 进候补自动顶替
- 确认失败整单回滚 vs 影子确认后晋升
- 释放作废正式册 vs 快照强制复检
- 跨档迁移整笔事务 vs 双写影子桥

## 续期落时刻

续期必须真改 `holds.expire_at`，只走甲路径（持档锁 + 改时刻与审计同事务），
乙路径关闭不混用；口径与验红顺序钉死在 `docs/续期落时刻说明.md`（2026-11-21 当日锁死）。
接口：`POST /api/holds/renew`（`hold_id`、`minutes`）。
