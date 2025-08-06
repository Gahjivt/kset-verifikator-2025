import os
import json
import logging
import time
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Depends
from contextlib import asynccontextmanager
from pydantic import BaseModel
from google.oauth2.service_account import Credentials
import gspread
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from threading import Lock
from datetime import datetime, time as dt_time, timezone
import uuid
from fastapi.responses import RedirectResponse, HTMLResponse
import requests
import psycopg2
from psycopg2 import pool, sql
import html

load_dotenv()
logging.basicConfig(level=logging.INFO)

#env stvari
google_key_path = os.environ["GOOGLE_KEY_PATH"]
spreadsheet_id = os.environ["SPREADSHEET_ID"]
sheet_id = int(os.environ["SPREADSHEET_SHEET_ID"])
impersonation_user = os.environ.get("SPREADSHEET_USER")
google_client_id = os.environ["GOOGLE_CLIENT_ID"]
google_client_secret = os.environ["GOOGLE_CLIENT_SECRET"]
google_redirect_uri = os.environ["GOOGLE_REDIRECT_URI"]

db_host = os.environ["DB_HOST"]
db_user = os.environ["DB_USER"]
db_password = os.environ["DB_PASSWORD"]
db_database = os.environ["DB_NAME"]


# provjera sheetsa
credentials = Credentials.from_service_account_file(
    google_key_path,
    scopes=["https://www.googleapis.com/auth/spreadsheets"],
    subject=impersonation_user
)
client = gspread.authorize(credentials)

cached_rows = None
cache_timestamp = 0
last_loaded_day = None
cache_lock = Lock()

# Inicijalizacija PostgreSQL connection pool-a
db_pool = None

def init_db(): #koristio sam psycopg2, no postoje drugi library na istu foru
    global db_pool
    db_pool = psycopg2.pool.SimpleConnectionPool(1, 10, #10 mozemo povecati ako ce trebati vise konekcija, iako neznam zasto bi trebalo?
        host=db_host,
        user=db_user,
        password=db_password,
        dbname=db_database
    )
    logging.info("POSGRESRADI.")

    with db_pool.getconn() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS verification_attempts (
                    state TEXT PRIMARY KEY,
                    izvor TEXT NOT NULL,
                    email TEXT,
                    status TEXT NOT NULL,
                    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    used_at TIMESTAMP WITH TIME ZONE
                );
            """)
        conn.commit()
    db_pool.putconn(conn)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        init_db()
        refresh_cache(force=True)
    except Exception as e:
        print(f"[ERROR] Startup error: {e}")
    yield

app = FastAPI(lifespan=lifespan)

class EmailRequest(BaseModel):
    email: str

class EmailsRequest(BaseModel):
    emails: list[str]
    
class VerificationRequest(BaseModel):
    state: str
    izvor: str

def normalize(email: str) -> str:
    return email.strip().lower()

def load_rows():
    if cached_rows is None:
        raise RuntimeError("STUPCI IZ SHEETA NE RADE")
    return cached_rows

def refresh_cache(force=False):
    global cached_rows, cache_timestamp, last_loaded_day
    now = datetime.now()
    refresh_time = dt_time(6, 47)

    if force or cached_rows is None or (now.date() != last_loaded_day and now.time() >= refresh_time):
        with cache_lock:
            try:
                spreadsheet = client.open_by_key(spreadsheet_id)
                worksheet = spreadsheet.get_worksheet_by_id(sheet_id)
                rows = worksheet.get_all_records()
                cached_rows = rows
                cache_timestamp = time.time()
                last_loaded_day = now.date()
                logging.info(f"Spreadsheet ucitan {len(rows)} redaka")
            except Exception as e:
                logging.exception("error u ucitavanju redaka: ", e)
                raise

@app.post("/verify-email")
def verify_email(req: EmailRequest):
    try:
        rows = load_rows()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    search_email = normalize(req.email)

    for row in rows:
        kset_email = normalize(row.get("KSET e-pošta", "") or "")
        private_email = normalize(row.get("Privatna e-pošta", "") or "")
        if search_email in [kset_email, private_email]:
            return {
                "full_name": row.get("Ime i prezime", "N/A"),
                "section": row.get("Matična sekcija", "N/A"),
                "status_clanstva": row.get("Trenutna vrsta članstva", "N/A"),
                "kset_email": kset_email,
                "private_email": private_email,
            }

    raise HTTPException(status_code=404, detail="Email nije pronađen.")

@app.post("/verify-emails")
def verify_emails_batch(req: EmailsRequest):
    try:
        rows = load_rows()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    
    lookup_data = {}
    for row in rows:
        kset_email = normalize(row.get("KSET e-pošta", "") or "")
        private_email = normalize(row.get("Privatna e-pošta", "") or "")
        data = {
            "full_name": row.get("Ime i prezime", "N/A"),
            "section": row.get("Matična sekcija", "N/A"),
            "status_clanstva": row.get("Trenutna vrsta članstva", "N/A"),
            "kset_email": kset_email,
            "private_email": private_email,
        }
        if kset_email:
            lookup_data[kset_email] = data
        if private_email:
            lookup_data[private_email] = data
            
    response_data = {}
    for email in req.emails:
        normalized_email = normalize(email)
        if normalized_email in lookup_data:
            response_data[email] = lookup_data[normalized_email]
    
    return response_data

@app.post("/generate-oauth-link")
def generate_oauth_link_simplified(req: VerificationRequest):
    conn = None
    try:
        conn = db_pool.getconn()
        cursor = conn.cursor()
        
        insert_query = """
            INSERT INTO verification_attempts (state, izvor, status, created_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (state) DO UPDATE SET izvor = EXCLUDED.izvor, status = EXCLUDED.status, created_at = EXCLUDED.created_at, used_at = NULL;
        """
        cursor.execute(insert_query, (
            req.state,
            req.izvor,
            "pending",
            datetime.now(timezone.utc)
        ))
        conn.commit()
        logging.info(f"Novi pokusaj verifikacije zabiljezen za state: {req.state}")

    except psycopg2.Error as e:
        logging.error(f"Greška prilikom zapisivanja u PostgreSQL bazu: {e}")
        if conn: conn.rollback()
        raise HTTPException(status_code=500, detail="Greška baze podataka")
    finally:
        if conn: db_pool.putconn(conn)
        
    oauth_url = (
        "https://accounts.google.com/o/oauth2/v2/auth"
        "?response_type=code"
        f"&client_id={google_client_id}"
        f"&redirect_uri={google_redirect_uri}"
        "&scope=openid%20email"
        f"&state={req.state}"
        "&prompt=select_account"
    )

    return {"oauth_url": oauth_url, "state": req.state}

@app.get("/oauth/status")
def oauth_status(state: str):
    conn = None
    try:
        conn = db_pool.getconn()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT email, status FROM verification_attempts WHERE state = %s",
            (state,)
        )
        row = cursor.fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail="Vec iskoristen link")

        email, status = row
        
        if status == "success":
            return {
                "status": "success",
                "private_email": email
            }
        elif status == "pending":
            return {"status": "pending"}
        else:
            return {"status": "fail", "reason": "Verifikacija nije uspjela"}
            
    except psycopg2.Error as e:
        logging.error(f"Greška prilikom provjere statusa u PostgreSQL: {e}")
        raise HTTPException(status_code=500, detail="Greška baze podataka")
    finally:
        if conn: db_pool.putconn(conn)

@app.get("/oauth/callback", response_class=HTMLResponse)
def oauth_callback(code: str, state: str):
    conn = None
    try:
        conn = db_pool.getconn()
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT created_at, status FROM verification_attempts WHERE state = %s FOR UPDATE",
            (state,)
        )
        row = cursor.fetchone()

        if not row:
            return HTMLResponse(content="<h1>Neispravan state</h1>", status_code=400)
            
        created_at, status = row
        
        if status != "pending":
            return HTMLResponse(content="<h1>OAuth link je već iskorišten</h1>", status_code=400)
            
        if (datetime.now(timezone.utc) - created_at).total_seconds() > 5 * 60:
            cursor.execute("UPDATE verification_attempts SET status = %s, used_at = %s WHERE state = %s",
                           ("expired", datetime.now(timezone.utc), state))
            conn.commit()
            return HTMLResponse(content="<h1>OAuth link je istekao</h1>", status_code=400)
            
        google_email = verify_email_with_google(code)
        
        req = EmailRequest(email=google_email)
        sheet_data = verify_email(req) # Ovo će baciti HTTPException ako email nije u bazi
        
        # Ažuriranje statusa i ostalih podataka u bazi
        update_query = """
            UPDATE verification_attempts 
            SET email = %s, status = %s, used_at = %s 
            WHERE state = %s
        """
        cursor.execute(update_query, (
            google_email,
            "success",
            datetime.now(timezone.utc),
            state
        ))
        conn.commit()
        
        html_content = f""" 
        <html>
        <head><title>Verifikacija uspješna</title></head>
        <body>
            <h1>Verifikacija uspješna</h1>
            <p>Ime i prezime: {html.escape(sheet_data.get('full_name', 'N/A'))}</p>
            <p>Matična sekcija: {html.escape(sheet_data.get('section', 'N/A'))}</p>
            <p>Email KSET: {html.escape(sheet_data.get('kset_email', 'N/A'))}</p>
            <p>Privatni email: {html.escape(sheet_data.get('private_email', 'N/A'))}</p>
            <p>Status članstva: {html.escape(sheet_data.get('status_clanstva', 'N/A'))}</p>
        </body>
        </html>
        """
        return HTMLResponse(content=html_content, status_code=200)

    except HTTPException as e:
        # Ažuriranje statusa na "fail" ako verifikacija nije uspjela
        if conn:
            cursor.execute("UPDATE verification_attempts SET status = %s, used_at = %s WHERE state = %s",
                           ("fail", datetime.now(timezone.utc), state))
            conn.commit()
        return HTMLResponse(content=f"""
            <h1>Verifikacija nije uspjela</h1>
            <p>Email se ne nalazi u bazi. Pokušajte se registrirati s drugim emailom.</p>
        """, status_code=403)
    except Exception as e:
        logging.error(f"Neočekivana greška u callbacku: {e}")
        if conn:
            cursor.execute("UPDATE verification_attempts SET status = %s, used_at = %s WHERE state = %s",
                           ("fail", datetime.now(timezone.utc), state))
            conn.commit()
        return HTMLResponse(content="<h1>Došlo je do neočekivane greške</h1>", status_code=500)
    finally:
        if conn:
            db_pool.putconn(conn)


def verify_email_with_google(code: str) -> str:
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    redirect_uri = os.getenv("GOOGLE_REDIRECT_URI")

    token_resp = requests.post("https://oauth2.googleapis.com/token", data={
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    })

    if token_resp.status_code != 200:
        logging.error("Request failed pri pristupu oauth: %s", token_resp.text)
        raise HTTPException(status_code=400, detail="Greška kod verifikacije (token)")

    access_token = token_resp.json().get("access_token")

    userinfo_resp = requests.get(
        "https://www.googleapis.com/oauth2/v3/userinfo",
        headers={"Authorization": f"Bearer {access_token}"}
    )

    if userinfo_resp.status_code != 200:
        logging.error("Userinfo request failed: %s", userinfo_resp.text)
        raise HTTPException(status_code=400, detail="Greška kod dohvaćanja korisnika")

    return userinfo_resp.json().get("email")

@app.post("/refresh-cache")
def api_refresh_cache():
    try:
        refresh_cache(force=True)
        return {"status": "Spreadsheet refreshed"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Greška: {e}")

@app.post("/clear-cache")
def clear_cache():
    global cached_rows, cache_timestamp, last_loaded_day
    cached_rows = None
    cache_timestamp = 0
    last_loaded_day = None
    return {"status": "Cache cleared"}
