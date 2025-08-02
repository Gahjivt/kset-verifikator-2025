import os
import json
import logging
import time
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from contextlib import asynccontextmanager
from pydantic import BaseModel
from google.oauth2.service_account import Credentials
import gspread
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from threading import Lock
from datetime import datetime, time as dt_time
import uuid
from fastapi.responses import RedirectResponse
import requests
from fastapi.responses import HTMLResponse

load_dotenv()
logging.basicConfig(level=logging.INFO)

# .env
google_key_path = os.environ["GOOGLE_KEY_PATH"]
spreadsheet_id = os.environ["SPREADSHEET_ID"]
sheet_id = int(os.environ["SPREADSHEET_SHEET_ID"])
impersonation_user = os.environ.get("SPREADSHEET_USER")

# Autentikacija
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


def cleanup_oauth_states(expire_seconds=600):
    now = time.time()
    to_delete = [state for state, data in oauth_states.items() if now - data.get("created_at", 0) > expire_seconds or data.get("used", False)]
    for state in to_delete:
        del oauth_states[state]
    if to_delete:
        print(f"[INFO]Obrišeno {len(to_delete)} starih ili iskorištenih oauth_state-ova")


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        refresh_cache(force=True)
        cleanup_oauth_states()
    except Exception as e:
        print(f"[ERROR] Startup error: {e}")
    yield


app = FastAPI(lifespan=lifespan)

class EmailRequest(BaseModel):
    email: str

def normalize(email: str) -> str: #zbog razmaka na kraju forme
    return email.strip().lower()

def send_email(to_email):
    subject = "Verifikacija uspješna"
    body = f"""
Pozdrav Korisnice,

Vaša adresa je verificirana.

Lijepi pozdravi,
KSET Bot (discord)
"""
    smtp_host = os.environ.get("SMTP_HOST")
    smtp_port = int(os.environ.get("SMTP_PORT", 587))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_pass = os.environ.get("SMTP_PASS")

    if not smtp_host or not smtp_user or not smtp_pass:
        print("[INGO] SMTP varijable nisu postavljene, preskačem slanje maila.")
        return

    msg = MIMEMultipart()
    msg["From"] = smtp_user
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        logging.info(f"Email poslan na: {to_email}")
    except Exception as e:
        logging.error(f"Greška pri slanju emaila: {e}")

# Dohvaćanje cachea
def load_rows():
    if cached_rows is None:
        raise RuntimeError("[ERROR]Cache nije učitan.")
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
                logging.exception("Nemoze refreshati podatke u refresh cache funkciji:")
                raise

# Provjera maila u inicijalnom koraku
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

# state povezuje sa email za oath
oauth_states = {}

@app.post("/generate-oauth-link")
def generate_oauth_link(req: EmailRequest):

    cleanup_oauth_states()

    try:
        user_data = verify_email(req)
    except HTTPException as e:
        raise e

    state = str(uuid.uuid4())
    user_data["verified"] = False
    user_data["created_at"] = time.time()
    user_data["used"] = False
    oauth_states[state] = user_data

    oauth_url = (
        "https://accounts.google.com/o/oauth2/v2/auth"
        "?response_type=code"
        f"&client_id={os.getenv('GOOGLE_CLIENT_ID')}"
        f"&redirect_uri={os.getenv('GOOGLE_REDIRECT_URI')}"
        "&scope=openid%20email"
        f"&state={state}"
        "&prompt=select_account"
    )

    return {"oauth_url": oauth_url, "state": state}


@app.get("/oauth/status")
def oauth_status(state: str):
    user_data = oauth_states.get(state)
    if not user_data:
        raise HTTPException(status_code=404, detail="Nepoznat ili već iskorišten state")

    if user_data.get("used", False):
        if user_data.get("verified", False):
            return {
                "status": "success",
                **{k: user_data[k] for k in ["full_name", "section", "status_clanstva", "kset_email", "private_email"]}
            }
        else:
            return {"status": "fail", "reason": "Verifikacija nije uspjela ili je link nevažeći"}

    return {"status": "pending"}


@app.get("/oauth/callback", response_class=HTMLResponse)
def oauth_callback(code: str, state: str):
    if state not in oauth_states:
        return HTMLResponse(content="<h1>Neispravan state</h1>", status_code=400)

    user_data = oauth_states[state]

    if user_data.get("used", False):
        return HTMLResponse(content="<h1>OAuth link je već iskorišten</h1>", status_code=400)

    if time.time() - user_data.get("created_at", 0) > 5 * 60:
        del oauth_states[state]
        return HTMLResponse(content="<h1>OAuth link je istekao</h1>", status_code=400)

    verified_email = verify_email_with_google(code)
    valid_emails = [normalize(user_data["kset_email"]), normalize(user_data["private_email"])]

    if normalize(verified_email) not in valid_emails:
        user_data["used"] = True
        return HTMLResponse(content="""
            <h1>Verifikacija nije uspjela</h1>
            <p>Email adrese se ne podudaraju.</p>
        """, status_code=403)

    user_data["verified"] = True
    user_data["used"] = True
    #TU CEMO DODATI KSET LOGOTIP
    html_content = f""" 
    <html>
    <head><title>Verifikacija uspješna</title></head>
    <body>
        <h1>Verifikacija uspješna</h1>
        <p>Ime i prezime: {user_data['full_name']}</p>
        <p>Matična sekcija: {user_data['section']}</p>
        <p>Email KSET: {user_data['kset_email']}</p>
        <p>Privatni email: {user_data['private_email']}</p>
        <p>Status članstva: {user_data['status_clanstva']}</p>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content, status_code=200)



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

