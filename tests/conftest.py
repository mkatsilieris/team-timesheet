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
    monkeypatch.setenv("REMINDER_TIME", "off")      # ο scheduler δεν τρέχει στα tests· τα tests καλούν το reminders απευθείας
    from app import db
    importlib.reload(db)
    from app import auth, export, importer, mailer, main, reminders
    for m in (importer, export, mailer, auth, reminders, main):
        importlib.reload(m)
    box = {"sent": []}
    monkeypatch.setattr(auth, "deliver_password", lambda email, name, password, kind: box.__setitem__(email, password))
    monkeypatch.setattr(mailer, "send_email", lambda to, subject, text, html: box["sent"].append((to, subject, text)))
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
