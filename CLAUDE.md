# Team Timesheet — σημειώσεις για development

- Backend: FastAPI + SQLite (`app/`). Οι εγγραφές γίνονται μία τη φορά μέσω `db.write_tx()`· κάθε εγγραφή αυξάνει το `meta.version`.
- Κάθε αλλαγή δεδομένων περνά από το `POST /api/changes` και γράφει γραμμή στον πίνακα `log`. Ο `log` είναι append-only· κανένα endpoint δεν πρέπει να τον σβήνει ή να τον αλλάζει.
- Το UI είναι ένα αρχείο, το `app/static/index.html`, σε vanilla JS χωρίς build. Οι αλλαγές μένουν στο `pending` και στέλνονται με debounce 1s· το UI κάνει polling στο `/api/version` κάθε 30s.
- Auth στο `app/auth.py`: εγγραφή μόνο με `@netcompany.com`, μόνιμος κωδικός στο email (scrypt hash στη βάση), session cookie `ts_session`, ρόλοι `pending | user | admin`. Τα endpoints δεδομένων παίρνουν `Member`, τα admin `Admin`. Οι αλλαγές ρόλων γράφονται στο `log`. Το ιστορικό γράφει το όνομα του συνδεδεμένου χρήστη, όχι το `user` του request.
- Τα άτομα (`team`) είναι οι εγκεκριμένοι χρήστες, συνδεδεμένοι με `team.email`. Δημιουργούνται και (απ)ενεργοποιούνται μόνο από το `db.sync_team_member()` όταν αλλάζει ο ρόλος· το `/api/changes` αλλάζει μόνο Grade/Type/κατάσταση και μόνο για admin. Το `db.migrate()` έσβησε μία φορά τις ώρες και τα άτομα χωρίς λογαριασμό (σημαία `meta.team_from_users`).
- Τα ονόματα ατόμων και projects είναι τα κλειδιά των καταχωρίσεων και δεν περιέχουν `|`. Η μετονομασία δεν υποστηρίζεται.
- Τα κείμενα του UI είναι στα ελληνικά, σε πλήρεις προτάσεις.
- Tests: `pytest` (χρησιμοποιούν προσωρινό `DATA_DIR`). Τρέξτε τα πριν από κάθε commit.
- Docker: `docker compose up -d --build`. Το Caddy σερβίρει HTTPS στην 9443 (η 9999 κάνει redirect)· η εφαρμογή δεν έχει πόρτα στον host. Volume `./data` για τη βάση, `caddy_data` για την τοπική CA.
