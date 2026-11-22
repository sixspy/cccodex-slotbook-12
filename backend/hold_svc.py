"""占位：容量校验、并发锁、候补分流、续期落时刻。"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from auth import AuthHeader, require_user, require_writer
from db import connect, write_audit
from locks import release, require_lock

router = APIRouter(tags=["holds"])

# 续期默认/上下限（分钟），续期只延长过期时刻、不改席数与档期
RENEW_MIN_MINUTES = 1
RENEW_MAX_MINUTES = 240


class HoldIn(BaseModel):
    slot_code: str
    party_key: str
    seats: int = Field(ge=1, le=50)


class RenewIn(BaseModel):
    minutes: int = Field(default=30, ge=RENEW_MIN_MINUTES, le=RENEW_MAX_MINUTES)


@router.get("/api/holds")
def list_holds(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            """SELECT h.*, s.code AS slot_code FROM holds h
               JOIN slots s ON s.id=h.slot_id ORDER BY h.id DESC LIMIT 200"""
        ).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.get("/api/holds/{hold_id}")
def get_hold(hold_id: int, authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        row = conn.execute(
            """SELECT h.*, s.code AS slot_code FROM holds h
               JOIN slots s ON s.id=h.slot_id WHERE h.id=?""",
            (hold_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "占位不存在")
        return dict(row)


@router.post("/api/holds/{hold_id}/renew")
def renew_hold(hold_id: int, body: RenewIn, authorization: AuthHeader = None):
    """续期：同一事务把新过期时刻与 hold_renew 审计原子写进正式占位行。

    路线 A（见 docs/续期落时刻说明.md，2026-11-22 锁死）：
    单连接单提交，任一步失败整体回滚，正式行时刻与审计都不留下半截。
    并发以 hold:{id} 抢档锁，后到者只留争用痕、回 409，不写业务数据。
    """
    user = require_writer(authorization)
    lock_key = f"hold:{hold_id}"
    with connect() as conn:
        try:
            require_lock(conn, lock_key, user)
        except HTTPException:
            # 后到者：争用痕必须留下，业务数据一条不写
            conn.commit()
            raise
        try:
            hold = conn.execute(
                "SELECT * FROM holds WHERE id=?", (hold_id,)
            ).fetchone()
            if not hold:
                raise HTTPException(404, "占位不存在")
            if hold["status"] != "占位中":
                raise HTTPException(409, "占位已非占位中，禁止续期")
            old_expire = hold["expire_at"]
            new_expire = (
                datetime.now() + timedelta(minutes=body.minutes)
            ).isoformat(timespec="seconds")
            if new_expire <= old_expire:
                raise HTTPException(409, "新过期时刻必须晚于现过期时刻")
            conn.execute(
                "UPDATE holds SET expire_at=? WHERE id=?",
                (new_expire, hold_id),
            )
            write_audit(
                conn,
                user,
                "hold_renew",
                str(hold_id),
                {"hold_id": hold_id, "old_expire": old_expire, "new_expire": new_expire},
            )
            conn.commit()
            return {
                "ok": True,
                "hold_id": hold_id,
                "expire_at": new_expire,
                "old_expire_at": old_expire,
            }
        except Exception:
            # 整单回滚：正式行时刻与审计都不得留下半截
            conn.rollback()
            raise
        finally:
            release(conn, lock_key, user)
            conn.commit()


@router.post("/api/holds")
def create_hold(body: HoldIn, authorization: AuthHeader = None):
    user = require_writer(authorization)
    lock_key = f"slot:{body.slot_code}"
    with connect() as conn:
        require_lock(conn, lock_key, user)
        try:
            slot = conn.execute(
                "SELECT * FROM slots WHERE code=?", (body.slot_code,)
            ).fetchone()
            if not slot:
                raise HTTPException(404, "档期不存在")
            if slot["status"] != "开放":
                raise HTTPException(409, "档期未开放，禁止占位")
            remain = int(slot["capacity"]) - int(slot["booked"])
            pending = conn.execute(
                "SELECT COALESCE(SUM(seats),0) s FROM holds WHERE slot_id=? AND status='占位中'",
                (slot["id"],),
            ).fetchone()["s"]
            free = remain - int(pending)
            now = datetime.now().isoformat(timespec="seconds")
            expire = (datetime.now() + timedelta(minutes=30)).isoformat(timespec="seconds")
            if body.seats > free:
                conn.execute(
                    """INSERT INTO waitlist(slot_id,party_key,seats,reason,status,created_at)
                       VALUES (?,?,?,?,?,?)""",
                    (slot["id"], body.party_key, body.seats, "容量不足", "候补中", now),
                )
                write_audit(
                    conn,
                    user,
                    "hold_to_waitlist",
                    body.party_key,
                    {"slot": body.slot_code, "free": free, "want": body.seats},
                )
                conn.commit()
                return {"disposition": "候补", "free": free}
            cur = conn.execute(
                """INSERT INTO holds(slot_id,party_key,seats,status,expire_at,created_at)
                   VALUES (?,?,?,?,?,?)""",
                (slot["id"], body.party_key, body.seats, "占位中", expire, now),
            )
            write_audit(
                conn,
                user,
                "hold_create",
                body.party_key,
                {"slot": body.slot_code, "seats": body.seats, "hold_id": cur.lastrowid},
            )
            conn.commit()
            return {"disposition": "占位", "hold_id": cur.lastrowid, "expire_at": expire}
        finally:
            release(conn, lock_key, user)
            conn.commit()
