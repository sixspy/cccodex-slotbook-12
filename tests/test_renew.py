"""续期：先见红的失败测。

1) 续期回成功但过期时刻不变（假绿）——必须红。
2) 两人同时续同一占位——后到只留争用痕——必须红。

夹具跑本机文件库（tmp_path 下的 SQLite 文件）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import app  # noqa: E402
import db as dbmod  # noqa: E402
from db import init_db  # noqa: E402

FAKE_GREEN = ROOT / "fixtures" / "renew_fake_green.json"


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    db = tmp_path / "renew.db"
    monkeypatch.setattr(dbmod, "DB_PATH", db)
    if db.exists():
        db.unlink()
    init_db()
    yield


def _login(c: TestClient, user="admin", password="123456"):
    r = c.post("/api/login", json={"username": user, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _seed_fake_green():
    """按假绿夹具落一条正式有效占位；过期时刻仍是旧值。"""
    fx = json.loads(FAKE_GREEN.read_text(encoding="utf-8"))
    slot, hold = fx["slot"], fx["hold"]
    with dbmod.connect() as conn:
        cur = conn.execute(
            """INSERT INTO slots(code,title,start_at,end_at,capacity,booked,status,generation)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                slot["code"], slot["title"], slot["start_at"], slot["end_at"],
                slot["capacity"], slot["booked"], slot["status"], slot["generation"],
            ),
        )
        sid = cur.lastrowid
        cur = conn.execute(
            """INSERT INTO holds(slot_id,party_key,seats,status,expire_at,created_at)
               VALUES (?,?,?,?,?,?)""",
            (
                sid, hold["party_key"], hold["seats"], hold["status"],
                hold["expire_at"], hold["created_at"],
            ),
        )
        conn.commit()
        return fx, sid, cur.lastrowid


def test_renew_success_advances_expire_and_same_source():
    """假绿夹具：接口回成功文案，过期时刻必须真的往后走，且五处同源。"""
    with TestClient(app) as c:
        h = _login(c)
        fx, _sid, hid = _seed_fake_green()
        old = fx["hold"]["expire_at"]

        r = c.post(f"/api/holds/{hid}/renew", headers=h, json={})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["message"] == "续期成功"
        assert body["hold_id"] == hid
        assert body["credential"] == f"H-{hid:06d}"
        assert body["old_expire"] == old

        new = body["expire_at"]
        assert new != old, "假绿：续期回成功，但过期时刻没变"
        assert new > old

        # 1) 正式有效占位行：库为准
        with dbmod.connect() as conn:
            row = conn.execute("SELECT * FROM holds WHERE id=?", (hid,)).fetchone()
            audit = conn.execute(
                "SELECT * FROM audit_trail WHERE action='hold_renew' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        assert row["status"] == "占位中"
        assert row["expire_at"] == new
        # 2) 续期审计：落账且与库同源
        assert audit is not None, "续期成功却没落续期审计"
        payload = json.loads(audit["payload"])
        assert payload["hold_id"] == hid
        assert payload["old_expire"] == old
        assert payload["new_expire"] == new
        assert payload["new_expire"] > payload["old_expire"]

        # 3) 续期页 / 占位详情页倒计时：同一来源
        detail = c.get(f"/api/holds/{hid}", headers=h).json()
        assert detail["expire_at"] == new
        assert detail["credential"] == body["credential"]
        assert abs(detail["countdown_seconds"] - body["countdown_seconds"]) <= 2
        renewals = c.get("/api/renewals", headers=h).json()["items"]
        item = [x for x in renewals if x["hold_id"] == hid][0]
        assert item["expire_at"] == new
        assert abs(item["countdown_seconds"] - body["countdown_seconds"]) <= 2
        holds = c.get("/api/holds", headers=h).json()["items"]
        assert [x for x in holds if x["id"] == hid][0]["expire_at"] == new

        # 4) 对账灯
        rec = c.get("/api/reconcile", headers=h).json()
        slot_item = [i for i in rec["items"] if i["slot_code"] == fx["slot"]["code"]][0]
        assert slot_item["renew_ok"] is True
        assert slot_item["ok"] is True
        assert rec["all_ok"] is True

        # 5) 档期盘
        dash = c.get("/api/dashboard", headers=h).json()
        assert dash["renew_mismatches"] == 0
        slots = c.get("/api/slots", headers=h).json()["items"]
        s = [x for x in slots if x["code"] == fx["slot"]["code"]][0]
        assert s["next_hold_expire"] == new


def test_two_party_renew_contends_late_only_leaves_trace():
    """两人同时续同一占位：后到只留争用痕，不写业务。"""
    with TestClient(app) as c:
        admin = _login(c, "admin")
        clerk = _login(c, "clerk")
        fx, _sid, hid = _seed_fake_green()
        old = fx["hold"]["expire_at"]

        # 先到者（admin）正持有档锁，续期在途
        r = c.post(
            "/api/locks/acquire", headers=admin, json={"lock_key": "slot:S-RENEW-01"}
        )
        assert r.status_code == 200, r.text

        # 后到者（clerk）续同一占位
        r2 = c.post(f"/api/holds/{hid}/renew", headers=clerk, json={})
        assert r2.status_code == 409, r2.text

        with dbmod.connect() as conn:
            assert conn.execute(
                "SELECT expire_at FROM holds WHERE id=?", (hid,)
            ).fetchone()["expire_at"] == old
            assert conn.execute(
                "SELECT COUNT(*) c FROM audit_trail WHERE action='hold_renew'"
            ).fetchone()["c"] == 0
            traces = conn.execute(
                "SELECT * FROM contention_log WHERE lock_key='slot:S-RENEW-01'"
            ).fetchall()
        assert any(t["actor"] == "clerk" for t in traces), "后到未留争用痕"

        # 先到者续期仍成功
        r3 = c.post(f"/api/holds/{hid}/renew", headers=admin, json={})
        assert r3.status_code == 200, r3.text
        assert r3.json()["expire_at"] > old


def test_reconcile_flags_row_audit_mismatch():
    """假绿打回原形：接口/行/审计三者分裂时对账灯必须红。"""
    with TestClient(app) as c:
        h = _login(c)
        fx, sid, hid = _seed_fake_green()
        old = fx["hold"]["expire_at"]
        # 续期成功一次作为基线
        r = c.post(f"/api/holds/{hid}/renew", headers=h, json={})
        assert r.status_code == 200
        new = r.json()["expire_at"]
        assert c.get("/api/reconcile", headers=h).json()["all_ok"] is True

        # 制造假绿：只把行改回旧时刻，审计仍记 new —— 账实不一致
        with dbmod.connect() as conn:
            conn.execute("UPDATE holds SET expire_at=? WHERE id=?", (old, hid))
            conn.commit()

        rec = c.get("/api/reconcile", headers=h).json()
        assert rec["all_ok"] is False
        assert rec["renew_mismatches"], "对账灯未抓住行与续期审计不符"
        item = [i for i in rec["items"] if i["slot_code"] == fx["slot"]["code"]][0]
        assert item["renew_ok"] is False
        dash = c.get("/api/dashboard", headers=h).json()
        assert dash["renew_mismatches"] >= 1
        # 续期页倒计时与行同源，会显示旧时刻；审计页显示 new —— 分裂可见
        item_r = [
            x for x in c.get("/api/renewals", headers=h).json()["items"]
            if x["hold_id"] == hid
        ][0]
        assert item_r["expire_at"] == old
        audit = c.get("/api/renewals/audit", headers=h).json()["items"][0]
        assert audit["new_expire"] == new


def test_renew_failure_rolls_back_both(monkeypatch):
    """失败则时刻与审计同败，不留半截。"""
    with TestClient(app) as c:
        h = _login(c)
        fx, sid, hid = _seed_fake_green()
        old = fx["hold"]["expire_at"]

        import hold_svc

        real_write_audit = hold_svc.write_audit

        def boom(conn, actor, action, target, payload=None):
            raise RuntimeError("审计写失败（注入）")

        monkeypatch.setattr(hold_svc, "write_audit", boom)
        with pytest.raises(RuntimeError):
            c.post(f"/api/holds/{hid}/renew", headers=h, json={})
        # 只还原审计注入，保留 _fresh_db 的库路径替换
        monkeypatch.setattr(hold_svc, "write_audit", real_write_audit)

        with dbmod.connect() as conn:
            row = conn.execute("SELECT expire_at FROM holds WHERE id=?", (hid,)).fetchone()
            n = conn.execute(
                "SELECT COUNT(*) c FROM audit_trail WHERE action='hold_renew'"
            ).fetchone()["c"]
        assert row["expire_at"] == old, "失败却改了过期时刻：留下半截"
        assert n == 0, "失败却落了审计"

        # 失败后锁应释放，可再次正常续期
        r = c.post(f"/api/holds/{hid}/renew", headers=h, json={})
        assert r.status_code == 200, r.text
        assert r.json()["expire_at"] > old


def test_renew_rejects_non_active_hold():
    with TestClient(app) as c:
        h = _login(c)
        fx, sid, hid = _seed_fake_green()
        with dbmod.connect() as conn:
            conn.execute("UPDATE holds SET status='已过期' WHERE id=?", (hid,))
            conn.commit()
        r = c.post(f"/api/holds/{hid}/renew", headers=h, json={})
        assert r.status_code == 409
        with dbmod.connect() as conn:
            assert conn.execute(
                "SELECT COUNT(*) c FROM audit_trail WHERE action='hold_renew'"
            ).fetchone()["c"] == 0
