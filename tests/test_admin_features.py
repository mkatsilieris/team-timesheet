from datetime import date, datetime

from fastapi.testclient import TestClient

from conftest import signup


def create(admin, email, name, role="user"):
    return admin.post("/api/admin/users", json={"email": email, "name": name, "role": role})


def login(app, mailbox, email):
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"email": email, "password": mailbox[email]}).status_code == 200
    return c


def hours(c, person, day, h):
    return c.post("/api/changes", json={"entries": [
        {"date": day, "person": person, "project": "Wiseman", "oldH": 0, "newH": h}]})


# ---------- Δημιουργία χρηστών από admin ----------
def test_admin_creates_user_and_password_is_emailed(client, mailbox):
    r = create(client, "New.Hire@netcompany.com", "HIRE New")
    assert r.status_code == 200, r.text
    created = next(u for u in r.json() if u["email"] == "new.hire@netcompany.com")
    assert created["role"] == "user" and created["approved_by"] == "admin.test@netcompany.com"
    with login(mailbox["app"], mailbox, "new.hire@netcompany.com") as c:
        assert c.get("/api/state").status_code == 200                     # εγκεκριμένος από την αρχή
    team = {t["name"]: t for t in client.get("/api/state").json()["team"]}
    assert team["HIRE New"]["active"] is True and team["HIRE New"]["email"] == "new.hire@netcompany.com"
    assert create(client, "new.hire@netcompany.com", "Again").status_code == 409
    assert create(client, "x@gmail.com", "Outsider").status_code == 400


def test_admin_can_create_admin_and_users_cannot_create(client, mailbox):
    assert create(client, "second.admin@netcompany.com", "ADMIN Second", "admin").status_code == 200
    with login(mailbox["app"], mailbox, "second.admin@netcompany.com") as c:
        assert c.get("/api/admin/users").status_code == 200
    create(client, "plain@netcompany.com", "PLAIN User")
    with login(mailbox["app"], mailbox, "plain@netcompany.com") as c:
        assert create(c, "sneaky@netcompany.com", "SNEAKY").status_code == 403


# ---------- Ώρες: ο χρήστης μόνο τις δικές του, ο admin όλων ----------
def test_users_edit_only_own_hours_admins_edit_all(client, mailbox):
    create(client, "a@netcompany.com", "USER A")
    create(client, "b@netcompany.com", "USER B")
    with login(mailbox["app"], mailbox, "a@netcompany.com") as a:
        assert hours(a, "USER A", "2026-10-01", 8).status_code == 200
        r = hours(a, "USER B", "2026-10-01", 8)
        assert r.status_code == 403 and "δικές σας" in r.json()["detail"]
        assert a.get("/api/state").status_code == 200                       # βλέπει όλους
    assert hours(client, "USER B", "2026-10-01", 6).status_code == 200      # ο admin γράφει για άλλον
    log = client.get("/api/log").json()[0]
    assert log["user"] == "Admin Test" and log["person"] == "USER B"


# ---------- Υπενθυμίσεις ----------
def test_previous_workday():
    from app.reminders import previous_workday
    assert previous_workday(date(2026, 10, 5)) == date(2026, 10, 2)        # Δευτέρα → Παρασκευή
    assert previous_workday(date(2026, 10, 2)) == date(2026, 10, 1)        # Παρασκευή → Πέμπτη


def test_notify_toggle_and_reminders(client, mailbox):
    from app import reminders
    day = "2026-10-01"
    for email, name in [("short@netcompany.com", "SHORT"), ("full@netcompany.com", "FULL"),
                        ("quiet@netcompany.com", "QUIET")]:
        create(client, email, name)
    hours(client, "SHORT", day, 3)
    hours(client, "FULL", day, 8)
    for email in ["short@netcompany.com", "full@netcompany.com"]:              # ο QUIET δεν έχει υπενθύμιση
        r = client.post("/api/admin/users/notify", json={"email": email, "enabled": True})
        assert r.status_code == 200
    users = {u["email"]: u for u in r.json()}
    assert users["short@netcompany.com"]["notify_missing"] and not users["quiet@netcompany.com"]["notify_missing"]

    # Όσοι εγκρίθηκαν μετά την ημέρα δεν ελέγχονται· εδώ τους «εγκρίνουμε» νωρίτερα.
    from app import db
    with db.write_tx() as conn:
        conn.execute("UPDATE users SET approved_at='2026-09-01 09:00:00'")

    preview = client.post("/api/admin/reminders/run", json={"day": day, "dry_run": True}).json()
    assert [t["email"] for t in preview["targets"]] == ["short@netcompany.com"] and mailbox["sent"] == []

    sent = client.post("/api/admin/reminders/run", json={"day": day, "dry_run": False}).json()
    assert sent["sent"] == ["short@netcompany.com"]
    to, subject, text = mailbox["sent"][0]
    assert to == "short@netcompany.com" and "01/10/2026" in subject and "3 από τις 8" in text

    # Ο scheduler στέλνει μία φορά ανά ημέρα, μόνο εργάσιμες, μετά την ώρα.
    mailbox["sent"].clear()
    import os
    os.environ["REMINDER_TIME"] = "09:00"
    try:
        reminders.run_if_due(datetime(2026, 10, 2, 8, 59))                  # πριν τις 09:00
        assert mailbox["sent"] == []
        reminders.run_if_due(datetime(2026, 10, 2, 9, 1))                   # Παρασκευή → ελέγχει την Πέμπτη 01/10
        assert [s[0] for s in mailbox["sent"]] == ["short@netcompany.com"]
        reminders.run_if_due(datetime(2026, 10, 2, 15, 0))                  # ίδια μέρα: όχι ξανά
        reminders.run_if_due(datetime(2026, 10, 3, 9, 30))                  # Σάββατο: τίποτα
        assert len(mailbox["sent"]) == 1
    finally:
        os.environ["REMINDER_TIME"] = "off"


def test_user_approved_after_the_day_is_not_reminded(client, mailbox):
    create(client, "late@netcompany.com", "LATE")
    client.post("/api/admin/users/notify", json={"email": "late@netcompany.com", "enabled": True})
    preview = client.post("/api/admin/reminders/run", json={"day": "2020-01-02", "dry_run": True}).json()
    assert preview["targets"] == []


def test_reminder_endpoints_are_admin_only(client, mailbox):
    create(client, "plain@netcompany.com", "PLAIN")
    with login(mailbox["app"], mailbox, "plain@netcompany.com") as c:
        assert c.post("/api/admin/reminders/run", json={"dry_run": True}).status_code == 403
        assert c.post("/api/admin/users/notify", json={"email": "plain@netcompany.com", "enabled": True}).status_code == 403
