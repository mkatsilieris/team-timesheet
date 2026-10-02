"""Εγγραφή με εταιρικό email (@netcompany.com), κωδικός πρόσβασης στο email, sessions με cookie και ρόλοι.

Ροή: ο χρήστης κάνει εγγραφή με email και ονοματεπώνυμο και λαμβάνει με email έναν μόνιμο κωδικό.
Συνδέεται με email και κωδικό, και μπορεί να αλλάξει τον κωδικό του. Αν τον ξεχάσει, ζητά νέο με email·
ο παλιός κωδικός ισχύει μέχρι να χρησιμοποιηθεί ο νέος, ώστε κάποιος τρίτος να μην μπορεί να τον κλειδώσει έξω.
Οι κωδικοί αποθηκεύονται μόνο ως scrypt hash. Ένας νέος χρήστης ξεκινά ως `pending` και βλέπει μόνο
τη σελίδα αναμονής μέχρι να τον εγκρίνει ένας admin. Τα emails της ADMIN_EMAILS γίνονται admin αυτόματα.
Χωρίς SMTP_HOST ο κωδικός γράφεται στο log του server αντί να σταλεί (για τοπικό development).
"""
from __future__ import annotations

import hashlib
import hmac
import html
import logging
import os
import re
import secrets
import time
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, StringConstraints

from . import db, mailer

log = logging.getLogger("uvicorn.error")

EMAIL_DOMAIN = "netcompany.com"
EMAIL_RE = re.compile(r"^[a-z0-9._%+\-]+@netcompany\.com$")
PASSWORD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"   # χωρίς 0/O, 1/l/I
PASSWORD_LEN = 12
MIN_PASSWORD_LEN = 10
RESEND_COOLDOWN = 60          # ελάχιστο διάστημα μεταξύ δύο emails κωδικού στον ίδιο χρήστη
MAX_FAILED_LOGINS = 10
LOCKOUT = 15 * 60
SESSION_TTL = 30 * 24 * 3600
COOKIE = "ts_session"

DisplayName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=120, pattern=r"^[^|]+$")]
NewPassword = Annotated[str, StringConstraints(min_length=MIN_PASSWORD_LEN, max_length=200)]


def admin_emails() -> set[str]:
    return {e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()}


def normalize_email(raw: str) -> str:
    email = raw.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(400, f"Χρησιμοποιήστε το εταιρικό σας email (@{EMAIL_DOMAIN}).")
    return email


# ---------- Κωδικοί ----------
def generate_password() -> str:
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(PASSWORD_LEN))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    n, r, p = 2**14, 8, 1
    dk = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=32)
    return f"scrypt${n}${r}${p}${salt.hex()}${dk.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, dk = stored.split("$")
    except ValueError:
        return False
    if algo != "scrypt":
        return False
    got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=len(dk) // 2)
    return hmac.compare_digest(got.hex(), dk)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---------- Αποστολή email ----------
PasswordMail = Literal["register", "reset", "invite"]


def deliver_password(email: str, name: str, password: str, kind: PasswordMail) -> None:
    """Στέλνει τον κωδικό: στην εγγραφή, στο «Ξέχασα τον κωδικό», ή όταν ένας admin φτιάχνει τον λογαριασμό."""
    intro, note, subject = {
        "register": ("Ο λογαριασμός σας στο Team Timesheet δημιουργήθηκε.",
                     "Η πρόσβασή σας θα ενεργοποιηθεί μόλις την εγκρίνει ένας διαχειριστής.",
                     "Ο κωδικός πρόσβασής σας στο Team Timesheet"),
        "invite": ("Ένας διαχειριστής δημιούργησε για εσάς λογαριασμό στο Team Timesheet.",
                   "Η πρόσβασή σας είναι ήδη ενεργή.",
                   "Ο λογαριασμός σας στο Team Timesheet"),
        "reset": ("Ζητήσατε νέο κωδικό πρόσβασης για το Team Timesheet.",
                  "Ο προηγούμενος κωδικός σας παύει να ισχύει μόλις συνδεθείτε με τον νέο. "
                  "Αν δεν ζητήσατε εσείς νέο κωδικό, αγνοήστε αυτό το μήνυμα και συνεχίστε με τον παλιό.",
                  "Νέος κωδικός πρόσβασης στο Team Timesheet"),
    }[kind]
    url = mailer.app_url()
    link_text = f"Η εφαρμογή: {url}\n" if url else ""
    link_html = f'<p>Η εφαρμογή: <a href="{url}">{url}</a></p>' if url else ""
    safe_name = html.escape(name)
    mailer.send_email(
        email, subject,
        f"Γεια σας {name},\n\n{intro}\n\nEmail: {email}\nΚωδικός πρόσβασης: {password}\n{link_text}\n"
        "Μπορείτε να αλλάξετε τον κωδικό από την εφαρμογή, μέσα από την επιλογή «Αλλαγή κωδικού».\n"
        f"{note}\n\nΜην προωθήσετε αυτό το μήνυμα.\n",
        f"""<p>Γεια σας {safe_name},</p>
<p>{intro}</p>
<p>Email: <b>{email}</b><br>Κωδικός πρόσβασης:</p>
<p style="font-family:Consolas,monospace;font-size:22px;letter-spacing:2px;font-weight:bold;color:#0F4C5C">{password}</p>
{link_html}
<p>Μπορείτε να αλλάξετε τον κωδικό από την εφαρμογή, μέσα από την επιλογή «Αλλαγή κωδικού».</p>
<p>{note}</p>
<p style="color:#5F6B70;font-size:12px">Μην προωθήσετε αυτό το μήνυμα.</p>""",
    )


def send_or_fail(email: str, name: str, password: str, kind: PasswordMail) -> None:
    try:
        deliver_password(email, name, password, kind)
    except Exception:
        log.exception("Αποτυχία αποστολής κωδικού στο %s", email)
        raise HTTPException(502, "Το email με τον κωδικό δεν στάλθηκε. Δοκιμάστε ξανά σε λίγο.")


# ---------- Τρέχων χρήστης ----------
def current_user(request: Request) -> Optional[dict]:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    with db.read_conn() as conn:
        row = conn.execute(
            "SELECT u.email, u.name, u.role FROM sessions s JOIN users u ON u.email = s.email "
            "WHERE s.token_hash = ? AND s.expires_at > ?",
            (_token_hash(token), time.time()),
        ).fetchone()
    return dict(row) if row else None


def require_login(request: Request) -> dict:
    u = current_user(request)
    if not u:
        raise HTTPException(401, "Συνδεθείτε για να συνεχίσετε.")
    return u


def require_member(request: Request) -> dict:
    """Για τα endpoints των δεδομένων: χρήστης με εγκεκριμένη πρόσβαση (user ή admin)."""
    u = require_login(request)
    if u["role"] not in ("user", "admin"):
        raise HTTPException(403, "Η πρόσβασή σας περιμένει έγκριση από διαχειριστή.")
    return u


def require_admin(request: Request) -> dict:
    u = require_member(request)
    if u["role"] != "admin":
        raise HTTPException(403, "Αυτή η ενέργεια επιτρέπεται μόνο σε διαχειριστές.")
    return u


LoggedIn = Annotated[dict, Depends(require_login)]
Member = Annotated[dict, Depends(require_member)]
Admin = Annotated[dict, Depends(require_admin)]

router = APIRouter()


# ---------- Εγγραφή / είσοδος ----------
class Register(BaseModel):
    email: str
    name: DisplayName


class Login(BaseModel):
    email: str
    password: str


class ForgotPassword(BaseModel):
    email: str


class ChangePassword(BaseModel):
    current: str
    new: NewPassword


@router.post("/api/auth/register")
def register(body: Register):
    email = normalize_email(body.email)
    password = generate_password()
    is_admin = email in admin_emails()
    role = "admin" if is_admin else "pending"
    stamp = db.now_stamp()
    with db.write_tx() as conn:
        if conn.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            raise HTTPException(409, "Υπάρχει ήδη λογαριασμός με αυτό το email. Συνδεθείτε, ή πατήστε «Ξέχασα τον κωδικό».")
        conn.execute(
            "INSERT INTO users(email,name,role,password_hash,reset_at,created_at,approved_by,approved_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (email, body.name, role, hash_password(password), time.time(), stamp,
             "ADMIN_EMAILS" if is_admin else "", stamp if is_admin else ""),
        )
        db.add_log(conn, body.name, "Εγγραφή χρήστη", note=f"{email} ({role})", ts=stamp)
        db.sync_team_member(conn, email, "system", ts=stamp)
        # Αν το email δεν φύγει, η εξαίρεση ακυρώνει τη συναλλαγή και ο λογαριασμός δεν δημιουργείται.
        send_or_fail(email, body.name, password, "register")
    return {"ok": True, "message": "Ο κωδικός πρόσβασης στάλθηκε στο email σας."}


@router.post("/api/auth/login")
def login(body: Login, response: Response):
    email = normalize_email(body.email)
    now = time.time()
    error = None
    token = secrets.token_urlsafe(32)
    with db.write_tx() as conn:
        user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if not user:
            error = (401, "Λάθος email ή κωδικός.")
        elif user["locked_until"] > now:
            mins = int((user["locked_until"] - now) // 60) + 1
            error = (429, f"Πολλές αποτυχημένες προσπάθειες. Δοκιμάστε ξανά σε {mins} λεπτά.")
        elif check_password(body.password, user["password_hash"]):
            conn.execute("UPDATE users SET failed_logins=0, locked_until=0, reset_hash='' WHERE email=?", (email,))
        elif user["reset_hash"] and check_password(body.password, user["reset_hash"]):
            conn.execute("UPDATE users SET password_hash=reset_hash, reset_hash='', failed_logins=0, locked_until=0 "
                         "WHERE email=?", (email,))
        else:
            failed = user["failed_logins"] + 1
            locked = now + LOCKOUT if failed >= MAX_FAILED_LOGINS else 0
            conn.execute("UPDATE users SET failed_logins=?, locked_until=? WHERE email=?",
                         (0 if locked else failed, locked, email))
            error = (401, "Λάθος email ή κωδικός.")
        if not error:
            if email in admin_emails() and user["role"] != "admin":
                stamp = db.now_stamp()
                conn.execute("UPDATE users SET role='admin', approved_by='ADMIN_EMAILS', approved_at=? WHERE email=?",
                             (stamp, email))
                db.add_log(conn, "system", "Αλλαγή πρόσβασης", note=f"{email}: {user['role']} → admin (ADMIN_EMAILS)", ts=stamp)
                db.sync_team_member(conn, email, "system", ts=stamp)
            conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
            conn.execute("INSERT INTO sessions(token_hash,email,created_at,expires_at) VALUES (?,?,?,?)",
                         (_token_hash(token), email, now, now + SESSION_TTL))
    if error:
        raise HTTPException(*error)
    response.set_cookie(COOKIE, token, max_age=SESSION_TTL, httponly=True, samesite="lax",
                        secure=os.environ.get("COOKIE_SECURE", "") == "1", path="/")
    with db.read_conn() as conn:
        return dict(conn.execute("SELECT email, name, role FROM users WHERE email=?", (email,)).fetchone())


@router.post("/api/auth/forgot")
def forgot(body: ForgotPassword):
    email = normalize_email(body.email)
    now = time.time()
    with db.write_tx() as conn:
        user = conn.execute("SELECT name, reset_at FROM users WHERE email=?", (email,)).fetchone()
        if user:
            if now - user["reset_at"] < RESEND_COOLDOWN:
                wait = int(RESEND_COOLDOWN - (now - user["reset_at"])) + 1
                raise HTTPException(429, f"Μόλις στάλθηκε κωδικός. Δοκιμάστε ξανά σε {wait} δευτερόλεπτα.")
            password = generate_password()
            conn.execute("UPDATE users SET reset_hash=?, reset_at=? WHERE email=?", (hash_password(password), now, email))
            send_or_fail(email, user["name"], password, "reset")
    # Ίδια απάντηση είτε υπάρχει λογαριασμός είτε όχι.
    return {"ok": True, "message": "Αν υπάρχει λογαριασμός με αυτό το email, στάλθηκε νέος κωδικός."}


@router.post("/api/auth/password")
def change_password(body: ChangePassword, user: LoggedIn, request: Request):
    with db.write_tx() as conn:
        row = conn.execute("SELECT password_hash FROM users WHERE email=?", (user["email"],)).fetchone()
        if not check_password(body.current, row["password_hash"]):
            raise HTTPException(400, "Ο τωρινός κωδικός δεν είναι σωστός.")
        conn.execute("UPDATE users SET password_hash=?, reset_hash='' WHERE email=?",
                     (hash_password(body.new), user["email"]))
        # Αποσυνδέει τις άλλες συσκευές· η τρέχουσα μένει συνδεδεμένη.
        conn.execute("DELETE FROM sessions WHERE email=? AND token_hash<>?",
                     (user["email"], _token_hash(request.cookies.get(COOKIE, ""))))
    return {"ok": True, "message": "Ο κωδικός σας άλλαξε."}


@router.get("/api/auth/me")
def me(user: LoggedIn):
    return user


@router.post("/api/auth/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE)
    if token:
        with db.write_tx() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash=?", (_token_hash(token),))
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


# ---------- Διαχείριση χρηστών ----------
class RoleChange(BaseModel):
    email: str
    role: Literal["pending", "user", "admin"]


class NewUser(BaseModel):
    email: str
    name: DisplayName
    role: Literal["user", "admin"] = "user"


class NotifyChange(BaseModel):
    email: str
    enabled: bool


@router.get("/api/admin/users")
def list_users(_: Admin):
    with db.read_conn() as conn:
        rows = conn.execute(
            "SELECT email, name, role, notify_missing, created_at, approved_by, approved_at FROM users "
            "ORDER BY CASE role WHEN 'pending' THEN 0 ELSE 1 END, name COLLATE NOCASE"
        ).fetchall()
    return [{**dict(r), "notify_missing": bool(r["notify_missing"])} for r in rows]


@router.post("/api/admin/users")
def create_user(body: NewUser, admin: Admin):
    """Ο admin φτιάχνει λογαριασμό· είναι εγκεκριμένος από την αρχή και ο κωδικός πηγαίνει στο email του χρήστη."""
    email = normalize_email(body.email)
    password = generate_password()
    stamp = db.now_stamp()
    with db.write_tx() as conn:
        if conn.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            raise HTTPException(409, "Υπάρχει ήδη λογαριασμός με αυτό το email.")
        conn.execute(
            "INSERT INTO users(email,name,role,password_hash,reset_at,created_at,approved_by,approved_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (email, body.name, body.role, hash_password(password), time.time(), stamp, admin["email"], stamp),
        )
        db.add_log(conn, admin["name"], "Δημιουργία χρήστη", note=f"{email} ({body.role})", ts=stamp)
        db.sync_team_member(conn, email, admin["name"], ts=stamp)
        # Αν το email δεν φύγει, η εξαίρεση ακυρώνει τη συναλλαγή και ο λογαριασμός δεν δημιουργείται.
        send_or_fail(email, body.name, password, "invite")
    return list_users(admin)


@router.post("/api/admin/users/notify")
def set_notify(body: NotifyChange, admin: Admin):
    """Ενεργοποιεί ή σβήνει την ημερήσια υπενθύμιση για ελλιπείς ώρες (app/reminders.py)."""
    email = body.email.strip().lower()
    with db.write_tx() as conn:
        user = conn.execute("SELECT notify_missing FROM users WHERE email=?", (email,)).fetchone()
        if not user:
            raise HTTPException(404, "Δεν βρέθηκε χρήστης με αυτό το email.")
        if bool(user["notify_missing"]) != body.enabled:
            conn.execute("UPDATE users SET notify_missing=? WHERE email=?", (int(body.enabled), email))
            db.add_log(conn, admin["name"], "Αλλαγή ειδοποιήσεων",
                       note=f"{email}: υπενθύμιση για ελλιπείς ώρες {'ενεργή' if body.enabled else 'ανενεργή'}")
    return list_users(admin)


@router.post("/api/admin/users/role")
def set_role(body: RoleChange, admin: Admin):
    email = body.email.strip().lower()
    if email == admin["email"]:
        raise HTTPException(400, "Δεν μπορείτε να αλλάξετε τον δικό σας ρόλο.")
    stamp = db.now_stamp()
    with db.write_tx() as conn:
        user = conn.execute("SELECT role FROM users WHERE email=?", (email,)).fetchone()
        if not user:
            raise HTTPException(404, "Δεν βρέθηκε χρήστης με αυτό το email.")
        if user["role"] != body.role:
            approved = body.role != "pending"
            conn.execute("UPDATE users SET role=?, approved_by=?, approved_at=? WHERE email=?",
                         (body.role, admin["email"] if approved else "", stamp if approved else "", email))
            db.add_log(conn, admin["name"], "Αλλαγή πρόσβασης", note=f"{email}: {user['role']} → {body.role}", ts=stamp)
            db.sync_team_member(conn, email, admin["name"], ts=stamp)
    return list_users(admin)
