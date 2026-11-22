"""对账：档期 booked 与正式册 seats 同源校验。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user
from db import connect, duplicate_active_holds, renew_mismatches

router = APIRouter(tags=["reconcile"])


def _renew_state(conn):
    bad = renew_mismatches(conn)
    bad_ids = {b["hold_id"] for b in bad}
    dup = duplicate_active_holds(conn)
    return bad, bad_ids, dup


@router.get("/api/reconcile")
def reconcile_all(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        slots = conn.execute("SELECT * FROM slots ORDER BY code").fetchall()
        bad, bad_ids, dup = _renew_state(conn)
        hold_rows = conn.execute(
            "SELECT id,slot_id FROM holds WHERE status='占位中'"
        ).fetchall()
        hold_slot = {int(h["id"]): int(h["slot_id"]) for h in hold_rows}
        bad_slots = {hold_slot[i] for i in bad_ids if i in hold_slot}
        dup_slots = {int(d["slot_id"]) for d in dup}
        items = []
        for s in slots:
            booked_sum = conn.execute(
                """SELECT COALESCE(SUM(seats),0) s FROM bookings
                   WHERE slot_id=? AND status='已确认'""",
                (s["id"],),
            ).fetchone()["s"]
            renew_ok = int(s["id"]) not in bad_slots and int(s["id"]) not in dup_slots
            ok = int(booked_sum) == int(s["booked"]) and renew_ok
            items.append(
                {
                    "slot_code": s["code"],
                    "slot_booked": s["booked"],
                    "booking_seats": booked_sum,
                    "generation": s["generation"],
                    "renew_ok": renew_ok,
                    "ok": ok,
                    "status": s["status"],
                }
            )
        return {
            "items": items,
            "all_ok": all(i["ok"] for i in items),
            "renew_mismatches": bad,
            "duplicate_active": dup,
        }


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
        bad, bad_ids, dup = _renew_state(conn)
        hold_rows = conn.execute(
            "SELECT id FROM holds WHERE slot_id=? AND status='占位中'", (s["id"],)
        ).fetchall()
        slot_hold_ids = {int(h["id"]) for h in hold_rows}
        renew_ok = not (
            (bad_ids & slot_hold_ids) or any(int(d["slot_id"]) == int(s["id"]) for d in dup)
        )
        return {
            "slot_code": code,
            "slot_booked": s["booked"],
            "booking_seats": booked_sum,
            "open_holds": holds,
            "renew_ok": renew_ok,
            "ok": int(booked_sum) == int(s["booked"]) and renew_ok,
            "generation": s["generation"],
        }
