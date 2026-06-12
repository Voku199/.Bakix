"""SQLite helpers for user-defined AI skills and the /skill create wizard state."""

import json
import logging

from app.database.connection import get_connection

log = logging.getLogger(__name__)

# ── Skill DB helpers ──────────────────────────────────────────────────────────

def has_pending_skill(user_id: str) -> bool:
    return _get_pending_skill(user_id) is not None


def _get_pending_skill(user_id: str) -> "dict | None":
    try:
        with get_connection() as db:
            row = db.execute(
                "SELECT step, data_json FROM pending_skills WHERE user_id = ?", (user_id,)
            ).fetchone()
        if row:
            return {"step": row["step"], "data": json.loads(row["data_json"] or "{}")}
    except Exception:
        log.warning("_get_pending_skill failed for user=%.8s", user_id)
    return None


def _set_pending_skill(user_id: str, step: int, data: dict) -> None:
    try:
        with get_connection() as db:
            db.execute(
                "INSERT INTO pending_skills (user_id, step, data_json, updated_at) "
                "VALUES (?, ?, ?, datetime('now')) "
                "ON CONFLICT(user_id) DO UPDATE SET "
                "  step = excluded.step, "
                "  data_json = excluded.data_json, "
                "  updated_at = excluded.updated_at",
                (user_id, step, json.dumps(data, ensure_ascii=False)),
            )
    except Exception:
        log.warning("_set_pending_skill failed for user=%.8s", user_id)


def _clear_pending_skill(user_id: str) -> None:
    try:
        with get_connection() as db:
            db.execute("DELETE FROM pending_skills WHERE user_id = ?", (user_id,))
    except Exception:
        log.warning("_clear_pending_skill failed for user=%.8s", user_id)


def _get_skill(user_id: str, name: str) -> "str | None":
    try:
        with get_connection() as db:
            row = db.execute(
                "SELECT description FROM skills WHERE user_id = ? AND name = ?", (user_id, name)
            ).fetchone()
        return row["description"] if row else None
    except Exception:
        log.warning("_get_skill failed: user=%.8s name=%s", user_id, name)
    return None


def _list_skills(user_id: str) -> list:
    try:
        with get_connection() as db:
            rows = db.execute(
                "SELECT name FROM skills WHERE user_id = ? ORDER BY name", (user_id,)
            ).fetchall()
        return [{"name": r["name"]} for r in rows]
    except Exception:
        log.warning("_list_skills failed for user=%.8s", user_id)
    return []


def _save_skill(user_id: str, name: str, description: str) -> None:
    with get_connection() as db:
        db.execute(
            "INSERT INTO skills (user_id, name, description) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, name) DO UPDATE SET "
            "  description = excluded.description, created_at = datetime('now')",
            (user_id, name, description),
        )


def _delete_skill(user_id: str, name: str) -> bool:
    try:
        with get_connection() as db:
            db.execute("DELETE FROM skills WHERE user_id = ? AND name = ?", (user_id, name))
        return True
    except Exception:
        log.warning("_delete_skill failed: user=%.8s name=%s", user_id, name)
        return False
