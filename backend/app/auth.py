from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta
from typing import Any

from .config import (
    AUTH_BOOTSTRAP_PASSWORD,
    AUTH_LOCKOUT_MINUTES,
    AUTH_LOGIN_WINDOW_MINUTES,
    AUTH_MAX_LOGIN_ATTEMPTS,
    AUTH_BOOTSTRAP_USERNAME,
    AUTH_SESSION_HOURS,
    PROJECT_DIR,
)
from .database import Database, now_iso

ROLE_LABELS = {
    "viewer": "只读观察员",
    "dispatcher": "调度员",
    "verifier": "核验员",
    "admin": "系统管理员",
}
VALID_ROLES = set(ROLE_LABELS)
PASSWORD_ITERATIONS = 310_000


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt, expected = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), _unb64(salt), int(iterations))
        return hmac.compare_digest(_b64(digest), expected)
    except Exception:
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class AuthService:
    def __init__(self, db: Database) -> None:
        self.db = db

    @staticmethod
    def public_user(row: dict) -> dict:
        return {
            "id": row["id"],
            "username": row["username"],
            "display_name": row.get("display_name") or row["username"],
            "role": row["role"],
            "role_label": ROLE_LABELS.get(row["role"], row["role"]),
            "is_active": bool(row.get("is_active", 1)),
            "created_at": row.get("created_at"),
            "last_login_at": row.get("last_login_at"),
        }

    @staticmethod
    def demo_user() -> dict:
        return {
            "id": 0,
            "username": "demo",
            "display_name": "演示值班员",
            "role": "admin",
            "role_label": ROLE_LABELS["admin"],
            "is_active": True,
            "created_at": None,
            "last_login_at": None,
        }

    def ensure_bootstrap_admin(self) -> dict | None:
        with self.db.connect() as conn:
            count = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        if count:
            return None
        password = AUTH_BOOTSTRAP_PASSWORD
        generated = False
        if not password:
            password = secrets.token_urlsafe(18)
            generated = True
        user = self.create_user(AUTH_BOOTSTRAP_USERNAME or "admin", password, role="admin", display_name="系统管理员")
        if generated:
            target = PROJECT_DIR / "data" / "bootstrap-admin-password.txt"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(password + "\n", encoding="utf-8")
        return user

    def create_user(self, username: str, password: str, role: str = "viewer", display_name: str = "") -> dict:
        username = username.strip().lower()
        if len(username) < 2:
            raise ValueError("用户名至少需要 2 个字符")
        if role not in VALID_ROLES:
            raise ValueError("用户角色无效")
        now = now_iso()
        encoded = hash_password(password)
        with self.db.connect() as conn:
            if conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone():
                raise ValueError("用户名已存在")
            user_id = self.db._insert_id(
                conn,
                "INSERT INTO users (username, display_name, role, password_hash, is_active, created_at, updated_at) VALUES (?, ?, ?, ?, 1, ?, ?)",
                (username, display_name.strip() or username, role, encoded, now, now),
            )
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            return self.public_user(dict(row))

    def authenticate(self, username: str, password: str) -> dict | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE username = ?", (username.strip().lower(),)).fetchone()
            if not row or not row["is_active"] or not verify_password(password, row["password_hash"]):
                return None
            now = now_iso()
            conn.execute("UPDATE users SET last_login_at = ?, updated_at = ? WHERE id = ?", (now, now, row["id"]))
            item = dict(row)
            item["last_login_at"] = now
            return self.public_user(item)

    @staticmethod
    def attempt_key(username: str, ip: str) -> str:
        raw = f"{(ip or '').strip()}|{username.strip().lower()}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def check_login_allowed(self, attempt_key: str) -> int:
        with self.db.connect() as conn:
            raw_row = conn.execute("SELECT * FROM login_attempts WHERE attempt_key = ?", (attempt_key,)).fetchone()
            row = dict(raw_row) if raw_row else None
            if not row or not row.get("locked_until"):
                return 0
            try:
                locked_until = datetime.fromisoformat(row["locked_until"])
            except ValueError:
                return 0
            remaining = int((locked_until - datetime.now().astimezone()).total_seconds())
            if remaining <= 0:
                conn.execute("DELETE FROM login_attempts WHERE attempt_key = ?", (attempt_key,))
                return 0
            return remaining

    def record_login_failure(self, attempt_key: str) -> int:
        now = datetime.now().astimezone()
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM login_attempts WHERE attempt_key = ?", (attempt_key,)).fetchone()
            count = 1
            if row:
                try:
                    started = datetime.fromisoformat(row["window_started_at"])
                except ValueError:
                    started = now - timedelta(minutes=AUTH_LOGIN_WINDOW_MINUTES + 1)
                if (now - started).total_seconds() <= AUTH_LOGIN_WINDOW_MINUTES * 60:
                    count = int(row["failure_count"]) + 1
                else:
                    started = now
            else:
                started = now
            locked_until = None
            if count >= AUTH_MAX_LOGIN_ATTEMPTS:
                locked_until = (now + timedelta(minutes=AUTH_LOCKOUT_MINUTES)).isoformat(timespec="seconds")
            conn.execute(
                """INSERT INTO login_attempts
                   (attempt_key, failure_count, window_started_at, locked_until, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(attempt_key) DO UPDATE SET
                   failure_count=excluded.failure_count, window_started_at=excluded.window_started_at,
                   locked_until=excluded.locked_until, updated_at=excluded.updated_at""",
                (attempt_key, count, started.isoformat(timespec="seconds"), locked_until, now.isoformat(timespec="seconds")),
            )
            return AUTH_LOCKOUT_MINUTES * 60 if locked_until else 0

    def reset_login_failures(self, attempt_key: str) -> None:
        with self.db.connect() as conn:
            conn.execute("DELETE FROM login_attempts WHERE attempt_key = ?", (attempt_key,))

    def change_password(self, user_id: int, current_password: str, new_password: str) -> bool:
        if user_id <= 0:
            raise ValueError("演示账号不能修改密码")
        if current_password == new_password:
            raise ValueError("新密码不能与当前密码相同")
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if not row or not verify_password(current_password, row["password_hash"]):
                raise ValueError("当前密码错误")
            conn.execute(
                "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
                (hash_password(new_password), now_iso(), user_id),
            )
            conn.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
            return True

    def create_session(self, user_id: int, ip: str = "", user_agent: str = "") -> tuple[str, str, int]:
        token = secrets.token_urlsafe(48)
        created = datetime.now().astimezone()
        expires = created + timedelta(hours=AUTH_SESSION_HOURS)
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO auth_sessions (token_hash, user_id, expires_at, created_at, last_seen_at, ip, user_agent) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (token_hash(token), user_id, expires.isoformat(timespec="seconds"), created.isoformat(timespec="seconds"),
                 created.isoformat(timespec="seconds"), ip[:120], user_agent[:300]),
            )
        return token, expires.isoformat(timespec="seconds"), AUTH_SESSION_HOURS * 3600

    def resolve_session(self, token: str) -> dict | None:
        if not token:
            return None
        hashed = token_hash(token)
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT s.expires_at, u.* FROM auth_sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?",
                (hashed,),
            ).fetchone()
            if not row:
                return None
            item = dict(row)
            try:
                expires = datetime.fromisoformat(item["expires_at"])
            except ValueError:
                expires = datetime.now().astimezone() - timedelta(seconds=1)
            if expires <= datetime.now().astimezone() or not item["is_active"]:
                conn.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (hashed,))
                return None
            conn.execute("UPDATE auth_sessions SET last_seen_at = ? WHERE token_hash = ?", (now_iso(), hashed))
            return self.public_user(item)

    def revoke_session(self, token: str) -> None:
        if token:
            with self.db.connect() as conn:
                conn.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash(token),))

    def list_users(self) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
            return [self.public_user(dict(row)) for row in rows]

    def update_user(self, user_id: int, changes: dict[str, Any]) -> dict | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if not row:
                return None
            sets, params = [], []
            if changes.get("display_name") is not None:
                sets.append("display_name = ?")
                params.append(str(changes["display_name"]).strip() or row["username"])
            if changes.get("role") is not None:
                if changes["role"] not in VALID_ROLES:
                    raise ValueError("用户角色无效")
                sets.append("role = ?")
                params.append(changes["role"])
            if changes.get("is_active") is not None:
                sets.append("is_active = ?")
                params.append(1 if changes["is_active"] else 0)
            if changes.get("password"):
                sets.append("password_hash = ?")
                params.append(hash_password(str(changes["password"])))
            if not sets:
                return self.public_user(dict(row))
            sets.append("updated_at = ?")
            params.append(now_iso())
            params.append(user_id)
            conn.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = ?", tuple(params))
            updated = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            return self.public_user(dict(updated))

    def delete_user(self, user_id: int) -> dict | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if not row:
                return None
            item = dict(row)
            if item["role"] == "admin" and item["is_active"]:
                active_admins = conn.execute(
                    "SELECT COUNT(*) AS count FROM users WHERE role = 'admin' AND is_active = 1"
                ).fetchone()["count"]
                if active_admins <= 1:
                    raise ValueError("不能删除最后一个启用的管理员")
            conn.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
            return self.public_user(item)

    def audit(self, action: str, method: str, path: str, status_code: int | None = None,
              user: dict | None = None, resource_type: str = "", resource_id: str = "",
              ip: str = "", detail: str = "") -> None:
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO audit_logs (user_id, username, role, action, resource_type, resource_id, method, path, status_code, ip, detail, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ((user or {}).get("id"), (user or {}).get("username", ""), (user or {}).get("role", ""),
                 action, resource_type, resource_id, method, path, status_code, ip[:120], detail[:1000], now_iso()),
            )

    def list_audit_logs(self, limit: int = 200) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            return [dict(row) for row in rows]
