from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import api as api_module
from app.auth import AuthService, verify_password
from app.database import Database
from app.main import app
from app.operations import OperationsRepository


def test_password_session_and_audit(tmp_path: Path):
    test_db = Database(tmp_path / "auth-core.db", database_url="")
    test_db.initialize()
    service = AuthService(test_db)
    user = service.create_user("dispatcher1", "StrongPass123!", role="dispatcher", display_name="值班调度员")
    assert user["role"] == "dispatcher"
    assert service.authenticate("dispatcher1", "wrong-password") is None
    authenticated = service.authenticate("dispatcher1", "StrongPass123!")
    assert authenticated and authenticated["username"] == "dispatcher1"
    token, expires_at, max_age = service.create_session(user["id"])
    assert expires_at and max_age > 0
    session_user = service.resolve_session(token)
    assert session_user["role"] == "dispatcher"
    service.audit("test_action", "POST", "/api/test", 200, session_user)
    logs = service.list_audit_logs()
    assert logs[0]["action"] == "test_action"
    service.revoke_session(token)
    assert service.resolve_session(token) is None


def test_auth_login_and_admin_endpoints(tmp_path: Path):
    test_db = Database(tmp_path / "auth-api.db", database_url="")
    test_db.initialize()
    api_module.db = test_db
    api_module.ops = OperationsRepository(test_db)
    api_module.auth_service = AuthService(test_db)
    admin = api_module.auth_service.create_user("admin-test", "AdminPass123!", role="admin", display_name="测试管理员")
    client = TestClient(app)

    login = client.post("/api/auth/login", json={"username": admin["username"], "password": "AdminPass123!"})
    assert login.status_code == 200, login.text
    assert login.json()["user"]["role"] == "admin"

    me = client.get("/api/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["user"]["username"] == "admin-test"

    created = client.post("/api/auth/users", json={
        "username": "viewer-test",
        "display_name": "测试观察员",
        "password": "ViewerPass123!",
        "role": "viewer",
    })
    assert created.status_code == 200, created.text
    assert created.json()["role"] == "viewer"
    viewer_id = created.json()["id"]
    deleted = client.delete(f"/api/auth/users/{viewer_id}")
    assert deleted.status_code == 200, deleted.text
    assert all(item["id"] != viewer_id for item in client.get("/api/auth/users").json())
    self_delete = client.delete(f"/api/auth/users/{admin['id']}")
    assert self_delete.status_code == 400

    logs = client.get("/api/auth/audit-logs")
    assert logs.status_code == 200, logs.text
    assert len(logs.json()) >= 1

    logout = client.post("/api/auth/logout")
    assert logout.status_code == 200


def test_login_lockout_after_repeated_failures(tmp_path: Path):
    test_db = Database(tmp_path / "auth-lockout.db", database_url="")
    test_db.initialize()
    api_module.db = test_db
    api_module.ops = OperationsRepository(test_db)
    api_module.auth_service = AuthService(test_db)
    api_module.auth_service.create_user("lockout-test", "CorrectPass123!", role="viewer")
    client = TestClient(app)
    statuses = []
    for _ in range(5):
        response = client.post("/api/auth/login", json={"username": "lockout-test", "password": "wrong-password"})
        statuses.append(response.status_code)
    assert statuses[:4] == [401, 401, 401, 401]
    assert statuses[4] == 429
    blocked = client.post("/api/auth/login", json={"username": "lockout-test", "password": "CorrectPass123!"})
    assert blocked.status_code == 429


def test_change_password_revokes_sessions(tmp_path: Path):
    test_db = Database(tmp_path / "auth-password.db", database_url="")
    test_db.initialize()
    api_module.db = test_db
    api_module.ops = OperationsRepository(test_db)
    api_module.auth_service = AuthService(test_db)
    user = api_module.auth_service.create_user("password-test", "CurrentPass123!", role="viewer")
    client = TestClient(app)
    login = client.post("/api/auth/login", json={"username": user["username"], "password": "CurrentPass123!"})
    assert login.status_code == 200
    changed = client.post("/api/auth/change-password", json={
        "current_password": "CurrentPass123!",
        "new_password": "NewPassword456!",
    })
    assert changed.status_code == 200, changed.text
    client.cookies.clear()
    old_login = client.post("/api/auth/login", json={"username": user["username"], "password": "CurrentPass123!"})
    assert old_login.status_code == 401
    new_login = client.post("/api/auth/login", json={"username": user["username"], "password": "NewPassword456!"})
    assert new_login.status_code == 200

def test_delete_user_revokes_sessions_and_protects_last_admin(tmp_path: Path):
    test_db = Database(tmp_path / "auth-delete.db", database_url="")
    test_db.initialize()
    service = AuthService(test_db)
    admin = service.create_user("delete-admin", "AdminPass123!", role="admin")
    viewer = service.create_user("delete-viewer", "ViewerPass123!", role="viewer")
    token, _, _ = service.create_session(viewer["id"])

    deleted = service.delete_user(viewer["id"])
    assert deleted and deleted["username"] == "delete-viewer"
    assert service.resolve_session(token) is None
    assert all(user["id"] != viewer["id"] for user in service.list_users())

    with pytest.raises(ValueError, match="最后一个启用"):
        service.delete_user(admin["id"])
