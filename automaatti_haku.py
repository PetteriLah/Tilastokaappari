import requests
import psycopg2
import subprocess
import os
import time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

# Asetukset
DATABASE_URL = os.environ.get('DATABASE_URL')
FIXED_ORGANIZATION = "Noormarkun Nopsa"
MAX_RETRIES = 3
RETRY_DELAY = 5  # sekuntia

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def log_message(message, level="INFO"):
    """Yksinkertainen lokitusfunktio"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{level}] {message}")

def is_already_processed(event_id):
    """Tarkistaa onko kilpailu jo olemassa tietokannassa"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Tarkista onko kilpailu jo tietokannassa
        cursor.execute("SELECT kilpailu_id FROM Kilpailut WHERE kilpailu_id = %s", (event_id,))
        
        result = cursor.fetchone()
        return result is not None
    except psycopg2.Error as e:
        log_message(f"Tietokantavirhe tapahtuman {event_id} tarkistuksessa: {str(e)}", "ERROR")
        return False
    finally:
        if 'conn' in locals():
            conn.close()

def update_last_updated(event_id):
    """Päivittää kilpailun viimeisimmän päivitysajan"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE Kilpailut 
            SET last_updated = CURRENT_TIMESTAMP 
            WHERE kilpailu_id = %s
        """, (event_id,))
        
        conn.commit()
        log_message(f"Päivitetty last_updated kilpailulle {event_id}", "DEBUG")
    except psycopg2.Error as e:
        log_message(f"Virhe last_updated päivityksessä: {str(e)}", "ERROR")
    finally:
        if 'conn' in locals():
            conn.close()

def fetch_events():
    """Hakee tapahtumat rajapinnasta"""
    try:
        log_message("Haetaan tapahtumia rajapinnasta...")
        response = requests.get("https://cached-public-api.tuloslista.com/live/v1/competition", timeout=15)
        response.raise_for_status()
        events = response.json()
        log_message(f"Haettu {len(events)} tapahtumaa rajapinnasta")
        return events
    except requests.RequestException as e:
        log_message(f"Virhe tapahtumien haussa rajapinnasta: {str(e)}", "ERROR")
        return []
    except Exception as e:
        log_message(f"Odottamaton virhe tapahtumien haussa: {str(e)}", "ERROR")
        return []

def is_valid_date(event_date):
    """Tarkistaa että tapahtuma on menneisyydessä (max eilinen)"""
    try:
        event_date = datetime.fromisoformat(event_date.replace("Z", "+00:00"))
        yesterday = datetime.now() - timedelta(days=1)
        valid = event_date.date() <= yesterday.date()
        
        if not valid:
            log_message(f"Tapahtuma {event_date} on tulevaisuudessa, ohitetaan", "DEBUG")
        
        return valid
    except ValueError as e:
        log_message(f"Virheellinen päivämäärä {event_date}: {str(e)}", "ERROR")
        return False

def run_tulosten_haku(event_id):
    """Suorittaa tulosten haun ja palauttaa onnistuiko"""
    try:
        if not os.path.exists("tulosten_haku.py"):
            raise FileNotFoundError("tulosten_haku.py -tiedostoa ei löydy")
        
        result = subprocess.run(
            ["python", "tulosten_haku.py", "--id", str(event_id), "--seura", FIXED_ORGANIZATION],
            check=True,
            text=True,
            capture_output=True,
            timeout=300
        )
        
        if "Tiedot tallennettu" in result.stdout:
            log_message(f"tulosten_haku.py suoritettu onnistuneesti tapahtumalle {event_id}")
            return True
        else:
            log_message(f"tulosten_haku.py suoritus epäonnistui (ei tuloksia) tapahtumalle {event_id}", "WARNING")
            return False
            
    except subprocess.CalledProcessError as e:
        log_message(f"tulosten_haku.py epäonnistui tapahtumalle {event_id}: {e.stderr}", "ERROR")
        return False
    except Exception as e:
        log_message(f"Odottamaton virhe tulosten haussa tapahtumalle {event_id}: {str(e)}", "ERROR")
        return False

def run_ikalaskuri():
    """Suorittaa ikälaskurin"""
    try:
        if not os.path.exists("ikalaskuri.py"):
            log_message("ikalaskuri.py -tiedostoa ei löydy", "WARNING")
            return False
        
        log_message("Ajetaan ikälaskuri.py...")
        result = subprocess.run(
            ["python", "ikalaskuri.py"],
            check=True,
            text=True,
            capture_output=True,
            timeout=60
        )
        
        if result.returncode == 0:
            log_message("ikalaskuri.py suoritettu onnistuneesti")
            return True
        else:
            log_message(f"ikalaskuri.py epäonnistui: {result.stderr}", "ERROR")
            return False
            
    except subprocess.CalledProcessError as e:
        log_message(f"ikalaskuri.py epäonnistui: {e.stderr}", "ERROR")
        return False
    except Exception as e:
        log_message(f"Odottamaton virhe ikälaskurin suorituksessa: {str(e)}", "ERROR")
        return False

def process_event(event):
    """Käsittelee yksittäisen tapahtuman"""
    event_id = event["Id"]
    event_name = event["Name"]
    
    # 1. Tarkista päivämäärä
    if not is_valid_date(event["Date"]):
        log_message(f"Tapahtuma {event_id} ({event_name}) ohitettu - tulevaisuudessa", "DEBUG")
        return
    
    # 2. Tarkista onko kilpailu JO OLEMASSA tietokannassa
    if is_already_processed(event_id):
        log_message(f"Tapahtuma {event_id} ({event_name}) ohitettu - kilpailu on jo tietokannassa", "DEBUG")
        return
    
    # 3. Suorita tulosten haku
    success = False
    error_msg = None
    
    for attempt in range(MAX_RETRIES):
        try:
            log_message(f"Käsitellään tapahtumaa {event_id} ({event_name}), yritys {attempt + 1}/{MAX_RETRIES}")
            
            success = run_tulosten_haku(event_id)
            
            if success:
                # Päivitä last_updated onnistuneelle käsittelylle
                update_last_updated(event_id)
                break
            else:
                error_msg = f"tulosten_haku.py ei palauttanut tuloksia (yritys {attempt + 1})"
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY)
        except Exception as e:
            error_msg = str(e)
            log_message(f"Virhe tapahtuman {event_id} käsittelyssä (yritys {attempt + 1}): {error_msg}", "ERROR")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY)
    
    if success:
        log_message(f"Tapahtuma {event_id} ({event_name}) käsitelty ONNISTUNEESTI")
    else:
        log_message(f"Tapahtuma {event_id} ({event_name}) käsittely EPÄONNISTUI: {error_msg}", "ERROR")

def main():
    try:
        log_message("Aloitetaan automaattihaku")
        
        events = fetch_events()
        if not events:
            log_message("Ei uusia tapahtumia saatavilla")
        else:
            log_message(f"Aloitetaan {len(events)} tapahtuman käsittely")
            
            with ThreadPoolExecutor(max_workers=5) as executor:
                for event in events:
                    executor.submit(process_event, event)
            
            log_message("Kaikki tapahtumat käsitelty")
        
        log_message("Aloitetaan ikälaskurin suoritus")
        run_ikalaskuri()
        
    except Exception as e:
        log_message(f"Kriittinen virhe pääfunktiossa: {str(e)}", "CRITICAL")
        raise

if __name__ == "__main__":
    main()
