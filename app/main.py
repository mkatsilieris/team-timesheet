"""Team Timesheet — FastAPI backend. Σερβίρει το UI (static/index.html) και ένα μικρό JSON API."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Annotated, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field, StringConstraints

from . import auth, db, reminders
from .auth import Admin, Member
from .export import build_export

STATIC = Path(__file__).parent / "static"
ISO_DATE = r"^\d{4}-\d{2}-\d{2}$"
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120, pattern=r"^[^|]+$")]
User = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    task = asyncio.create_task(reminders.scheduler())
    yield
    task.cancel()


app = FastAPI(title="Team Timesheet", lifespan=lifespan)
app.include_router(auth.router)


# ---------- Μοντέλα ----------
class EntryChange(BaseModel):
    date: str = Field(pattern=ISO_DATE)
    person: Name
    project: Name
    oldH: float = Field(ge=0, le=24)   # τιμή που είχε ο χρήστης στην οθόνη όταν έκανε την αλλαγή
    newH: float = Field(ge=0, le=24)   # 0 = διαγραφή


class TeamMember(BaseModel):
    name: Name
    grade: Optional[int] = None
    type: str = ""
    active: bool = True


class Project(BaseModel):
    name: Name
    active: bool = True


class Changes(BaseModel):
    user: Optional[User] = None   # αγνοείται· το ιστορικό γράφει το όνομα του συνδεδεμένου χρήστη
    entries: list[EntryChange] = []
    team: list[TeamMember] = []
    projects: list[Project] = []


# ---------- Βοηθητικά ----------
def state_payload() -> dict:
    with db.read_conn() as conn:
        return {
            "version": db.get_version(conn),
            "team": [
                {"name": r["name"], "grade": r["grade"], "type": r["type"], "active": bool(r["active"]),
                 "email": r["email"]}
                for r in conn.execute("SELECT * FROM team ORDER BY name COLLATE NOCASE")
            ],
            "projects": [
                {"name": r["name"], "active": bool(r["active"])}
                for r in conn.execute("SELECT * FROM projects ORDER BY sort, name")
            ],
            "entries": [
                {"date": r["date"], "person": r["person"], "project": r["project"], "hours": r["hours"],
                 "by": r["updated_by"], "at": r["updated_at"]}
                for r in conn.execute("SELECT * FROM entries")
            ],
        }


# ---------- API ----------
@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/api/state")
def get_state(_: Member):
    return state_payload()


@app.get("/api/version")
def get_version(_: Member):
    with db.read_conn() as conn:
        return {"version": db.get_version(conn)}


@app.get("/api/log")
def get_log(_: Member, limit: int = Query(2000, ge=1, le=20000)):
    with db.read_conn() as conn:
        rows = conn.execute("SELECT * FROM log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/changes")
def post_changes(ch: Changes, user: Member):
    """Εφαρμόζει μια δέσμη αλλαγών σε μία συναλλαγή και γράφει κάθε αλλαγή στο log.

    Αν στο μεταξύ κάποιος άλλος άλλαξε την ίδια τιμή (τρέχουσα τιμή != oldH), κερδίζει η νεότερη αλλαγή
    και το log σημειώνει τη σύγκρουση.
    """
    is_admin = user["role"] == "admin"
    if ch.team and not is_admin:
        raise HTTPException(403, "Τα στοιχεία των ατόμων τα αλλάζει μόνο διαχειριστής.")
    conflicts = 0
    now = db.now_stamp()
    with db.write_tx() as conn:
        if not is_admin:
            # Ένας απλός χρήστης γράφει μόνο τις δικές του ώρες· οι διαχειριστές γράφουν για όλους.
            own = conn.execute("SELECT name FROM team WHERE email=?", (user["email"],)).fetchone()
            others = {e.person for e in ch.entries if not own or e.person != own["name"]}
            if others:
                raise HTTPException(403, "Μπορείτε να αλλάξετε μόνο τις δικές σας ώρες. "
                                         "Για ώρες άλλων απευθυνθείτε σε έναν διαχειριστή.")
        for t in ch.team:
            # Τα άτομα δημιουργούνται μόνο από την έγκριση χρηστών (db.sync_team_member)· εδώ μόνο αλλάζουν.
            before = conn.execute("SELECT * FROM team WHERE name=?", (t.name,)).fetchone()
            if not before:
                raise HTTPException(400, f"Δεν υπάρχει άτομο «{t.name}». Τα άτομα προστίθενται όταν εγκρίνετε την εγγραφή τους.")
            conn.execute("UPDATE team SET grade=?, type=?, active=? WHERE name=?",
                         (t.grade, t.type, int(t.active), t.name))
            desc = (f"Grade {t.grade}, {t.type}, {'ενεργό' if t.active else 'ανενεργό'} "
                    f"(πριν: Grade {before['grade']}, {before['type']}, {'ενεργό' if before['active'] else 'ανενεργό'})")
            db.add_log(conn, user["name"], "Αλλαγή ατόμου", person=t.name, note=desc, ts=now)

        for p in ch.projects:
            before = conn.execute("SELECT * FROM projects WHERE name=?", (p.name,)).fetchone()
            if before:
                conn.execute("UPDATE projects SET active=? WHERE name=?", (int(p.active), p.name))
                action = "Ενεργοποίηση project" if p.active else "Απενεργοποίηση project"
            else:
                nxt = conn.execute("SELECT COALESCE(MAX(sort),-1)+1 FROM projects").fetchone()[0]
                conn.execute("INSERT INTO projects(name,active,sort) VALUES (?,?,?)", (p.name, int(p.active), nxt))
                action = "Νέο project"
            db.add_log(conn, user["name"], action, project=p.name, ts=now)

        for e in ch.entries:
            row = conn.execute(
                "SELECT hours, updated_by FROM entries WHERE date=? AND person=? AND project=?",
                (e.date, e.person, e.project),
            ).fetchone()
            cur = row["hours"] if row else 0.0
            new = round(e.newH, 2)
            if cur == new:
                continue
            note = ""
            if cur != e.oldH:
                conflicts += 1
                note = (f"Στο μεταξύ η τιμή είχε αλλάξει από {row['updated_by'] if row else 'άλλον χρήστη'} "
                        f"σε {cur:g}. Κρατήθηκε η νεότερη τιμή.")
            if new > 0:
                conn.execute(
                    "INSERT INTO entries(date,person,project,hours,updated_by,updated_at) VALUES (?,?,?,?,?,?) "
                    "ON CONFLICT(date,person,project) DO UPDATE SET hours=excluded.hours, "
                    "updated_by=excluded.updated_by, updated_at=excluded.updated_at",
                    (e.date, e.person, e.project, new, user["name"], now),
                )
            else:
                conn.execute("DELETE FROM entries WHERE date=? AND person=? AND project=?", (e.date, e.person, e.project))
            action = "Προσθήκη" if cur == 0 else ("Διαγραφή" if new == 0 else "Αλλαγή")
            db.add_log(conn, user["name"], action, e.date, e.person, e.project, cur, new, note, ts=now)

    out = state_payload()
    out["conflicts"] = conflicts
    return out


class ReminderRun(BaseModel):
    day: Optional[str] = Field(None, pattern=ISO_DATE)   # default: η προηγούμενη εργάσιμη
    dry_run: bool = True


@app.post("/api/admin/reminders/run")
def run_reminders(body: ReminderRun, _: Admin):
    """Προεπισκόπηση (dry_run) ή άμεση αποστολή των υπενθυμίσεων για ελλιπείς ώρες."""
    day = date.fromisoformat(body.day) if body.day else reminders.previous_workday(date.today())
    return reminders.run(day, dry_run=body.dry_run)


@app.get("/api/export")
def export(
    _: Member,
    date_from: Optional[str] = Query(None, alias="from", pattern=ISO_DATE),
    date_to: Optional[str] = Query(None, alias="to", pattern=ISO_DATE),
):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(400, "Η ημερομηνία «Από» είναι μετά την «Έως».")
    data, a, b = build_export(date_from, date_to)
    fname = f"Timesheet_{a}_{b}.xlsx"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.get("/api/backup")
def backup(_: Member):
    """Κατεβάζει αντίγραφο της SQLite βάσης (συνεπές, μέσω του backup API της SQLite)."""
    import sqlite3
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp_path = tmp.name
    src = db.connect()
    dst = sqlite3.connect(tmp_path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    data = Path(tmp_path).read_bytes()
    Path(tmp_path).unlink(missing_ok=True)
    return Response(content=data, media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="timesheet_{db.now_stamp()[:10]}.db"'})


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})
