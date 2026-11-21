"""续期落时刻：先对假绿夹具见红，再对真实现见绿；另加双人争用失败测。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tests"))

from app import app  # noqa: E402
from db import init_db  # noqa: E402
import db as dbmod  # noqa: E402
import renew_harness as harness  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_file_db(tmp_path, monkeypatch):
    """夹具与实现都只跑本机文件库。"""
    db = tmp_path / "t.db"
    monkeypatch.setattr(dbmod, "DB_PATH", db)
    if db.exists():
        db.unlink()
    init_db()
    assert dbmod.DB_PATH == db and db.is_file()
    yield


def test_fake_green_renew_rejected():
    """假绿夹具：续期回 200、审计也写了，但时刻没动 —— 断言必须见红。"""
    with TestClient(harness.build_fake_green_app()) as c:
        h = harness.login(c)
        hold = harness.new_hold(c, h)
        before = hold["expire_at"]
        with pytest.raises(AssertionError, match="过期时刻未前移"):
            harness.assert_renewal_landed(c, h, hold["id"], before, minutes=60)
        # 证明这不是因为接口炸了：它确实回了成功
        r = harness.renew(c, h, hold["id"], 60)
        assert r.status_code == 200 and r.json()["ok"] is True
        assert r.json()["expire_at"] == before


def test_renew_lands_expire_at():
    """真实现：续期成功必须真改过期时刻，五处同态。"""
    with TestClient(app) as c:
        h = harness.login(c)
        hold = harness.new_hold(c, h)
        body = harness.assert_renewal_landed(c, h, hold["id"], hold["expire_at"], minutes=60)
        assert body["previous_expire"] == hold["expire_at"]
        assert body["expire_at"] > hold["expire_at"]


def test_renew_contention_loser_leaves_only_trace():
    """两人续同一占位：须抢档锁，后到只留争用痕，不动时刻。"""
    with TestClient(app) as c:
        h1 = harness.login(c, "admin")
        h2 = harness.login(c, "clerk")
        hold = harness.new_hold(c, h1)
        before = hold["expire_at"]

        # admin 先持档锁（模拟并发中先到者仍在事务内）
        lock_key = f"slot:{hold['slot_code']}"
        r = c.post("/api/locks/acquire", headers=h1, json={"lock_key": lock_key})
        assert r.status_code == 200, r.text

        r2 = harness.renew(c, h2, hold["id"], 60)
        assert r2.status_code == 409, r2.text

        # 后到者只留争用痕
        contention = c.get("/api/contention", headers=h1).json()["items"]
        mine = [x for x in contention if x["lock_key"] == lock_key and x["actor"] == "clerk"]
        assert mine, contention
        assert mine[0]["action"] == "争用"

        # 时刻零改、审计无 clerk 的续期落时刻
        holds = c.get("/api/holds", headers=h1).json()["items"]
        held = next(x for x in holds if x["id"] == hold["id"])
        assert held["expire_at"] == before
        audit = c.get(
            "/api/audit", headers=h1, params={"action": "hold_renew", "limit": 20}
        ).json()["items"]
        assert not [a for a in audit if a["actor"] == "clerk"], audit

        c.post("/api/locks/release", headers=h1, json={"lock_key": lock_key})

        # 先到者续期仍可行，且时刻真前移
        body = harness.assert_renewal_landed(c, h1, hold["id"], before, minutes=90)
        assert body["expire_at"] > before


def test_renew_is_single_transaction_rollback():
    """甲路径：改时刻与续期审计同事务，审计写失败则时刻同滚。"""
    import hold_svc

    with TestClient(app) as c:
        h = harness.login(c)
        hold = harness.new_hold(c, h)
        before = hold["expire_at"]

        def boom(*a, **k):
            raise RuntimeError("审计写失败")

        orig = hold_svc.write_audit
        hold_svc.write_audit = boom
        try:
            with pytest.raises(RuntimeError):
                harness.renew(c, h, hold["id"], 60)
        finally:
            hold_svc.write_audit = orig

        holds = c.get("/api/holds", headers=h).json()["items"]
        held = next(x for x in holds if x["id"] == hold["id"])
        assert held["expire_at"] == before, "审计失败但时刻已改：甲乙混用/非同一事务"
        audit = c.get(
            "/api/audit", headers=h, params={"action": "hold_renew", "limit": 20}
        ).json()["items"]
        assert not [a for a in audit if a["target"] == hold["party_key"]], audit

        # 事务回滚后锁也应可再取，续期仍能落时刻
        body = harness.assert_renewal_landed(c, h, hold["id"], before, 60)
        assert body["expire_at"] > before


def test_frontend_only_renew_refused_with_drift_trace():
    """只刷前端倒计时：拒写、时刻零改，记续期落时刻偏航。"""
    with TestClient(app) as c:
        h = harness.login(c)
        hold = harness.new_hold(c, h)
        before = hold["expire_at"]
        r = c.post(
            "/api/holds/renew",
            headers=h,
            json={"hold_id": hold["id"], "minutes": 60, "frontend_only": True},
        )
        assert r.status_code == 409, r.text
        holds = c.get("/api/holds", headers=h).json()["items"]
        held = next(x for x in holds if x["id"] == hold["id"])
        assert held["expire_at"] == before
        audit = c.get(
            "/api/audit", headers=h, params={"action": "续期落时刻偏航", "limit": 20}
        ).json()["items"]
        assert audit, "偏航拒写没有留续期落时刻偏航痕"
        payload = json.loads(audit[0]["payload"] or "{}")
        assert payload.get("reason") == "只刷前端倒计时"


def test_expired_hold_cannot_renew():
    with TestClient(app) as c:
        h = harness.login(c)
        hold = harness.new_hold(c, h)
        # 人为把过期时刻推到过去
        import db as dbmod
        with dbmod.connect() as conn:
            conn.execute(
                "UPDATE holds SET expire_at=? WHERE id=?",
                ("2020-01-01T00:00:00", hold["id"]),
            )
            conn.commit()
        r = harness.renew(c, h, hold["id"], 60)
        assert r.status_code == 409, r.text
        holds = c.get("/api/holds", headers=h).json()["items"]
        held = next(x for x in holds if x["id"] == hold["id"])
        assert held["status"] == "已过期"
        assert held["expire_at"] == "2020-01-01T00:00:00"


def test_renew_path_is_locked_to_single_landing_path():
    """钉死口径：只走甲路径，乙路径不启用，两边不得混用。"""
    import hold_svc

    assert hold_svc.RENEW_LANDING_PATH == "甲·持锁同事务改过期时刻"
    assert hold_svc.RENEW_SHADOW_ENABLED is False
    assert hold_svc.RENEW_PATH_LOCKED_ON == "2026-11-21"


def test_reconcile_all_reports_hold_expiry_state():
    """全量对账也须与占位过期时刻同态；续期后不得判分裂。"""
    with TestClient(app) as c:
        h = harness.login(c)
        hold = harness.new_hold(c, h)
        rec = c.get("/api/reconcile", headers=h).json()
        row = next(i for i in rec["items"] if i["slot_code"] == hold["slot_code"])
        assert row["holds_ok"] is True and row["expired_open"] == 0
        assert rec["all_ok"] is True

        body = harness.assert_renewal_landed(c, h, hold["id"], hold["expire_at"], 30)
        rec2 = c.get("/api/reconcile", headers=h).json()
        row2 = next(i for i in rec2["items"] if i["slot_code"] == hold["slot_code"])
        assert row2["holds_ok"] is True
        assert rec2["all_ok"] is True
        assert body["expire_at"] > hold["expire_at"]
