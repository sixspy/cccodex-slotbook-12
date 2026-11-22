"""对账：档期 booked 与正式册 seats 同源校验；续期审计与占位过期时刻同源校验。"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user
from db import connect

router = APIRouter(tags=["reconcile"])


def _latest_renew(conn, hold_id: int):
    """取该占位最新一条 hold_renew 审计；无则 None。"""
    row = conn.execute(
        """SELECT * FROM audit_trail WHERE action='hold_renew' AND target=?
           ORDER BY id DESC LIMIT 1""",
        (str(hold_id),),
    ).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row["payload"] or "{}")
    except json.JSONDecodeError:
        return {}


@router.get("/api/reconcile")
def reconcile_all(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        slots = conn.execute("SELECT * FROM slots ORDER BY code").fetchall()
        items = []
        for s in slots:
            booked_sum = conn.execute(
                """SELECT COALESCE(SUM(seats),0) s FROM bookings
                   WHERE slot_id=? AND status='已确认'""",
                (s["id"],),
            ).fetchone()["s"]
            ok = int(booked_sum) == int(s["booked"])
            items.append(
                {
                    "slot_code": s["code"],
                    "slot_booked": s["booked"],
                    "booking_seats": booked_sum,
                    "generation": s["generation"],
                    "ok": ok,
                    "status": s["status"],
                }
            )

        # 续期同源：每条带 hold_renew 审计的占位，审计 new_expire 必须等于正式行时刻
        hold_rows = conn.execute(
            """SELECT h.*, s.code AS slot_code FROM holds h
               JOIN slots s ON s.id=h.slot_id ORDER BY h.id"""
        ).fetchall()
        renew_items, mismatch = [], 0
        for h in hold_rows:
            payload = _latest_renew(conn, h["id"])
            if payload is None:
                continue
            renew_ok = payload.get("new_expire") == h["expire_at"]
            if not renew_ok:
                mismatch += 1
            renew_items.append(
                {
                    "hold_id": h["id"],
                    "slot_code": h["slot_code"],
                    "expire_at": h["expire_at"],
                    "audit_new_expire": payload.get("new_expire"),
                    "renew_ok": renew_ok,
                }
            )

        return {
            "items": items,
            "all_ok": all(i["ok"] for i in items),
            "renew_items": renew_items,
            "renew_all_ok": mismatch == 0,
            "renew_mismatch": mismatch,
        }


@router.get("/api/reconcile/holds")
def reconcile_holds(authorization: AuthHeader = None):
    """逐占位对账：正式行 expire_at 与最新 hold_renew 审计同源；无续期审计视为同源。"""
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            """SELECT h.*, s.code AS slot_code FROM holds h
               JOIN slots s ON s.id=h.slot_id ORDER BY h.id"""
        ).fetchall()
        items = []
        for h in rows:
            payload = _latest_renew(conn, h["id"])
            items.append(
                {
                    "hold_id": h["id"],
                    "slot_code": h["slot_code"],
                    "status": h["status"],
                    "expire_at": h["expire_at"],
                    "renew_ok": True if payload is None else payload.get("new_expire") == h["expire_at"],
                }
            )
        return {"items": items, "renew_all_ok": all(i["renew_ok"] for i in items)}


@router.get("/api/reconcile/{code}")
def reconcile_one(code: str, authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        s = conn.execute("SELECT * FROM slots WHERE code=?", (code,)).fetchone()
        if not s:
            raise HTTPException(404, "档期不存在")
        booked_sum = conn.execute(
            """SELECT COALESCE(SUM(seats),0) s FROM bookings
               WHERE slot_id=? AND status='已确认'""",
            (s["id"],),
        ).fetchone()["s"]
        holds = conn.execute(
            "SELECT COUNT(*) c FROM holds WHERE slot_id=? AND status='占位中'",
            (s["id"],),
        ).fetchone()["c"]
        return {
            "slot_code": code,
            "slot_booked": s["booked"],
            "booking_seats": booked_sum,
            "open_holds": holds,
            "ok": int(booked_sum) == int(s["booked"]),
            "generation": s["generation"],
        }
