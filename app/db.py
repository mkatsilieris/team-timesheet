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
    active INTEGER NOT NULL DEFAULT 1
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
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
INSERT OR IGNORE INTO meta(key, value) VALUES ('version', '0');
"""

SEED_TEAM = [
    ("KOUTSELIS Dimitris", 8, "DEV"),
    ("MELADAKI Chrysoula", 8, "DEV"),
    ("DIMITRAINA Ergina", 8, "DEV"),
    ("KATSILIERIS Michael", 12, "DEV"),
    ("Katsifi Nelly", 11, "PM"),
]
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


def init_db() -> None:
    """Δημιουργεί το σχήμα. Σε άδεια βάση: εισάγει το data/Timesheet_DB.xlsx αν υπάρχει, αλλιώς τα αρχικά δεδομένα."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        empty = conn.execute("SELECT (SELECT COUNT(*) FROM team) + (SELECT COUNT(*) FROM projects)").fetchone()[0] == 0
    finally:
        conn.close()
    if not empty:
        return
    legacy = DATA_DIR / "Timesheet_DB.xlsx"
    if legacy.exists():
        from .importer import import_legacy_xlsx

        import_legacy_xlsx(legacy)
        return
    with write_tx() as conn:
        conn.executemany("INSERT INTO team(name,grade,type,active) VALUES (?,?,?,1)", SEED_TEAM)
        conn.executemany(
            "INSERT INTO projects(name,active,sort) VALUES (?,1,?)", [(p, i) for i, p in enumerate(SEED_PROJECTS)]
        )
        add_log(conn, "system", "Δημιουργία βάσης", note="Αρχικά δεδομένα ομάδας και projects")
