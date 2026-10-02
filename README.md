# Team Timesheet

Μικρή web εφαρμογή για την καταγραφή των ωρών που δουλεύει κάθε μέλος της ομάδας ανά ημέρα και project. Τρέχει σε ένα Docker container και σερβίρεται στην πόρτα 9999.

## Εκκίνηση στο VM

```bash
docker compose up -d --build
```

Η εφαρμογή ανοίγει στο `http://<ip-του-vm>:9999`. Τα δεδομένα αποθηκεύονται στο `./data/timesheet.db` (SQLite). Ο φάκελος είναι volume του container, οπότε τα δεδομένα δεν χάνονται όταν ξαναχτίζεται το image.

Χρήσιμες εντολές:

```bash
docker compose logs -f            # logs
docker compose up -d --build      # νέα έκδοση μετά από αλλαγές στον κώδικα
docker compose down               # σταμάτημα (τα δεδομένα μένουν στο ./data)
```

Αν το VM έχει firewall, πρέπει να ανοίξει η πόρτα 9999 (π.χ. `sudo ufw allow 9999/tcp`, ή NSG rule αν το VM είναι στο Azure).

## Μεταφορά δεδομένων από την έκδοση με το Excel στο OneDrive

Αντιγράψτε το `Timesheet_DB.xlsx` στον φάκελο `data/` **πριν** από την πρώτη εκκίνηση. Όταν η βάση είναι άδεια, η εφαρμογή εισάγει από αυτό το αρχείο την ομάδα, τα projects, τις ώρες και το ιστορικό. Αν το αρχείο δεν υπάρχει, η εφαρμογή ξεκινά με την ομάδα και τα projects που ορίζονται στο `app/db.py`.

Αν η βάση έχει ήδη δημιουργηθεί, σβήστε το `data/timesheet.db` και ξανατρέξτε το container για να γίνει η εισαγωγή.

## Τι κάνει

- **Ώρες:** επιλέγετε άτομο και μήνα. Οι ημερομηνίες είναι γραμμές και τα projects στήλες. Η εφαρμογή αποθηκεύει κάθε αλλαγή αυτόματα μετά από ένα δευτερόλεπτο.
- **Σύνοψη:** δείχνει ώρες ή man-days ανά άτομο και project για όποιο διάστημα επιλέξετε.
- **Ομάδα & Projects:** προσθέτετε άτομα και projects, και απενεργοποιείτε όσα δεν χρειάζονται πια.
- **Ιστορικό αλλαγών:** η εφαρμογή γράφει κάθε αλλαγή μαζί με το ποιος την έκανε, πότε, και την παλιά και τη νέα τιμή. Το ιστορικό δεν σβήνεται.
- **Λήψη Excel:** κατεβάζει ένα αρχείο με σύνοψη, ένα sheet ανά άτομο (ημερομηνίες × projects), τα πρωτογενή δεδομένα, τα μηνιαία σύνολα και το ιστορικό.

Αν δύο χρήστες αλλάξουν την ίδια τιμή σχεδόν ταυτόχρονα, η εφαρμογή κρατά τη νεότερη αλλαγή και σημειώνει τη σύγκρουση στο ιστορικό.

## API

| Method | Path | Περιγραφή |
|---|---|---|
| GET | `/api/state` | Ομάδα, projects, όλες οι καταχωρίσεις και το version |
| GET | `/api/version` | Μετρητής που αυξάνει σε κάθε εγγραφή (το UI κάνει polling κάθε 30″) |
| POST | `/api/changes` | Δέσμη αλλαγών `{user, entries[], team[], projects[]}` |
| GET | `/api/log?limit=` | Ιστορικό, τα νεότερα πρώτα |
| GET | `/api/export?from=YYYY-MM-DD&to=YYYY-MM-DD` | Excel για download |
| GET | `/api/backup` | Αντίγραφο της SQLite βάσης |
| GET | `/healthz` | Health check |

## Τοπικό development

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows (Linux/macOS: source .venv/bin/activate)
pip install -r requirements-dev.txt
set DATA_DIR=./data               # Windows cmd (PowerShell: $env:DATA_DIR="./data")
uvicorn app.main:app --reload --port 9999
pytest
```

## Δομή

```
app/main.py             FastAPI: API και σερβίρισμα του UI
app/db.py               Σχήμα SQLite, αρχικά δεδομένα, συναλλαγές εγγραφής
app/export.py           Παραγωγή του Excel για download (openpyxl)
app/importer.py         Εισαγωγή του Timesheet_DB.xlsx της προηγούμενης έκδοσης
app/static/index.html   Ολόκληρο το UI (vanilla JS, χωρίς build step)
tests/test_api.py       Tests του API
```

## Περιορισμοί

- Η εφαρμογή δεν έχει login. Ο χρήστης γράφει το όνομά του στο πεδίο «Είμαι» και ο browser το θυμάται. Το ιστορικό καταγράφει αυτό το όνομα. Για πραγματική ταυτοποίηση χρειάζεται Entra ID, π.χ. με oauth2-proxy μπροστά από το container.
- Η εφαρμογή σερβίρεται σε HTTP. Αν το VM είναι προσβάσιμο έξω από το εσωτερικό δίκτυο, χρειάζεται reverse proxy με HTTPS.
- Η εφαρμογή τρέχει με έναν uvicorn worker, γιατί η SQLite δέχεται έναν writer τη φορά. Για μια ομάδα αυτό αρκεί.
- Κάντε backup τον φάκελο `data/` ή καλείτε περιοδικά το `/api/backup`.
