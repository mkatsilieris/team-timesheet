# Team Timesheet — σημειώσεις για development

- Backend: FastAPI + SQLite (`app/`). Οι εγγραφές γίνονται μία τη φορά μέσω `db.write_tx()`· κάθε εγγραφή αυξάνει το `meta.version`.
- Κάθε αλλαγή δεδομένων περνά από το `POST /api/changes` και γράφει γραμμή στον πίνακα `log`. Ο `log` είναι append-only· κανένα endpoint δεν πρέπει να τον σβήνει ή να τον αλλάζει.
- Το UI είναι ένα αρχείο, το `app/static/index.html`, σε vanilla JS χωρίς build. Οι αλλαγές μένουν στο `pending` και στέλνονται με debounce 1s· το UI κάνει polling στο `/api/version` κάθε 30s.
- Τα ονόματα ατόμων και projects είναι τα κλειδιά των καταχωρίσεων και δεν περιέχουν `|`. Η μετονομασία δεν υποστηρίζεται.
- Τα κείμενα του UI είναι στα ελληνικά, σε πλήρεις προτάσεις.
- Tests: `pytest` (χρησιμοποιούν προσωρινό `DATA_DIR`). Τρέξτε τα πριν από κάθε commit.
- Docker: `docker compose up -d --build`, πόρτα 9999, volume `./data`.
