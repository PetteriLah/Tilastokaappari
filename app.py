import threading
import time
from datetime import datetime, timedelta
import os
import psycopg2
from flask import Flask, render_template, request, url_for, redirect, flash, g
import subprocess
import re
from urllib.parse import urlparse

app = Flask(__name__)
app.secret_key = 'salainen_avain'

# PostgreSQL-tietokannan asetukset
DATABASE_URL = os.environ.get('DATABASE_URL')

# Alusta päivitystila applikaatiolle
app.update_in_progress = False
app.last_update_status = {"success": None, "message": ""}

def get_db_connection():
    # Parsitaan tietokantaosoite
    result = urlparse(DATABASE_URL)
    username = result.username
    password = result.password
    database = result.path[1:]
    hostname = result.hostname
    port = result.port
    
    conn = psycopg2.connect(
        database=database,
        user=username,
        password=password,
        host=hostname,
        port=port
    )
    return conn

def get_last_update_time():
    """Hakee viimeisimmän päivitysajan tietokannasta"""
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT MAX(last_updated) as last_update FROM Kilpailut")
        result = c.fetchone()
        conn.close()
        
        if result and result[0]:
            return result[0]
        return datetime.min
    except Exception as e:
        app.logger.error(f"Päivitysajan hakuvirhe: {str(e)}")
        return datetime.min

def update_database_thread():
    """Suorita tietokannan päivitys taustasäikeessä"""
    app.update_in_progress = True
    try:
        app.logger.info("Taustapäivitys alkoi")
        result = subprocess.run(['python', 'automaatti_haku.py'], 
                              capture_output=True, text=True, timeout=3600)  # 1h timeout

        if result.returncode == 0:
            # Päivitetään viimeisin päivitysaika tietokantaan
            current_time = datetime.now().isoformat()
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("UPDATE Kilpailut SET last_updated = %s WHERE last_updated = (SELECT MAX(last_updated) FROM Kilpailut)", 
                     (current_time,))
            conn.commit()
            conn.close()
            
            app.last_update_status = {"success": True, "message": "Tietokanta päivitetty onnistuneesti!"}
            app.logger.info("Taustapäivitys valmis")
        else:
            error_msg = f"Päivitys epäonnistui: {result.stderr}"
            app.last_update_status = {"success": False, "message": error_msg}
            app.logger.error(error_msg)
            
    except subprocess.TimeoutExpired:
        error_msg = "Päivitys aikakatkaistiin (yli 1 tunti)"
        app.last_update_status = {"success": False, "message": error_msg}
        app.logger.error(error_msg)
    except Exception as e:
        error_msg = f"Päivitysprosessi epäonnistui: {str(e)}"
        app.last_update_status = {"success": False, "message": error_msg}
        app.logger.error(error_msg)
    finally:
        app.update_in_progress = False

def check_db_update():
    """Tarkistaa päivitysajan ja käynnistää taustapäivityksen tarvittaessa"""
    
    # Älä päivitä jos päivitys on jo meneillään
    if app.update_in_progress:
        app.logger.info("Päivitys on jo meneillään")
        return
        
    try:
        # Hae viimeisin päivitysaika tietokannasta
        last_update = get_last_update_time()

        # Päivitä jos yli 24h vanha
        if datetime.now() - last_update > timedelta(days=1):
            # Käynnistä taustasäie
            thread = threading.Thread(target=update_database_thread)
            thread.daemon = True  # Säie sammuu kun pääohjelma sammuu
            thread.start()
            app.logger.info("Taustapäivitys käynnistetty")
            
    except Exception as e:
        app.logger.error(f"Automaattipäivitys epäonnistui: {str(e)}")

@app.route('/paivita_tietokanta')
def paivita_tietokanta():
    """Manuaalinen tietokannan päivitys taustalla"""
    
    if app.update_in_progress:
        flash('Päivitys on jo meneillään', 'info')
    else:
        # Käynnistä taustapäivitys
        thread = threading.Thread(target=update_database_thread)
        thread.daemon = True
        thread.start()
        flash('Taustapäivitys käynnistetty. Sivu päivittyy automaattisesti.', 'success')
    
    return redirect(url_for('index'))

@app.before_request
def before_request():
    """Suorita ennen jokaista pyyntöä"""
    if request.endpoint != 'static':
        check_db_update()

@app.context_processor
def inject_template_vars():
    """Lisää yhteiset muuttujat kaikille templateille"""
    
    last_update = None
    last_update_dt = get_last_update_time()
    needs_update = True

    if last_update_dt > datetime.min:
        last_update = last_update_dt.strftime('%d.%m.%Y %H:%M')
        needs_update = (datetime.now() - last_update_dt) > timedelta(days=1)

    return {
        'current_year': datetime.now().year,
        'db_last_update': last_update,
        'db_needs_update': needs_update,
        'update_in_progress': app.update_in_progress,
        'last_update_status': app.last_update_status
    }

# Loput reitit pysyvät ennallaan...
@app.route('/')
def index():
    return render_template('index.html')

# ... muut reitit
@app.route('/kilpailut')
def nayta_kilpailut():
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT kilpailu_id, kilpailun_nimi, alkupvm FROM Kilpailut ORDER BY alkupvm DESC")
        kilpailut = c.fetchall()
        
        # Muunnetaan tulokset sanakirjoiksi
        kilpailut_list = []
        for kilpailu in kilpailut:
            kilpailut_list.append({
                'kilpailu_id': kilpailu[0],
                'kilpailun_nimi': kilpailu[1],
                'alkupvm': kilpailu[2]
            })
            
    return render_template('kilpailut.html', kilpailut=kilpailut_list)
    

@app.route('/kilpailu/<int:kilpailu_id>')
def nayta_kilpailun_tulokset(kilpailu_id):
    conn = get_db_connection()
    c = conn.cursor()
    
    # Hae kilpailun perustiedot
    c.execute("SELECT kilpailun_nimi, alkupvm, paikkakunta FROM Kilpailut WHERE kilpailu_id = %s", (kilpailu_id,))
    kilpailu = c.fetchone()
    
    if not kilpailu:
        conn.close()
        return render_template('error.html', message='Kilpailua ei löytynyt'), 404
    
    # Muunnetaan kilpailu sanakirjaksi
    kilpailu_dict = {
        'kilpailun_nimi': kilpailu[0],
        'alkupvm': kilpailu[1],
        'paikkakunta': kilpailu[2]
    }
    
    # Hae kilpailun lajit
    c.execute("""
        SELECT laji_id, lajin_nimi, sarja 
        FROM Lajit 
        WHERE kilpailu_id = %s
        ORDER BY lajin_nimi
    """, (kilpailu_id,))
    lajit = c.fetchall()
    
    # Muunnetaan lajit sanakirjoiksi
    lajit_list = []
    for laji in lajit:
        lajit_list.append({
            'laji_id': laji[0],
            'lajin_nimi': laji[1],
            'sarja': laji[2]
        })
    
    # Hae tulokset kullekin lajille
    tulokset = {}
    for laji in lajit_list:
        c.execute("""
            SELECT t.sijoitus, u.etunimi, u.sukunimi, 
                   COALESCE(s.seura_nimi, '-') as seura, 
                   COALESCE(t.tulos, t.lisatiedot) as tulos,
                   u.syntymavuosi, u.sukupuoli
            FROM Tulokset t
            JOIN Urheilijat u ON t.urheilija_id = u.urheilija_id
            LEFT JOIN Seurat s ON u.seura_id = s.seura_id
            WHERE t.laji_id = %s
            ORDER BY t.sijoitus
        """, (laji['laji_id'],))
        results = c.fetchall()
        
        # Muunnetaan tulokset sanakirjoiksi
        tulokset_list = []
        for result in results:
            tulokset_list.append({
                'sijoitus': result[0],
                'etunimi': result[1],
                'sukunimi': result[2],
                'seura': result[3],
                'tulos': result[4],
                'syntymavuosi': result[5],
                'sukupuoli': result[6]
            })
            
        tulokset[laji['laji_id']] = tulokset_list
    
    conn.close()
    
    return render_template('kilpailun_tulokset.html', 
                         kilpailu=kilpailu_dict, 
                         lajit=lajit_list, 
                         tulokset=tulokset)

# ... (muut reitit pysyvät samanlaisina kuin aiemmin)

if __name__ == '__main__':
    # Varmista että tietokanta on olemassa
    if not DATABASE_URL:
        print("Tietokantaosoitetta ei löydy ympäristömuuttujasta DATABASE_URL!")
        exit(1)
    
    # Testaa tietokantayhteys
    try:
        conn = get_db_connection()
        print("Tietokantayhteys toimii!")
        conn.close()
    except Exception as e:
        print(f"Tietokantayhteys epäonnistui: {e}")
        exit(1)
    
    # Käynnistä suoraan Gunicornilla Fly.io:ssa
    # (Dockerfile määrittää jo oikean käynnistyskomennon)
    app.run(host='0.0.0.0', port=10000, debug=False)
