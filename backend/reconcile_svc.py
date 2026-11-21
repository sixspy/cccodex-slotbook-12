"""对账：档期 booked 与正式册 seats 同源校验。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user
from db import connect

from datetime import datetime

router = APIRouter(tags=["reconcile"])


@router.get("/api/reconcile")
def reconcile_all(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        now = datetime.now().isoformat(timespec="seconds")
        slots = conn.execute("SELECT * FROM slots ORDER BY code").fetchall()
        items = []
        for s in slots:
            booked_sum = conn.execute(
                """SELECT COALESCE(SUM(seats),0) s FROM bookings
                   WHERE slot_id=? AND status='已确认'""",
                (s["id"],),
            ).fetchone()["s"]
            expired_open = conn.execute(
                """SELECT COUNT(*) c FROM holds
                   WHERE slot_id=? AND status='占位中' AND expire_at < ?""",
                (s["id"], now),
            ).fetchone()["c"]
            ok = int(booked_sum) == int(s["booked"])
            items.append(
                {
                    "slot_code": s["code"],
                    "slot_booked": s["booked"],
                    "booking_seats": booked_sum,
                    "generation": s["generation"],
                    "ok": ok,
                    "status": s["status"],
                    "holds_ok": int(expired_open) == 0,
                    "expired_open": expired_open,
                }
            )
        return {
            "items": items,
            "all_ok": all(i["ok"] and i["holds_ok"] for i in items),
            "checked_at": now,
        }


@router.get("/api/reconcile/{code}")
def reconcile_one(code: str, authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        s = conn.execute("SELECT * FROM slots WHERE code=?", (code,)).fetchone()
        if not s:
            raise HTTPException(404, "档期不存在")
        now = datetime.now().isoformat(timespec="seconds")
        booked_sum = conn.execute(
            """SELECT COALESCE(SUM(seats),0) s FROM bookings
               WHERE slot_id=? AND status='已确认'""",
            (s["id"],),
        ).fetchone()["s"]
        holds = conn.execute(
            "SELECT COUNT(*) c FROM holds WHERE slot_id=? AND status='占位中'",
            (s["id"],),
        ).fetchone()["c"]
        holds_expire = conn.execute(
            """SELECT id, party_key, status, expire_at FROM holds
               WHERE slot_id=? ORDER BY id""",
            (s["id"],),
        ).fetchall()
        expired_open = [
            h for h in holds_expire
            if h["status"] == "占位中" and h["expire_at"] < now
        ]
        return {
            "slot_code": code,
            "slot_booked": s["booked"],
            "booking_seats": booked_sum,
            "open_holds": holds,
            "holds_expire": [dict(h) for h in holds_expire],
            "holds_ok": not expired_open,
            "expired_open": [dict(h) for h in expired_open],
            "ok": int(booked_sum) == int(s["booked"]),
            "generation": s["generation"],
        }
