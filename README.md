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

## 续期（2026-11-22 落）

续期接口 `POST /api/holds/{hold_id}/renew` 不再只回文案：新过期时刻与 `hold_renew`
审计在**同一事务单次提交**原子写进正式占位行（路线 A，决策见
[`docs/续期落时刻说明.md`](docs/续期落时刻说明.md)，当日锁死，禁用影子草稿与第三路）。

- 并发：`hold:{id}` 抢档锁，后到者仅留 `contention_log` 争用痕并回 409，业务零写。
- 失败：任一步异常整单回滚，正式行时刻与审计都不留半截，锁必释放。
- 五处同源（事实源唯一：正式行 `holds.expire_at`）：续期页倒计时、占位详情过期时刻、
  续期审计（`/api/audit?action=hold_renew`）、对账灯（`/api/reconcile` 的
  `renew_all_ok/renew_mismatch` 与 `/api/reconcile/holds`）、档期盘
  （`/api/dashboard` 的 `renew_total`）。
- 假绿夹具 `fixtures/renew_fake_green.json`：审计声称已续、正式行时刻未动，对账灯判分裂。
- 测试 `tests/test_renew.py`：假绿先红、双人争用先红、原子回滚，夹具跑本机临时文件库。
