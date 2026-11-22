"""审计与互证只读面。"""
from __future__ import annotations

from fastapi import APIRouter

from auth import AuthHeader, require_user
from db import connect, credential, renew_audits

router = APIRouter(tags=["audit"])


@router.get("/api/audit")
def list_audit(authorization: AuthHeader = None, limit: int = 100):
    require_user(authorization)
    lim = max(1, min(limit, 500))
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM audit_trail ORDER BY id DESC LIMIT ?", (lim,)
        ).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.get("/api/renewals/audit")
def list_renew_audit(authorization: AuthHeader = None, limit: int = 100):
    """续期审计专面：与占位行同源，按占位主键列出每次续期。"""
    require_user(authorization)
    lim = max(1, min(limit, 500))
    with connect() as conn:
        items = []
        for hid, seq in sorted(renew_audits(conn).items(), reverse=True):
            for ev in seq:
                items.append(
                    {
                        "hold_id": hid,
                        "credential": ev.get("credential") or credential(hid),
                        "slot_code": ev.get("slot"),
                        "old_expire": ev.get("old_expire"),
                        "new_expire": ev.get("new_expire"),
                        "extend_minutes": ev.get("extend_minutes"),
                        "audit_id": ev["audit_id"],
                        "audit_at": ev["audit_at"],
                    }
                )
        items.sort(key=lambda x: x["audit_id"], reverse=True)
        return {"items": items[:lim]}


@router.get("/api/proof/{party_key}")
def proof(party_key: str, authorization: AuthHeader = None):
    """按当事人互证：占位/正式/候补是否同源可读。"""
    require_user(authorization)
    with connect() as conn:
        holds = conn.execute(
            "SELECT * FROM holds WHERE party_key=? ORDER BY id DESC", (party_key,)
        ).fetchall()
        books = conn.execute(
            "SELECT * FROM bookings WHERE party_key=? ORDER BY id DESC", (party_key,)
        ).fetchall()
        waits = conn.execute(
            "SELECT * FROM waitlist WHERE party_key=? ORDER BY id DESC", (party_key,)
        ).fetchall()
        return {
            "party_key": party_key,
            "holds": [dict(r) for r in holds],
            "bookings": [dict(r) for r in books],
            "waitlist": [dict(r) for r in waits],
        }
