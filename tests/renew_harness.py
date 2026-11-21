"""续期假绿夹具：续期回成功、审计也写了，但过期时刻一动不动。

用途：
1. 先让「续期成功必须真改过期时刻」的失败测对着它见红；
2. 再用同一个断言检查器证明测试确实抓得住假绿。

夹具只跑本机文件库（由测试的 tmp_path 文件库注入），不碰生产实现。
"""
from __future__ import annotations

import json

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from audit_svc import router as audit_router
from auth import require_writer
from confirm_svc import router as confirm_router
from db import connect, write_audit
from hold_svc import router as hold_router
from locks import router as locks_router
from reconcile_svc import router as reconcile_router
from slots_svc import router as slots_router
from waitlist_svc import router as waitlist_router


class FakeRenewIn(BaseModel):
    hold_id: int
    minutes: int = Field(default=30, ge=1, le=1440)


def _fake_green_renew(body: FakeRenewIn, authorization: str | None = Header(None)):
    """假绿：校验、回成功、写 hold_renew 审计，但 expire_at 原样返回。"""
    user = require_writer(authorization)
    with connect() as conn:
        hold = conn.execute("SELECT * FROM holds WHERE id=?", (body.hold_id,)).fetchone()
        if not hold:
            raise HTTPException(404, "占位不存在")
        write_audit(
            conn,
            user,
            "hold_renew",
            hold["party_key"],
            {
                "hold_id": hold["id"],
                "previous_expire": hold["expire_at"],
                "expire_at": hold["expire_at"],  # ← 假绿：没有任何前移
                "minutes": body.minutes,
                "fake_green": True,
            },
        )
        conn.commit()
        return {
            "ok": True,
            "hold_id": hold["id"],
            "expire_at": hold["expire_at"],
            "previous_expire": hold["expire_at"],
            "minutes": body.minutes,
        }


def build_fake_green_app() -> FastAPI:
    """真路由 + 假续期：除续期外的链路都是本仓真实实现。"""
    app = FastAPI()
    for router in (
        slots_router,
        confirm_router,
        waitlist_router,
        locks_router,
        audit_router,
        reconcile_router,
    ):
        app.include_router(router)
    for route in hold_router.routes:
        if getattr(route, "path", None) != "/api/holds/renew":
            app.router.routes.append(route)
    # 登录入口
    from auth import router as auth_router

    app.include_router(auth_router)
    app.post("/api/holds/renew")(_fake_green_renew)
    return app


def login(client, username: str = "admin", password: str = "123456") -> dict:
    r = client.post("/api/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def new_hold(client, headers, slot_code="S-AM-01", party_key="P-RENEW", seats=1) -> dict:
    r = client.post(
        "/api/holds",
        headers=headers,
        json={"slot_code": slot_code, "party_key": party_key, "seats": seats},
    )
    assert r.status_code == 200, r.text
    assert r.json()["disposition"] == "占位", r.text
    hold_id = r.json()["hold_id"]
    items = client.get("/api/holds", headers=headers).json()["items"]
    return next(h for h in items if h["id"] == hold_id)


def renew(client, headers, hold_id: int, minutes: int = 60):
    return client.post(
        "/api/holds/renew", headers=headers, json={"hold_id": hold_id, "minutes": minutes}
    )


def assert_renewal_landed(client, headers, hold_id: int, before_expire: str, minutes: int = 60) -> dict:
    """五处同态断言：续期结果 / 过期时刻 / 占位态 / 对账 / 审计。"""
    r = renew(client, headers, hold_id, minutes)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("ok") is True, body

    landed = body.get("expire_at")
    assert landed and landed > before_expire, (
        f"续期返回成功但过期时刻未前移：{before_expire} -> {landed}"
    )

    # 占位态 + 过期时刻
    holds = client.get("/api/holds", headers=headers).json()["items"]
    held = next(h for h in holds if h["id"] == hold_id)
    assert held["status"] == "占位中", held
    assert held["expire_at"] == landed, f"占位过期时刻与续期结果分裂：{held['expire_at']} != {landed}"

    # 审计（续期落时刻）
    audit = client.get(
        "/api/audit",
        headers=headers,
        params={"action": "hold_renew", "target": held["party_key"], "limit": 5},
    ).json()["items"]
    assert audit, "续期成功但没有 hold_renew 审计"
    payload = json.loads(audit[0]["payload"] or "{}")
    assert payload.get("expire_at") == landed, f"审计时刻与续期结果分裂：{payload.get('expire_at')} != {landed}"

    # 对账（同档同占位同态）
    rec = client.get(f"/api/reconcile/{held['slot_code']}", headers=headers).json()
    entry = next(x for x in rec.get("holds_expire", []) if x["id"] == hold_id)
    assert entry["expire_at"] == landed, f"对账时刻与续期结果分裂：{entry['expire_at']} != {landed}"
    assert rec.get("holds_ok") is True, f"对账判定占位态分裂：{rec}"
    return body
