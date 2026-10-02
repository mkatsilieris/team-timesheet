"""SQLite αποθήκευση: ομάδα, projects, καταχωρίσεις ωρών και ιστορικό αλλαγών (append-only)."""
from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
DB_PATH = DATA_DIR / "timesheet.db"

# Ένας writer τη φορά. Οι αναγνώσεις γίνονται παράλληλα χάρη στο WAL.
WRITE_LOCK = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS team (
    name   TEXT PRIMARY KEY,
    grade  INTEGER,
    type   TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1,
    email  TEXT                        -- ο λογαριασμός (users.email) του ατόμου· προστίθεται στο migrate()
);
CREATE TABLE IF NOT EXISTS projects (
    name   TEXT PRIMARY KEY,
    active INTEGER NOT NULL DEFAULT 1,
    sort   INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS entries (
    date       TEXT NOT NULL,          -- YYYY-MM-DD
    person     TEXT NOT NULL,
    project    TEXT NOT NULL,
    hours      REAL NOT NULL,
    updated_by TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (date, person, project)
);
CREATE INDEX IF NOT EXISTS ix_entries_person_date ON entries(person, date);
CREATE TABLE IF NOT EXISTS log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    user      TEXT NOT NULL,
    action    TEXT NOT NULL,
    date      TEXT,
    person    TEXT,
    project   TEXT,
    old_hours REAL,
    new_hours REAL,
    note      TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS users (
    email         TEXT PRIMARY KEY,    -- πάντα πεζά, @netcompany.com
    name          TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'pending',   -- pending | user | admin
    password_hash TEXT NOT NULL,       -- scrypt· ο κωδικός δεν αποθηκεύεται πουθενά
    reset_hash    TEXT NOT NULL DEFAULT '',          -- νέος κωδικός από «Ξέχασα τον κωδικό», μέχρι να χρησιμοποιηθεί
    reset_at      REAL NOT NULL DEFAULT 0,           -- epoch seconds της τελευταίας αποστολής κωδικού
    failed_logins INTEGER NOT NULL DEFAULT 0,
    locked_until  REAL NOT NULL DEFAULT 0,
    notify_missing INTEGER NOT NULL DEFAULT 0,       -- email αν η προηγούμενη εργάσιμη έχει < 8 ώρες (ο admin το ανάβει)
    created_at    TEXT NOT NULL,
    approved_by   TEXT NOT NULL DEFAULT '',
    approved_at   TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,       -- sha256 του token του cookie· το ίδιο το token δεν αποθηκεύεται
    email      TEXT NOT NULL REFERENCES users(email) ON DELETE CASCADE,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
INSERT OR IGNORE INTO meta(key, value) VALUES ('version', '0');
"""

SEED_PROJECTS = [
    "Wiseman",
    "AutoResolve Tickets AI (OCT)",
    "Unstuck Orders AI",
    "Agentic Wiseman",
    "Intent Classifier Solution",
    "Promitheas",
    "Annual Leave",
]


def now_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def read_conn():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def write_tx():
    """Σειριακή συναλλαγή εγγραφής· αυξάνει το version ώστε οι clients να βλέπουν ότι κάτι άλλαξε."""
    with WRITE_LOCK:
        conn = connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("UPDATE meta SET value = CAST(value AS INTEGER) + 1 WHERE key = 'version'")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()


def get_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT value FROM meta WHERE key='version'").fetchone()[0])


def add_log(conn, user, action, date=None, person=None, project=None, old=None, new=None, note="", ts=None):
    conn.execute(
        "INSERT INTO log(ts,user,action,date,person,project,old_hours,new_hours,note) VALUES (?,?,?,?,?,?,?,?,?)",
        (ts or now_stamp(), user, action, date, person, project, old, new, note),
    )


def unique_team_name(conn, name: str, email: str) -> str:
    """Το όνομα είναι κλειδί των καταχωρίσεων· αν το έχει ήδη άλλος, προστίθεται το πρώτο μέρος του email."""
    taken = conn.execute("SELECT email FROM team WHERE name=?", (name,)).fetchone()
    if not taken or taken["email"] == email:
        return name
    return f"{name} ({email.split('@')[0]})"


def sync_team_member(conn, email: str, by: str, ts: str | None = None) -> None:
    """Τα άτομα της ομάδας είναι οι εγκεκριμένοι χρήστες: ένας user/admin είναι ενεργό άτομο, ένας pending όχι."""
    user = conn.execute("SELECT name, role FROM users WHERE email=?", (email,)).fetchone()
    if not user:
        return
    approved = user["role"] in ("user", "admin")
    row = conn.execute("SELECT name, active FROM team WHERE email=?", (email,)).fetchone()
    if row:
        if bool(row["active"]) != approved:
            conn.execute("UPDATE team SET active=? WHERE email=?", (int(approved), email))
            add_log(conn, by, "Αλλαγή ατόμου", person=row["name"],
                    note="ενεργό (εγκρίθηκε η πρόσβαση)" if approved else "ανενεργό (αφαιρέθηκε η πρόσβαση)", ts=ts)
    elif approved:
        name = unique_team_name(conn, user["name"], email)
        conn.execute("INSERT INTO team(name,grade,type,active,email) VALUES (?,NULL,'',1,?)", (name, email))
        add_log(conn, by, "Νέο άτομο", person=name, note=f"Από τον λογαριασμό {email}", ts=ts)


def migrate() -> None:
    """Μία φορά ανά βάση: τα άτομα γίνονται οι εγκεκριμένοι χρήστες και σβήνονται οι ώρες και τα άτομα
    της εποχής χωρίς λογαριασμούς. Το log δεν σβήνεται· καταγράφει τη διαγραφή."""
    with read_conn() as conn:
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(team)")]
        user_cols = [r["name"] for r in conn.execute("PRAGMA table_info(users)")]
        done = conn.execute("SELECT 1 FROM meta WHERE key='team_from_users'").fetchone()
    if "notify_missing" not in user_cols:
        with write_tx() as conn:
            conn.execute("ALTER TABLE users ADD COLUMN notify_missing INTEGER NOT NULL DEFAULT 0")
    if "email" in cols and done:
        return
    with write_tx() as conn:
        if "email" not in cols:
            conn.execute("ALTER TABLE team ADD COLUMN email TEXT")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_team_email ON team(email)")
        if not done:
            n_entries = conn.execute("DELETE FROM entries").rowcount
            n_people = conn.execute("DELETE FROM team WHERE email IS NULL").rowcount
            if n_entries or n_people:
                add_log(conn, "system", "Διαγραφή παλιών δεδομένων",
                        note=f"Σβήστηκαν {n_entries} καταχωρίσεις ωρών και {n_people} άτομα. "
                             "Από εδώ και πέρα τα άτομα είναι οι εγκεκριμένοι χρήστες.")
            for u in conn.execute("SELECT email FROM users WHERE role IN ('user','admin')").fetchall():
                sync_team_member(conn, u["email"], "system")
            conn.execute("INSERT INTO meta(key,value) VALUES ('team_from_users','1')")


def init_db() -> None:
    """Δημιουργεί το σχήμα. Σε άδεια βάση: εισάγει το data/Timesheet_DB.xlsx αν υπάρχει, αλλιώς τα αρχικά projects."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = connect()
    try:
        conn.executescript(SCHEMA)
    finally:
        conn.close()
    migrate()
    with read_conn() as conn:
        empty = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0
    if not empty:
        return
    legacy = DATA_DIR / "Timesheet_DB.xlsx"
    if legacy.exists():
        from .importer import import_legacy_xlsx

        import_legacy_xlsx(legacy)
        return
    with write_tx() as conn:
        conn.executemany(
            "INSERT INTO projects(name,active,sort) VALUES (?,1,?)", [(p, i) for i, p in enumerate(SEED_PROJECTS)]
        )
        add_log(conn, "system", "Δημιουργία βάσης", note="Αρχικά projects")
