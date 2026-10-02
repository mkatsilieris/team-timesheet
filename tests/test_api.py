import io

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from conftest import signup


def post(c, user, entries=(), team=(), projects=()):
    r = c.post("/api/changes", json={"user": user, "entries": list(entries), "team": list(team), "projects": list(projects)})
    assert r.status_code == 200, r.text
    return r.json()


def approve(admin, mailbox, email, name):
    """Εγγραφή νέου χρήστη και έγκριση από τον admin· επιστρέφει client συνδεδεμένο ως αυτός."""
    c = TestClient(mailbox["app"])
    signup(c, mailbox, email, name)
    r = admin.post("/api/admin/users/role", json={"email": email, "role": "user"})
    assert r.status_code == 200, r.text
    return c


def test_seed(client):
    s = client.get("/api/state").json()
    # Η ομάδα είναι μόνο οι εγκεκριμένοι χρήστες: εδώ ο admin του fixture.
    assert [t["name"] for t in s["team"]] == ["Admin Test"]
    assert len(s["projects"]) == 7 and s["entries"] == []


def test_entry_lifecycle_and_log(client):
    e = {"date": "2026-10-01", "person": "Admin Test", "project": "Wiseman"}
    s = post(client, "Nikos", [{**e, "oldH": 0, "newH": 4.5}])
    assert s["entries"][0]["hours"] == 4.5 and s["conflicts"] == 0
    post(client, "Nikos", [{**e, "oldH": 4.5, "newH": 3}])
    s = post(client, "Nikos", [{**e, "oldH": 3, "newH": 0}])
    assert s["entries"] == []
    actions = [l["action"] for l in client.get("/api/log").json()]
    assert actions[:3] == ["Διαγραφή", "Αλλαγή", "Προσθήκη"]


def test_conflict_is_logged(client):
    e = {"date": "2026-10-01", "person": "Admin Test", "project": "Promitheas"}
    post(client, "A", [{**e, "oldH": 0, "newH": 2}])
    s = post(client, "B", [{**e, "oldH": 0, "newH": 5}])  # ο B δεν είχε δει την αλλαγή του A
    assert s["conflicts"] == 1 and s["entries"][0]["hours"] == 5
    assert "Admin Test" in client.get("/api/log").json()[0]["note"]


def test_validation(client):
    r = client.post("/api/changes", json={"user": "x", "entries": [
        {"date": "2026-10-01", "person": "P", "project": "J", "oldH": 0, "newH": 30}]})
    assert r.status_code == 422


def test_new_person_project_and_export(client, mailbox):
    with approve(client, mailbox, "new.person@netcompany.com", "NEW Person"):
        pass
    post(client, "Nikos", team=[{"name": "NEW Person", "grade": 9, "type": "DEV", "active": True}],
         projects=[{"name": "New Proj", "active": True}])
    post(client, "Nikos", [{"date": "2026-10-02", "person": "NEW Person", "project": "New Proj", "oldH": 0, "newH": 6}])
    r = client.get("/api/export?from=2026-10-01&to=2026-10-31")
    assert r.status_code == 200
    wb = load_workbook(io.BytesIO(r.content))
    assert "NEW Person" in wb.sheetnames and "Summary" in wb.sheetnames and "Log" in wb.sheetnames
    ws = wb["NEW Person"]
    headers = [c.value for c in ws[3]]
    assert "New Proj" in headers
    col = headers.index("New Proj") + 1
    row = next(r for r in range(4, ws.max_row) if ws.cell(r, 1).value and ws.cell(r, 1).value.day == 2)
    assert ws.cell(row, col).value == 6


def test_team_follows_user_approval(client, mailbox):
    team = lambda: {t["name"]: t for t in client.get("/api/state").json()["team"]}
    c = TestClient(mailbox["app"])
    signup(c, mailbox, "pending.one@netcompany.com", "PENDING One")
    assert "PENDING One" not in team()                       # pending: δεν είναι άτομο της ομάδας
    client.post("/api/admin/users/role", json={"email": "pending.one@netcompany.com", "role": "user"})
    assert team()["PENDING One"]["active"] is True           # εγκρίθηκε: ενεργό άτομο
    client.post("/api/admin/users/role", json={"email": "pending.one@netcompany.com", "role": "pending"})
    assert team()["PENDING One"]["active"] is False          # αφαιρέθηκε η πρόσβαση: ανενεργό, οι ώρες μένουν
    c.close()


def test_same_name_gets_distinct_team_entry(client, mailbox):
    approve(client, mailbox, "other.admin@netcompany.com", "Admin Test").close()
    names = sorted(t["name"] for t in client.get("/api/state").json()["team"])
    assert names == ["Admin Test", "Admin Test (other.admin)"]


def test_only_admin_edits_people_and_no_new_people(client, mailbox):
    user = approve(client, mailbox, "plain.user@netcompany.com", "PLAIN User")
    r = user.post("/api/changes", json={"team": [{"name": "PLAIN User", "grade": 12, "type": "DEV", "active": True}]})
    assert r.status_code == 403
    r = client.post("/api/changes", json={"team": [{"name": "Ghost", "grade": 1, "type": "DEV", "active": True}]})
    assert r.status_code == 400
    r = client.post("/api/changes", json={"team": [{"name": "PLAIN User", "grade": None, "type": "", "active": True}]})
    assert r.status_code == 200                               # κενό Grade επιτρέπεται
    # Ο απλός χρήστης γράφει κανονικά ώρες και projects.
    post(user, "PLAIN User", [{"date": "2026-10-01", "person": "PLAIN User", "project": "Wiseman", "oldH": 0, "newH": 8}])
    user.close()
