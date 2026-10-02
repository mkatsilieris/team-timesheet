"""Ημερήσια υπενθύμιση με email σε όσους η προηγούμενη εργάσιμη έχει λιγότερες από 8 ώρες.

Τρέχει κάθε εργάσιμη (Δευ–Παρ) από τις REMINDER_TIME (default 09:00, ώρα του container) και μία φορά ανά ημέρα:
η ημερομηνία της τελευταίας εκτέλεσης μένει στο meta.reminders_last_run. Ελέγχει μόνο χρήστες με
`notify_missing` που είναι ενεργά άτομα της ομάδας και είχαν εγκριθεί μέχρι εκείνη την ημέρα.
Αργίες δεν λαμβάνονται υπόψη· όποιος λείπει γράφει τις ώρες στο project της άδειας.
"""
from __future__ import annotations

import asyncio
import html
import logging
import os
from datetime import date, datetime, time as dtime, timedelta

from . import db, mailer

log = logging.getLogger("uvicorn.error")

DAY_HOURS = 8
GR_DAYS = ["Δευτέρα", "Τρίτη", "Τετάρτη", "Πέμπτη", "Παρασκευή", "Σάββατο", "Κυριακή"]


def previous_workday(today: date) -> date:
    d = today - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def reminder_time() -> dtime | None:
    raw = os.environ.get("REMINDER_TIME", "09:00").strip()
    if raw.lower() in ("", "off", "0"):
        return None
    h, m = raw.split(":")
    return dtime(int(h), int(m))


def missing_hours(day: date) -> list[dict]:
    """Όσοι έχουν ενεργή την υπενθύμιση και λιγότερες από 8 ώρες την `day`."""
    iso = day.isoformat()
    with db.read_conn() as conn:
        rows = conn.execute(
            """SELECT u.email, u.name AS user_name, t.name AS person,
                      COALESCE((SELECT SUM(e.hours) FROM entries e WHERE e.person = t.name AND e.date = ?), 0) AS hours
               FROM users u JOIN team t ON t.email = u.email
               WHERE u.notify_missing = 1 AND u.role IN ('user','admin') AND t.active = 1
                 AND substr(u.approved_at, 1, 10) <= ?""",
            (iso, iso),
        ).fetchall()
    return [dict(r) for r in rows if r["hours"] < DAY_HOURS]


def send_reminder(r: dict, day: date) -> None:
    when = f"{GR_DAYS[day.weekday()]} {day.strftime('%d/%m/%Y')}"
    hours = f"{r['hours']:g}"
    url = mailer.app_url()
    mailer.send_email(
        r["email"],
        f"Λείπουν ώρες στο Team Timesheet για {when}",
        f"Γεια σας {r['user_name']},\n\n"
        f"Για την {when} έχετε καταχωρίσει {hours} από τις {DAY_HOURS} ώρες.\n"
        f"Συμπληρώστε τις ώρες σας{' στο ' + url if url else ' στο Team Timesheet'}.\n\n"
        "Αν εκείνη την ημέρα είχατε άδεια, καταχωρίστε την στο project της άδειας.\n",
        f"<p>Γεια σας {html.escape(r['user_name'])},</p>"
        f"<p>Για την <b>{when}</b> έχετε καταχωρίσει <b>{hours}</b> από τις {DAY_HOURS} ώρες.</p>"
        + (f'<p>Συμπληρώστε τις ώρες σας στο <a href="{url}">{url}</a>.</p>' if url
           else "<p>Συμπληρώστε τις ώρες σας στο Team Timesheet.</p>")
        + '<p style="color:#5F6B70;font-size:12px">Αν εκείνη την ημέρα είχατε άδεια, καταχωρίστε την στο project της άδειας.</p>',
    )


def run(day: date, dry_run: bool = False) -> dict:
    """Στέλνει τις υπενθυμίσεις για την `day`. Με dry_run επιστρέφει μόνο ποιοι θα τις λάμβαναν."""
    targets = missing_hours(day)
    sent, failed = [], []
    if not dry_run:
        for r in targets:
            try:
                send_reminder(r, day)
                sent.append(r["email"])
            except Exception:
                log.exception("Αποτυχία αποστολής υπενθύμισης στο %s", r["email"])
                failed.append(r["email"])
        if targets:
            with db.write_tx() as conn:
                db.add_log(conn, "system", "Υπενθυμίσεις ωρών", date=day.isoformat(),
                           note=f"Στάλθηκαν: {', '.join(sent) or '—'}" + (f". Απέτυχαν: {', '.join(failed)}" if failed else ""))
    return {"day": day.isoformat(), "targets": targets, "sent": sent, "failed": failed}


def due_today(now: datetime) -> bool:
    at = reminder_time()
    if at is None or now.weekday() >= 5 or now.time() < at:
        return False
    with db.read_conn() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key='reminders_last_run'").fetchone()
    return not row or row["value"] != now.date().isoformat()


def run_if_due(now: datetime) -> None:
    if not due_today(now):
        return
    with db.write_tx() as conn:   # σημειώνεται πρώτα, ώστε ένα σφάλμα να μη στείλει τα ίδια emails ξανά και ξανά
        conn.execute("INSERT INTO meta(key,value) VALUES ('reminders_last_run', ?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (now.date().isoformat(),))
    result = run(previous_workday(now.date()))
    log.info("Υπενθυμίσεις για %s: στάλθηκαν %d, απέτυχαν %d", result["day"], len(result["sent"]), len(result["failed"]))


async def scheduler() -> None:
    """Ελέγχει κάθε λεπτό αν ήρθε η ώρα της σημερινής υπενθύμισης."""
    if reminder_time() is None:
        return
    while True:
        try:
            await asyncio.to_thread(run_if_due, datetime.now())
        except Exception:
            log.exception("Σφάλμα στις υπενθυμίσεις ωρών")
        await asyncio.sleep(60)
