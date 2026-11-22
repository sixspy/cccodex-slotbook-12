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

## 续期（2026-11-22 锁死）

命运：**就地改写原正式有效占位行** `holds.expire_at` 并同事务落
`audit_trail.action='hold_renew'`；占位主键与对外凭证号 `H-{id:06d}` 不变；
失败则时刻与审计同败。不采用“作废旧行另开续期行”，禁止混用与第三路。
详见 `docs/续期账本命运说明.md`。

- 并发：续期抢 `slot:<code>` 档锁，后到只记 `contention_log`。
- 同源五处：续期页倒计时、占位详情倒计时、占位行 `expire_at`、续期审计、
  对账灯与档期盘；对账新增 `renew_ok` / `renew_mismatches` / `duplicate_active`。
- 接口：`POST /api/holds/{id}/renew`、`GET /api/holds/{id}`、`GET /api/renewals`、
  `GET /api/renewals/audit`。
- 假绿夹具：`fixtures/renew_fake_green.json`；回归测 `tests/test_renew.py`。
