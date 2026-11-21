"""占位：容量校验、并发锁、候补分流、续期落时刻。"""
from __future__ import annotations

from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from auth import AuthHeader, require_user, require_writer
from db import connect, write_audit
from locks import release, require_lock

router = APIRouter(tags=["holds"])

# 续期落时刻口径：只走甲路径——持档锁后，改过期时刻与续期审计同一事务，
# 失败则时刻与审计同滚。乙路径（续期影子原子切换）本仓不启用，两边不得混用。
RENEW_LANDING_PATH = "甲·持锁同事务改过期时刻"
RENEW_SHADOW_ENABLED = False
RENEW_PATH_LOCKED_ON = "2026-11-21"


class HoldIn(BaseModel):
    slot_code: str
    party_key: str
    seats: int = Field(ge=1, le=50)


class RenewIn(BaseModel):
    hold_id: int
    minutes: int = Field(default=30, ge=1, le=1440)
    # 只刷前端倒计时的请求一律拒写：续期必须真改过期时刻。
    frontend_only: bool = False


def _refuse_renew_drift(conn, user: str, hold_id: int, reason: str, extra: dict | None = None):
    """记续期落时刻偏航并拒写：时刻零改，偏航痕单独留证。"""
    conn.rollback()
    write_audit(
        conn,
        user,
        "续期落时刻偏航",
        str(hold_id),
        {"reason": reason, "path": RENEW_LANDING_PATH, **(extra or {})},
    )
    conn.commit()
    raise HTTPException(409, f"续期落时刻偏航：{reason}，拒写")


@router.get("/api/holds")
def list_holds(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            """SELECT h.*, s.code AS slot_code FROM holds h
               JOIN slots s ON s.id=h.slot_id ORDER BY h.id DESC LIMIT 200"""
        ).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.post("/api/holds/renew")
def renew_hold(body: RenewIn, authorization: AuthHeader = None):
    """续期：持档锁 → 同事务改 expire_at + 写 hold_renew 审计 → 提交。

    两人同时续同一占位，后到者抢锁失败只留争用痕；时刻与审计同滚，绝不只刷倒计时。
    """
    user = require_writer(authorization)
    if RENEW_SHADOW_ENABLED:
        with connect() as conn:
            _refuse_renew_drift(conn, user, body.hold_id, "甲乙路径混用", {"shadow": True})
    if body.frontend_only:
        with connect() as conn:
            _refuse_renew_drift(
                conn, user, body.hold_id, "只刷前端倒计时", {"minutes": body.minutes}
            )

    with connect() as conn:
        hold = conn.execute("SELECT * FROM holds WHERE id=?", (body.hold_id,)).fetchone()
        if not hold:
            raise HTTPException(404, "占位不存在")
        slot = conn.execute("SELECT * FROM slots WHERE id=?", (hold["slot_id"],)).fetchone()
        if not slot:
            raise HTTPException(404, "档期不存在")
        lock_key = f"slot:{slot['code']}"
        require_lock(conn, lock_key, user)
        try:
            hold = conn.execute(
                "SELECT * FROM holds WHERE id=?", (body.hold_id,)
            ).fetchone()
            if hold["status"] != "占位中":
                raise HTTPException(409, "占位已非占位中，禁止续期")
            now_dt = datetime.now()
            now = now_dt.isoformat(timespec="seconds")
            previous = hold["expire_at"]
            if previous < now:
                conn.execute("UPDATE holds SET status='已过期' WHERE id=?", (hold["id"],))
                write_audit(conn, user, "hold_expire", str(hold["id"]), {})
                conn.commit()
                raise HTTPException(409, "占位已过期，禁止续期")

            # 从「当前过期时刻」与「此刻」中较晚者起算，保证倒计时真的前移。
            try:
                base = max(datetime.fromisoformat(previous), now_dt)
            except ValueError:
                base = now_dt
            expire_at = (base + timedelta(minutes=body.minutes)).isoformat(timespec="seconds")
            if expire_at <= previous:
                _refuse_renew_drift(
                    conn,
                    user,
                    hold["id"],
                    "续期后时刻未前移",
                    {"previous_expire": previous, "expire_at": expire_at},
                )

            cur = conn.execute(
                """UPDATE holds SET expire_at=?
                   WHERE id=? AND status='占位中' AND expire_at=?""",
                (expire_at, hold["id"], previous),
            )
            if cur.rowcount != 1:
                _refuse_renew_drift(
                    conn,
                    user,
                    hold["id"],
                    "占位态在锁内被改写",
                    {"previous_expire": previous, "expire_at": expire_at},
                )
            write_audit(
                conn,
                user,
                "hold_renew",
                hold["party_key"],
                {
                    "hold_id": hold["id"],
                    "previous_expire": previous,
                    "expire_at": expire_at,
                    "minutes": body.minutes,
                    "path": RENEW_LANDING_PATH,
                },
            )
            conn.commit()
            return {
                "ok": True,
                "hold_id": hold["id"],
                "position": hold["status"],
                "previous_expire": previous,
                "expire_at": expire_at,
                "minutes": body.minutes,
                "path": RENEW_LANDING_PATH,
            }
        except HTTPException:
            conn.rollback()
            raise
        except Exception:
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
