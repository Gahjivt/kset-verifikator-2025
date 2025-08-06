# Discord Verifikator Backend

Ovo je backend servis za Discord verifikacijski sustav. Omogućuje provjeru korisničkih email adresa na temelju podataka iz Google Sheeta te autentikaciju putem Google OAuth-a. Također koristi PostgreSQL za praćenje pokušaja verifikacije.

Kod se pokrece preko nardbe:
python -m uvicorn verifikator:app --reload
---

## Značajke

- FastAPI REST backend
- Učitavanje podataka iz Google Sheeta (gspread + service account)
- OAuth verifikacija putem Google računa
- Provjera pojedinačnih i višestrukih email adresa
- Redis-style cache s dnevnim osvježavanjem
- PostgreSQL baza za praćenje verifikacijskih pokušaja

---

## Ovisnosti

- `fastapi`
- `uvicorn`
- `python-dotenv`
- `google-auth`
- `gspread`
- `psycopg2`
- `requests`
- `pydantic`

Instaliraj sve ovisnosti:

```bash
pip install -r requirements.txt
```

---

## Konfiguracija (`.env`)

Postavi sljedeće varijable u `.env` datoteku:

```env
# Google service account
GOOGLE_KEY_PATH=./service_account.json
SPREADSHEET_ID=...
SPREADSHEET_SHEET_ID=0
SPREADSHEET_USER=ime@kset.hr

# OAuth
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
GOOGLE_REDIRECT_URI=http://localhost:8000/oauth/callback

# PostgreSQL baza
DB_HOST=localhost
DB_USER=korisnik
DB_PASSWORD=lozinka
DB_NAME=verifikacija
```

---

## Arhitektura

- `GET /oauth/callback`: Prima `code` i `state`, dohvaća email od Googlea i validira ga protiv Google Sheeta
- `POST /verify-email`: Prima jedan email i traži ga u sheetu
- `POST /verify-emails`: Prima listu emailova i vraća info za one koji su pronađeni
- `POST /generate-oauth-link`: Generira OAuth URL s unikatnim `state`
- `GET /oauth/status`: Provjerava status verifikacije na temelju `state`
- `POST /refresh-cache`: Ručno osvježava cache iz Google Sheeta
- `POST /clear-cache`: Briše cache (korisno za testiranje)

---

## Pokretanje lokalno

```bash
uvicorn app:app --reload
```

---

## Baza podataka

Koristi se `verification_attempts` tablica:

```sql
CREATE TABLE verification_attempts (
    state TEXT PRIMARY KEY,
    izvor TEXT NOT NULL,
    email TEXT,
    status TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    used_at TIMESTAMP WITH TIME ZONE
);
```

Statusi mogu biti:
- `pending` — kreiran pokušaj, čeka na korisnika
- `success` — email je uspješno verificiran
- `fail` — OAuth uspješan, ali email nije pronađen
- `expired` — link istekao (više od 5 min)

---

## OAuth logika

1. Discord bot traži `/generate-oauth-link` s unikatnim `state`
2. Korisnik klikne na link, prolazi kroz Google OAuth
3. Nakon `callback`, backend dohvaća email korisnika
4. Ako se email nalazi u Google Sheet-u, veza se označava kao `success`
5. `/oauth/status` endpoint može se koristiti za polling s frontenda

---

## Napomene

- Verifikacija se temelji na kolumnama: `KSET e-pošta`, `Privatna e-pošta`, `Ime i prezime`, `Matična sekcija`, `Trenutna vrsta članstva`
- Cache se automatski osvježava jednom dnevno (nakon 06:47)
- Ako je email pronađen u sheetu, prikazuje se korisnički info

---

## Debug

Ako želiš ručno osvježiti podatke iz Sheeta:

```bash
curl -X POST http://localhost:8000/refresh-cache
```

Ako želiš očistiti cache:

```bash
curl -X POST http://localhost:8000/clear-cache
```

---
