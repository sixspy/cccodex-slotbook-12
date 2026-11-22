"""占位：容量校验、并发锁、候补分流。"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from auth import AuthHeader, require_user, require_writer
from db import (
    connect,
    countdown_seconds,
    credential,
    renew_audits,
    write_audit,
)
from locks import release, require_lock, try_acquire

router = APIRouter(tags=["holds"])


class HoldIn(BaseModel):
    slot_code: str
    party_key: str
    seats: int = Field(ge=1, le=50)


class RenewIn(BaseModel):
    extend_minutes: int = Field(default=30, ge=1, le=1440)


def _hold_view(conn, h) -> dict:
    item = dict(h)
    item["credential"] = credential(h["id"])
    item["countdown_seconds"] = countdown_seconds(h["expire_at"], h["status"])
    return item


@router.get("/api/holds")
def list_holds(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            """SELECT h.*, s.code AS slot_code FROM holds h
               JOIN slots s ON s.id=h.slot_id ORDER BY h.id DESC LIMIT 200"""
        ).fetchall()
        return {"items": [_hold_view(conn, r) for r in rows]}


@router.get("/api/holds/{hold_id}")
def get_hold(hold_id: int, authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        h = conn.execute(
            """SELECT h.*, s.code AS slot_code FROM holds h
               JOIN slots s ON s.id=h.slot_id WHERE h.id=?""",
            (hold_id,),
        ).fetchone()
        if not h:
            raise HTTPException(404, "占位不存在")
        item = _hold_view(conn, h)
        seq = renew_audits(conn).get(hold_id, [])
        item["renew_count"] = len(seq)
        item["last_renew_at"] = seq[-1]["audit_at"] if seq else None
        item["last_renew_new_expire"] = seq[-1]["new_expire"] if seq else None
        return item


@router.get("/api/renewals")
def list_renewals(authorization: AuthHeader = None):
    """续期页：与占位行、审计同源，按占位主键聚合。"""
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            """SELECT h.*, s.code AS slot_code FROM holds h
               JOIN slots s ON s.id=h.slot_id ORDER BY h.id DESC LIMIT 200"""
        ).fetchall()
        audits = renew_audits(conn)
        items = []
        for h in rows:
            seq = audits.get(int(h["id"]), [])
            items.append(
                {
                    "hold_id": h["id"],
                    "credential": credential(h["id"]),
                    "slot_code": h["slot_code"],
                    "party_key": h["party_key"],
                    "status": h["status"],
                    "expire_at": h["expire_at"],
                    "countdown_seconds": countdown_seconds(h["expire_at"], h["status"]),
                    "renew_count": len(seq),
                    "last_renew_at": seq[-1]["audit_at"] if seq else None,
                    "last_old_expire": seq[-1]["old_expire"] if seq else None,
                    "last_new_expire": seq[-1]["new_expire"] if seq else None,
                }
            )
        return {"items": items}


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


@router.post("/api/holds/{hold_id}/renew")
def renew_hold(hold_id: int, body: RenewIn, authorization: AuthHeader = None):
    """续期：就地改写原正式有效占位行，同事务落续期审计。

    命运见 docs/续期账本命运说明.md：主键与凭证号不变，失败则时刻与审计同败。
    并发：抢 slot:<code> 档锁；后到只记争用痕，不写业务。
    """
    user = require_writer(authorization)
    with connect() as conn:
        hold = conn.execute("SELECT * FROM holds WHERE id=?", (hold_id,)).fetchone()
        if not hold:
            raise HTTPException(404, "占位不存在")
        slot = conn.execute("SELECT * FROM slots WHERE id=?", (hold["slot_id"],)).fetchone()
        if not slot:
            raise HTTPException(404, "档期不存在")
        lock_key = f"slot:{slot['code']}"

        # 先落锁决定：争用痕必须留痕（提交），后到不写任何业务。
        if not try_acquire(conn, lock_key, user):
            conn.commit()
            raise HTTPException(409, "争用：后到只记争用，不写业务")
        try:
            hold = conn.execute("SELECT * FROM holds WHERE id=?", (hold_id,)).fetchone()
            if hold["status"] != "占位中":
                raise HTTPException(409, "占位已非占位中，禁止续期")

            now = datetime.now()
            old_expire = hold["expire_at"]
            try:
                base = max(now, datetime.fromisoformat(old_expire))
            except (TypeError, ValueError):
                base = now
            new_expire = (base + timedelta(minutes=body.extend_minutes)).isoformat(
                timespec="seconds"
            )

            # 就地改写 + 同事务审计：任一失败整笔回滚，不留半截。
            conn.execute(
                "UPDATE holds SET expire_at=? WHERE id=? AND status='占位中'",
                (new_expire, hold_id),
            )
            write_audit(
                conn,
                user,
                "hold_renew",
                hold["party_key"],
                {
                    "hold_id": hold_id,
                    "credential": credential(hold_id),
                    "slot": slot["code"],
                    "old_expire": old_expire,
                    "new_expire": new_expire,
                    "extend_minutes": body.extend_minutes,
                },
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            release(conn, lock_key, user)
            conn.commit()

        return {
            "ok": True,
            "message": "续期成功",
            "hold_id": hold_id,
            "credential": credential(hold_id),
            "slot_code": slot["code"],
            "old_expire": old_expire,
            "expire_at": new_expire,
            "countdown_seconds": countdown_seconds(new_expire, "占位中"),
            "extend_minutes": body.extend_minutes,
        }
