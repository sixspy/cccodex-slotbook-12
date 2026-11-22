"""续期失败测（先红）：

1. 假绿夹具——续期审计已留痕但正式行时刻没变，对账灯必须判分裂；
   真续期必须把新过期时刻与审计在同一事务落成，五处同源。
2. 两人同时续同一占位——抢档锁，后到只留争用痕，正式时刻零改、无新审计。
3. 任一步失败——正式行时刻与审计都不留下半截（整单回滚）。

夹具跑本机文件库（tmp_path 下 SQLite），不连任何外部服务。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import app  # noqa: E402
import db as dbmod  # noqa: E402
from db import init_db, write_audit  # noqa: E402
import hold_svc  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setattr(dbmod, "DB_PATH", db)
    if db.exists():
        db.unlink()
    init_db()
    yield


def _login(c: TestClient, user: str = "admin", password: str = "123456"):
    r = c.post("/api/login", json={"username": user, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _load_fake_green(conn) -> int:
    """灌入假绿夹具：正式占位行仍是旧 expire_at，却已有一条 hold_renew 审计痕。"""
    fx = json.loads(
        (ROOT / "fixtures" / "renew_fake_green.json").read_text(encoding="utf-8")
    )
    slot = conn.execute(
        "SELECT id FROM slots WHERE code=?", (fx["hold"]["slot_code"],)
    ).fetchone()
    cur = conn.execute(
        """INSERT INTO holds(slot_id,party_key,seats,status,expire_at,created_at)
           VALUES (?,?,?,?,?,?)""",
        (
            slot["id"],
            fx["hold"]["party_key"],
            fx["hold"]["seats"],
            fx["hold"]["status"],
            fx["hold"]["expire_at"],
            fx["hold"]["created_at"],
        ),
    )
    hold_id = cur.lastrowid
    payload = dict(fx["fake_renew_audit"]["payload"])
    payload["hold_id"] = hold_id
    write_audit(
        conn,
        fx["fake_renew_audit"]["actor"],
        fx["fake_renew_audit"]["action"],
        str(hold_id),
        payload,
    )
    conn.commit()
    return hold_id


def _hold_recon(c, headers, hold_id: int) -> dict:
    items = c.get("/api/reconcile/holds", headers=headers).json()["items"]
    return next(i for i in items if i["hold_id"] == hold_id)


def test_fake_green_renew_must_move_expire_at():
    """假绿先判红；真续期后正式行时刻、响应、审计、对账灯全部同源变绿。"""
    with TestClient(app) as c:
        h = _login(c)
        with dbmod.connect() as conn:
            hold_id = _load_fake_green(conn)

        # 假绿状态：审计声称续到 10:30，正式行仍停在 10:00 —— 对账灯必须判分裂
        before = _hold_recon(c, h, hold_id)
        assert before["renew_ok"] is False
        assert before["expire_at"] == "2026-11-22T10:00:00"

        # 真续期
        r = c.post(
            f"/api/holds/{hold_id}/renew", headers=h, json={"minutes": 30}
        )
        assert r.status_code == 200, r.text
        body = r.json()
        old_dt = datetime.fromisoformat("2026-11-22T10:00:00")
        new_dt = datetime.fromisoformat(body["expire_at"])
        assert new_dt > old_dt, "续期必须给出更晚的新过期时刻"

        # 正式占位行时刻真的落库（不是只回文案）
        detail = c.get(f"/api/holds/{hold_id}", headers=h).json()
        assert detail["expire_at"] == body["expire_at"]
        assert detail["expire_at"] != "2026-11-22T10:00:00"

        # 续期审计与正式行同源：最新一条 hold_renew 的 new_expire 等于正式行时刻
        audits = c.get("/api/audit", headers=h).json()["items"]
        latest = next(a for a in audits if a["action"] == "hold_renew")
        payload = json.loads(latest["payload"])
        assert payload["new_expire"] == detail["expire_at"]
        assert payload["old_expire"] == "2026-11-22T10:00:00"

        # 对账灯转绿；总览同源计数一致
        after = _hold_recon(c, h, hold_id)
        assert after["renew_ok"] is True
        recon = c.get("/api/reconcile", headers=h).json()
        assert recon["renew_all_ok"] is True
        assert recon["renew_mismatch"] == 0

        # 档期盘续期计数取自审计（同源），至少含夹具假痕 + 本次真续期两条
        dash = c.get("/api/dashboard", headers=h).json()
        assert dash["renew_total"] >= 2


def test_concurrent_renew_loser_leaves_only_contention():
    """两人同时续同一占位：后到者 409，只留争用痕，正式时刻与审计零改。"""
    with TestClient(app) as c:
        h = _login(c)
        r = c.post(
            "/api/holds",
            headers=h,
            json={"slot_code": "S-AM-01", "party_key": "P-CONC", "seats": 1},
        )
        assert r.status_code == 200, r.text
        hold_id = r.json()["hold_id"]
        before_expire = c.get(f"/api/holds/{hold_id}", headers=h).json()["expire_at"]

        # 模拟先到者已持该占位的续期档锁
        with dbmod.connect() as conn:
            conn.execute(
                "INSERT INTO slot_locks(lock_key,holder,acquired_at) VALUES (?,?,?)",
                (
                    f"hold:{hold_id}",
                    "clerk",
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            conn.commit()

        contention_before = len(c.get("/api/contention", headers=h).json()["items"])
        audits_before = c.get("/api/audit", headers=h).json()["items"]

        # 后到者续期
        r2 = c.post(
            f"/api/holds/{hold_id}/renew", headers=h, json={"minutes": 30}
        )
        assert r2.status_code == 409, r2.text

        # 只多一条争用痕，锁键指向该占位
        contention_after = c.get("/api/contention", headers=h).json()["items"]
        assert len(contention_after) == contention_before + 1
        assert contention_after[0]["lock_key"] == f"hold:{hold_id}"
        assert contention_after[0]["action"] == "争用"

        # 正式过期时刻零改、续期审计一条不多
        after_expire = c.get(f"/api/holds/{hold_id}", headers=h).json()["expire_at"]
        assert after_expire == before_expire
        audits_after = c.get("/api/audit", headers=h).json()["items"]
        assert len(audits_after) == len(audits_before)


def test_renew_atomic_rollback_leaves_no_half_write(monkeypatch):
    """审计写入失败时：UPDATE 整单回滚，正式时刻不改、无半截审计、锁释放。"""
    def _boom(*_a, **_kw):
        raise RuntimeError("注入：审计写失败")

    with TestClient(app, raise_server_exceptions=False) as c:
        h = _login(c)
        r = c.post(
            "/api/holds",
            headers=h,
            json={"slot_code": "S-PM-02", "party_key": "P-ATOM", "seats": 1},
        )
        hold_id = r.json()["hold_id"]
        before = c.get(f"/api/holds/{hold_id}", headers=h).json()["expire_at"]

        # 占位建成后再注入审计故障，只打续期事务；用 60 分钟保证新时刻严格晚于旧时刻
        monkeypatch.setattr(hold_svc, "write_audit", _boom)
        r2 = c.post(
            f"/api/holds/{hold_id}/renew", headers=h, json={"minutes": 60}
        )
        assert r2.status_code == 500

        after = c.get(f"/api/holds/{hold_id}", headers=h).json()
        assert after["expire_at"] == before, "正式行时刻不得留下半截更新"
        audits = c.get("/api/audit", headers=h).json()["items"]
        assert not any(a["action"] == "hold_renew" for a in audits), "不得留半截续期审计"
        locks = c.get("/api/locks", headers=h).json()["items"]
        assert not any(l["lock_key"] == f"hold:{hold_id}" for l in locks), "锁必须释放"
