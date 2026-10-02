---
name: deploy
description: Deploy team-timesheet to the dev VM 10.240.138.191 (user mkatsil). The VM has no internet, so the image is built locally, shipped with docker save + scp, and started with --no-build. Use when the user says deploy, "κάνε deploy", "ανέβασέ το στο VM", "πέρασέ το στον server", or asks what version runs there.
---

# Deploy στο VM

Στόχος: `mkatsil@10.240.138.191`, φάκελος `~/team-timesheet`. Η εφαρμογή ανοίγει στο `https://10.240.138.191:9443` μέσω Caddy· η 9999 κάνει redirect.
Το VM **δεν έχει internet** (δεν κάνει resolve το github.com ούτε το PyPI): δεν γίνεται `git clone` ούτε `docker compose build` εκεί.

## Κανόνες
- Τρέχε `ssh`/`scp` από **PowerShell** (Windows OpenSSH). Το key έχει passphrase και φορτώνεται από το Windows ssh-agent· το ssh του Git Bash δεν το βλέπει.
- Πάντα `-o BatchMode=yes`. Αν πάρεις `Permission denied`, σταμάτα και ζήτα από τον χρήστη `ssh-add $HOME\.ssh\id_ed25519`. Μην ζητήσεις password ή passphrase.
- **Μην αγγίξεις το `~/team-timesheet/data/`.** Εκεί ζει η παραγωγική `timesheet.db`. Το archive περιέχει μόνο `data/.gitkeep`.
- **Μην αγγίξεις το `~/team-timesheet/.env`.** Έχει τις ρυθμίσεις production (ADMIN_EMAILS, SMTP). Το archive δεν το περιέχει. Αν λείπει, σταμάτα και ρώτα: χωρίς αυτό δεν στέλνονται emails και δεν υπάρχει admin.
- Πάρε backup της βάσης πριν από κάθε restart.
- Κάνε deploy μόνο commit που υπάρχει (το archive βγαίνει από `HEAD`). Αν το working tree έχει αλλαγές, ρώτα πρώτα.

## Βήματα

Ορίσε `$S` ως τον φάκελο scratchpad της συνεδρίας και `$H = "mkatsil@10.240.138.191"`.

1. **Tests** (δεν υπάρχει τοπική Python):
   ```powershell
   docker run --rm -v "${PWD}:/src" -w /src python:3.12-slim sh -c "pip install -q --root-user-action=ignore -r requirements-dev.txt -r requirements.txt && python -m pytest -q -p no:cacheprovider"
   ```
   Αν αποτύχουν, σταμάτα.

2. **Build και πακετάρισμα** (το Docker Desktop πρέπει να τρέχει):
   ```powershell
   docker compose build
   git archive --format=tar.gz --prefix=team-timesheet/ -o "$S\team-timesheet.tar.gz" HEAD
   docker pull caddy:2-alpine
   docker save -o "$S\team-timesheet-image.tar" team-timesheet:latest caddy:2-alpine
   ```
   Το image του Caddy μπαίνει στο ίδιο tar, γιατί το VM δεν μπορεί να το κατεβάσει.

3. **Έλεγχος πριν πειράξεις κάτι στο VM**:
   ```powershell
   ssh -o BatchMode=yes $H "test -w ~/team-timesheet && echo writable || echo NOT-writable; docker ps --filter name=team-timesheet --format '{{.Image}} {{.Status}}'"
   ```
   Αν βγει `NOT-writable`, σταμάτα και δώσε στον χρήστη να τρέξει ως root:
   `find /home/mkatsil/team-timesheet -path '*/data' -prune -o -exec chown mkatsil:mkatsil {} +`

4. **Μεταφορά**:
   ```powershell
   scp -o BatchMode=yes "$S\team-timesheet.tar.gz" "$S\team-timesheet-image.tar" "${H}:~/"
   ```

5. **Backup βάσης, φόρτωση, restart**:
   ```powershell
   ssh -o BatchMode=yes $H @'
   set -e
   ts=$(date +%Y%m%d-%H%M%S)
   docker exec team-timesheet python -c "import sqlite3; s=sqlite3.connect('/data/timesheet.db'); d=sqlite3.connect('/data/backup-$ts.db'); s.backup(d); d.close()" || echo "no running container, skipped backup"
   tar -xzf ~/team-timesheet.tar.gz -C ~
   docker load -i ~/team-timesheet-image.tar
   cd ~/team-timesheet && docker compose up -d --no-build
   rm ~/team-timesheet.tar.gz ~/team-timesheet-image.tar
   '@
   ```

6. **Επαλήθευση**:
   ```powershell
   ssh -o BatchMode=yes $H "sleep 5; docker compose -f ~/team-timesheet/docker-compose.yml ps; curl -sk -o /dev/null -w 'https healthz %{http_code}\n' https://localhost:9443/healthz; curl -s -o /dev/null -w 'http 9999 -> %{http_code} %{redirect_url}\n' http://localhost:9999/; docker image inspect team-timesheet:latest --format '{{.Id}}'"
   ```
   Περιμένεις `https healthz 200` και `http 9999 -> 301 https://...:9443/`.
   Σύγκρινε το image ID με το τοπικό `docker image inspect team-timesheet:latest --format '{{.Id}}'`. Ανέφερε το commit που ανέβηκε, το όνομα του backup και το αποτέλεσμα του `/healthz`.
