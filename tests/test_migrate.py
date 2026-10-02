import sqlite3

from fastapi.testclient import TestClient

from conftest import ADMIN

OLD_SCHEMA = """
CREATE TABLE team (name TEXT PRIMARY KEY, grade INTEGER, type TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE projects (name TEXT PRIMARY KEY, active INTEGER NOT NULL DEFAULT 1, sort INTEGER NOT NULL DEFAULT 0);
CREATE TABLE entries (date TEXT NOT NULL, person TEXT NOT NULL, project TEXT NOT NULL, hours REAL NOT NULL,
    updated_by TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT '', PRIMARY KEY (date, person, project));
CREATE TABLE log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, user TEXT NOT NULL, action TEXT NOT NULL,
    date TEXT, person TEXT, project TEXT, old_hours REAL, new_hours REAL, note TEXT NOT NULL DEFAULT '');
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT INTO meta VALUES ('version', '7');
INSERT INTO team VALUES ('KATSILIERIS Michael', 12, 'DEV', 1), ('Katsifi Nelly', 11, 'PM', 1);
INSERT INTO projects VALUES ('Wiseman', 1, 0);
INSERT INTO entries VALUES ('2026-10-01', 'Katsifi Nelly', 'Wiseman', 6, 'x', 'y');
INSERT INTO log(ts,user,action,note) VALUES ('2026-10-01 10:00:00', 'Nikos', 'Προσθήκη', 'παλιά εγγραφή');
CREATE TABLE users (email TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'pending',
    password_hash TEXT NOT NULL, reset_hash TEXT NOT NULL DEFAULT '', reset_at REAL NOT NULL DEFAULT 0,
    failed_logins INTEGER NOT NULL DEFAULT 0, locked_until REAL NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
    approved_by TEXT NOT NULL DEFAULT '', approved_at TEXT NOT NULL DEFAULT '');
INSERT INTO users(email,name,role,password_hash,created_at) VALUES
    ('approved@netcompany.com', 'APPROVED User', 'user', 'x', 'now'),
    ('waiting@netcompany.com', 'WAITING User', 'pending', 'x', 'now');
"""


def test_migration_wipes_old_people_data_once(tmp_path, mailbox):
    db_file = tmp_path / "timesheet.db"
    conn = sqlite3.connect(db_file)
    conn.executescript(OLD_SCHEMA)
    conn.close()

    with TestClient(mailbox["app"]) as c:
        from conftest import signup
        signup(c, mailbox, ADMIN, "Admin Test")
        s = c.get("/api/state").json()
        assert sorted(t["name"] for t in s["team"]) == ["APPROVED User", "Admin Test"]
        assert s["entries"] == [] and [p["name"] for p in s["projects"]] == ["Wiseman"]
        log = c.get("/api/log").json()
        wipe = [l for l in log if l["action"] == "Διαγραφή παλιών δεδομένων"]
        assert len(wipe) == 1 and "1 καταχωρίσεις ωρών και 2 άτομα" in wipe[0]["note"]
        assert any(l["note"] == "παλιά εγγραφή" for l in log)        # το log δεν σβήνεται

        # Νέες ώρες μετά το migration δεν σβήνονται σε επόμενη εκκίνηση.
        r = c.post("/api/changes", json={"entries": [
            {"date": "2026-10-02", "person": "Admin Test", "project": "Wiseman", "oldH": 0, "newH": 3}]})
        assert r.status_code == 200
    with TestClient(mailbox["app"]) as c:
        signup_login = c.post("/api/auth/login", json={"email": ADMIN, "password": mailbox[ADMIN]})
        assert signup_login.status_code == 200
        assert len(c.get("/api/state").json()["entries"]) == 1
