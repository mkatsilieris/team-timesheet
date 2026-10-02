import importlib

import pytest
from fastapi.testclient import TestClient

ADMIN = "admin.test@netcompany.com"


@pytest.fixture()
def mailbox(tmp_path, monkeypatch):
    """Ξεκινά την εφαρμογή σε άδεια βάση· τα emails κωδικών πάνε σε αυτό το dict αντί για SMTP."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ADMIN_EMAILS", ADMIN)
    monkeypatch.delenv("SMTP_HOST", raising=False)
    from app import db
    importlib.reload(db)
    from app import auth, export, importer, main
    importlib.reload(importer); importlib.reload(export); importlib.reload(auth); importlib.reload(main)
    box = {}
    monkeypatch.setattr(auth, "deliver_password", lambda email, name, password, reset: box.__setitem__(email, password))
    box["app"] = main.app
    return box


def signup(c, box, email, name):
    r = c.post("/api/auth/register", json={"email": email, "name": name})
    assert r.status_code == 200, r.text
    r = c.post("/api/auth/login", json={"email": email, "password": box[email.strip().lower()]})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture()
def client(mailbox):
    """Client συνδεδεμένος ως admin (από το ADMIN_EMAILS)."""
    with TestClient(mailbox["app"]) as c:
        signup(c, mailbox, ADMIN, "Admin Test")
        yield c
