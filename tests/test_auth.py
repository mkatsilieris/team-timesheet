from fastapi.testclient import TestClient

from conftest import ADMIN, signup


def test_only_netcompany_emails(mailbox):
    with TestClient(mailbox["app"]) as c:
        for email in ["x@gmail.com", "x@netcompany.com.evil.io", "x@evil-netcompany.com", "not-an-email"]:
            r = c.post("/api/auth/register", json={"email": email, "name": "Test User"})
            assert r.status_code == 400, email


def test_data_requires_login(mailbox):
    with TestClient(mailbox["app"]) as c:
        for path in ["/api/state", "/api/version", "/api/log", "/api/export", "/api/backup"]:
            assert c.get(path).status_code == 401, path
        assert c.post("/api/changes", json={}).status_code == 401
        assert c.get("/healthz").status_code == 200
        assert c.get("/").status_code == 200


def test_new_user_is_pending_until_admin_approves(mailbox):
    app = mailbox["app"]
    with TestClient(app) as admin, TestClient(app) as user:
        signup(admin, mailbox, ADMIN, "Admin Test")
        me = signup(user, mailbox, "Nikos.Test@NetCompany.com", "TEST Nikos")
        assert me == {"email": "nikos.test@netcompany.com", "name": "TEST Nikos", "role": "pending"}
        assert user.get("/api/state").status_code == 403
        assert user.get("/api/admin/users").status_code == 403

        r = admin.post("/api/admin/users/role", json={"email": "nikos.test@netcompany.com", "role": "user"})
        assert r.status_code == 200, r.text
        assert user.get("/api/state").status_code == 200
        assert user.get("/api/admin/users").status_code == 403      # user δεν είναι admin

        admin.post("/api/admin/users/role", json={"email": "nikos.test@netcompany.com", "role": "pending"})
        assert user.get("/api/state").status_code == 403            # η πρόσβαση αφαιρέθηκε αμέσως

        notes = [l["note"] for l in admin.get("/api/log").json() if l["action"] == "Αλλαγή πρόσβασης"]
        assert notes == ["nikos.test@netcompany.com: user → pending", "nikos.test@netcompany.com: pending → user"]


def test_admin_cannot_change_own_role(client):
    r = client.post("/api/admin/users/role", json={"email": ADMIN, "role": "user"})
    assert r.status_code == 400


def test_password_is_stored_hashed_and_duplicate_register_fails(mailbox):
    with TestClient(mailbox["app"]) as c:
        signup(c, mailbox, ADMIN, "Admin Test")
        from app import db
        with db.read_conn() as conn:
            stored = conn.execute("SELECT password_hash FROM users").fetchone()[0]
        assert stored.startswith("scrypt$") and mailbox[ADMIN] not in stored
        r = c.post("/api/auth/register", json={"email": ADMIN, "name": "Again"})
        assert r.status_code == 409


def test_login_wrong_password_and_lockout(mailbox):
    with TestClient(mailbox["app"]) as c:
        c.post("/api/auth/register", json={"email": ADMIN, "name": "Admin Test"})
        for _ in range(10):
            assert c.post("/api/auth/login", json={"email": ADMIN, "password": "wrong"}).status_code == 401
        r = c.post("/api/auth/login", json={"email": ADMIN, "password": mailbox[ADMIN]})
        assert r.status_code == 429                                  # κλειδωμένος, ακόμα και με σωστό κωδικό
        assert c.post("/api/auth/login", json={"email": "nobody@netcompany.com", "password": "x"}).status_code == 401


def test_change_password(client, mailbox):
    old = mailbox[ADMIN]
    r = client.post("/api/auth/password", json={"current": "wrong", "new": "a-new-password"})
    assert r.status_code == 400
    r = client.post("/api/auth/password", json={"current": old, "new": "short"})
    assert r.status_code == 422
    r = client.post("/api/auth/password", json={"current": old, "new": "a-new-password"})
    assert r.status_code == 200
    assert client.get("/api/state").status_code == 200                # η τρέχουσα συσκευή μένει συνδεδεμένη
    client.post("/api/auth/logout")
    assert client.get("/api/state").status_code == 401
    assert client.post("/api/auth/login", json={"email": ADMIN, "password": old}).status_code == 401
    assert client.post("/api/auth/login", json={"email": ADMIN, "password": "a-new-password"}).status_code == 200


def test_forgot_password_keeps_old_until_new_is_used(mailbox, monkeypatch):
    from app import auth
    with TestClient(mailbox["app"]) as c:
        c.post("/api/auth/register", json={"email": ADMIN, "name": "Admin Test"})
        old = mailbox[ADMIN]
        assert c.post("/api/auth/forgot", json={"email": ADMIN}).status_code == 429   # cooldown μετά την εγγραφή
        monkeypatch.setattr(auth, "RESEND_COOLDOWN", 0)
        assert c.post("/api/auth/forgot", json={"email": ADMIN}).status_code == 200
        new = mailbox[ADMIN]
        assert new != old
        assert c.post("/api/auth/login", json={"email": ADMIN, "password": old}).status_code == 200
        assert c.post("/api/auth/forgot", json={"email": ADMIN}).status_code == 200
        newer = mailbox[ADMIN]
        assert c.post("/api/auth/login", json={"email": ADMIN, "password": newer}).status_code == 200
        assert c.post("/api/auth/login", json={"email": ADMIN, "password": old}).status_code == 401
        # Για ανύπαρκτο λογαριασμό η απάντηση είναι η ίδια.
        assert c.post("/api/auth/forgot", json={"email": "nobody@netcompany.com"}).status_code == 200


def test_failed_email_does_not_create_account(mailbox, monkeypatch):
    from app import auth

    def boom(*a):
        raise OSError("smtp down")
    monkeypatch.setattr(auth, "deliver_password", boom)
    with TestClient(mailbox["app"]) as c:
        r = c.post("/api/auth/register", json={"email": ADMIN, "name": "Admin Test"})
        assert r.status_code == 502
        monkeypatch.setattr(auth, "deliver_password", lambda email, name, password, reset: mailbox.__setitem__(email, password))
        assert c.post("/api/auth/register", json={"email": ADMIN, "name": "Admin Test"}).status_code == 200


def test_changes_are_logged_with_session_name(client):
    r = client.post("/api/changes", json={"user": "someone else", "entries": [
        {"date": "2026-10-01", "person": "Katsifi Nelly", "project": "Promitheas", "oldH": 0, "newH": 2}]})
    assert r.status_code == 200
    assert client.get("/api/log").json()[0]["user"] == "Admin Test"
