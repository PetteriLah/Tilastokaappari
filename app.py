import threading
import time
from datetime import datetime, timedelta
import os
import sqlite3
from flask import Flask, render_template, request, url_for, redirect, flash
import subprocess

app = Flask(__name__)
app.secret_key = 'salainen_avain'

# Tietokannan asetukset
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
TEMPLATE_DIR = os.path.join(BASE_DIR, 'templates')
DATABASE_FILE = os.path.join(DATA_DIR, "kilpailut.db")
LAST_UPDATE_FILE = os.path.join(DATA_DIR, "last_update.txt")

# Päivitystilan seuranta
update_in_progress = False
last_update_status = {"success": None, "message": ""}

def get_db_connection():
    conn = sqlite3.connect(DATABASE_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def update_database_thread():
    """Suorita tietokannan päivitys taustasäikeessä"""
    global update_in_progress, last_update_status
    
    update_in_progress = True
    try:
        app.logger.info("Taustapäivitys alkoi")
        result = subprocess.run(['python', 'automaatti_haku.py'], 
                              capture_output=True, text=True, timeout=3600)  # 1h timeout

        if result.returncode == 0:
            with open(LAST_UPDATE_FILE, 'w') as f:
                f.write(datetime.now().isoformat())
            last_update_status = {"success": True, "message": "Tietokanta päivitetty onnistuneesti!"}
            app.logger.info("Taustapäivitys valmis")
        else:
            error_msg = f"Päivitys epäonnistui: {result.stderr}"
            last_update_status = {"success": False, "message": error_msg}
            app.logger.error(error_msg)
            
    except subprocess.TimeoutExpired:
        error_msg = "Päivitys aikakatkaistiin (yli 1 tunti)"
        last_update_status = {"success": False, "message": error_msg}
        app.logger.error(error_msg)
    except Exception as e:
        error_msg = f"Päivitysprosessi epäonnistui: {str(e)}"
        last_update_status = {"success": False, "message": error_msg}
        app.logger.error(error_msg)
    finally:
        update_in_progress = False

def check_db_update():
    """Tarkistaa päivitysajan ja käynnistää taustapäivityksen tarvittaessa"""
    global update_in_progress
    
    # Älä päivitä jos päivitys on jo meneillään
    if update_in_progress:
        app.logger.info("Päivitys on jo meneillään")
        return
        
    try:
        # Lue viimeisin päivitysaika
        if os.path.exists(LAST_UPDATE_FILE):
            with open(LAST_UPDATE_FILE, 'r') as f:
                last_update_str = f.read().strip()
                last_update = datetime.fromisoformat(last_update_str)
        else:
            last_update = datetime.min

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
    global update_in_progress
    
    if update_in_progress:
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
    global update_in_progress, last_update_status
    
    last_update = None
    last_update_dt = None
    needs_update = True

    if os.path.exists(LAST_UPDATE_FILE):
        try:
            with open(LAST_UPDATE_FILE, 'r') as f:
                last_update_str = f.read().strip()
                last_update_dt = datetime.fromisoformat(last_update_str)
                last_update = last_update_dt.strftime('%d.%m.%Y %H:%M')
                needs_update = (datetime.now() - last_update_dt) > timedelta(days=1)
        except Exception as e:
            app.logger.error(f"Päivitysajan lukuvirhe: {str(e)}")
            needs_update = True

    return {
        'current_year': datetime.now().year,
        'db_last_update': last_update,
        'db_needs_update': needs_update,
        'update_in_progress': update_in_progress,
        'last_update_status': last_update_status
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
    return render_template('kilpailut.html', kilpailut=kilpailut)
    

@app.route('/kilpailu/<int:kilpailu_id>')
def nayta_kilpailun_tulokset(kilpailu_id):
    conn = get_db_connection()
    c = conn.cursor()
    
    # Hae kilpailun perustiedot
    c.execute("SELECT kilpailun_nimi, alkupvm FROM Kilpailut WHERE kilpailu_id = ?", (kilpailu_id,))
    kilpailu = c.fetchone()
    
    if not kilpailu:
        conn.close()
        return render_template('error.html', message='Kilpailua ei löytynyt'), 404
    
    # Hae kilpailun lajit
    c.execute("""
        SELECT laji_id, lajin_nimi, sarja 
        FROM Lajit 
        WHERE kilpailu_id = ?
        ORDER BY lajin_nimi
    """, (kilpailu_id,))
    lajit = c.fetchall()
    
    # Hae tulokset kullekin lajille
    tulokset = {}
    for laji in lajit:
        c.execute("""
            SELECT t.sijoitus, u.etunimi, u.sukunimi, 
                   COALESCE(s.seura_nimi, '-') as seura, 
                   COALESCE(t.tulos, t.lisatiedot) as tulos,
                   u.syntymavuosi, u.sukupuoli
            FROM Tulokset t
            JOIN Urheilijat u ON t.urheilija_id = u.urheilija_id
            LEFT JOIN Seurat s ON u.seura_id = s.seura_id
            WHERE t.laji_id = ?
            ORDER BY t.sijoitus
        """, (laji['laji_id'],))
        tulokset[laji['laji_id']] = c.fetchall()
    
    conn.close()
    
    return render_template('kilpailun_tulokset.html', 
                         kilpailu=kilpailu, 
                         lajit=lajit, 
                         tulokset=tulokset)

@app.route('/urheilija')
def hae_urheilijan_tulokset():
    nimi = request.args.get('nimi', '').strip()
    sukupuoli = request.args.get('sukupuoli', '').upper()
    ika_min = request.args.get('ika_min', type=int)
    ika_max = request.args.get('ika_max', type=int)
    
    if not nimi:
        return render_template('error.html', message='Anna urheilijan nimi'), 400
    
    conn = get_db_connection()
    c = conn.cursor()
    
    # Muodosta SQL-kysely dynaamisesti suodattimien perusteella
    sql = """
        SELECT l.lajin_nimi, l.sarja, k.kilpailun_nimi, k.alkupvm, 
               COALESCE(t.tulos, t.lisatiedot) as tulos,
               t.sijoitus, u.syntymavuosi, u.sukupuoli
        FROM Tulokset t
        JOIN Urheilijat u ON t.urheilija_id = u.urheilija_id
        JOIN Lajit l ON t.laji_id = l.laji_id
        JOIN Kilpailut k ON l.kilpailu_id = k.kilpailu_id
        WHERE (u.etunimi LIKE ? OR u.sukunimi LIKE ? OR (u.etunimi || ' ' || u.sukunimi) LIKE ?)
    """
    params = [f'%{nimi}%', f'%{nimi}%', f'%{nimi}%']
    
    # Lisää sukupuoli-suodatin jos annettu
    if sukupuoli in ['M', 'N']:
        sql += " AND u.sukupuoli = ?"
        params.append(sukupuoli)
    
    # Lisää ikäsuodattimet jos annettu
    if ika_min is not None or ika_max is not None:
        sql += " AND k.alkupvm IS NOT NULL AND u.syntymavuosi IS NOT NULL"
        
        if ika_min is not None and ika_max is not None:
            sql += " AND (CAST(SUBSTR(k.alkupvm, 1, 4) AS INTEGER) - u.syntymavuosi BETWEEN ? AND ?"
            params.extend([ika_min, ika_max])
        elif ika_min is not None:
            sql += " AND (CAST(SUBSTR(k.alkupvm, 1, 4) AS INTEGER) - u.syntymavuosi >= ?"
            params.append(ika_min)
        elif ika_max is not None:
            sql += " AND (CAST(SUBSTR(k.alkupvm, 1, 4) AS INTEGER) - u.syntymavuosi <= ?"
            params.append(ika_max)
    
    sql += " ORDER BY k.alkupvm DESC, l.lajin_nimi"
    
    c.execute(sql, params)
    tulokset = c.fetchall()
    
    conn.close()
    
    return render_template('urheilijan_tulokset.html', 
                         nimi=nimi,
                         tulokset=tulokset,
                         sukupuoli=sukupuoli,
                         ika_min=ika_min,
                         ika_max=ika_max)

@app.route('/laji')
def hae_lajin_parhaat_tulokset():
    laji = request.args.get('laji', '').strip()
    sukupuoli = request.args.get('sukupuoli', '').upper()
    ika_min = request.args.get('ika_min', type=int)
    ika_max = request.args.get('ika_max', type=int)
    vuosi = request.args.get('vuosi', type=int)
    
    if not laji:
        return render_template('error.html', message='Anna lajin nimi'), 400
    
    # Määritä järjestyssuunta
    jarjestys = "DESC"  # Oletus: suurempi on parempi
    aikalajit = ['kävely', 'aidat', 'esteet', 'viesti', 'aitaviesti', 'maantiejuoksu',
                'maraton', 'puolimaraton', '10000m', '5000m', '3000m', '1500m', '800m', '400m']
    
    if (laji.lower().endswith('m') or 
        any(aikalaji in laji.lower() for aikalaji in aikalajit)):
        jarjestys = "ASC"  # Pienempi tulos on parempi
    
    conn = get_db_connection()
    c = conn.cursor()
    
    # Muodosta SQL-kysely
    sql = """
        WITH ParhaatTulokset AS (
            SELECT 
                u.urheilija_id,
                u.etunimi, 
                u.sukunimi, 
                COALESCE(s.seura_nimi, '-') as seura,
                COALESCE(t.tulos, t.lisatiedot) as tulos,
                k.kilpailun_nimi, 
                k.alkupvm,
                u.syntymavuosi, 
                u.sukupuoli,
                t.sijoitus,
                CASE WHEN t.tulos GLOB '*[0-9]*:[0-9]*' THEN
                    CAST(substr(t.tulos, 1, instr(t.tulos, ':')-1) AS INTEGER) * 60 + 
                    CAST(substr(t.tulos, instr(t.tulos, ':')+1) AS INTEGER)
                WHEN t.tulos GLOB '*[0-9]*.*[0-9]*' THEN
                    CAST(t.tulos AS REAL)
                ELSE
                    CASE WHEN ? = 'ASC' THEN 999999 ELSE -999999 END
                END AS tulos_numero,
                ROW_NUMBER() OVER (
                    PARTITION BY u.urheilija_id 
                    ORDER BY 
                        CASE WHEN t.tulos GLOB '*[0-9]*:[0-9]*' THEN
                            CAST(substr(t.tulos, 1, instr(t.tulos, ':')-1) AS INTEGER) * 60 + 
                            CAST(substr(t.tulos, instr(t.tulos, ':')+1) AS INTEGER)
                        WHEN t.tulos GLOB '*[0-9]*.*[0-9]*' THEN
                            CAST(t.tulos AS REAL)
                        ELSE
                            CASE WHEN ? = 'ASC' THEN 999999 ELSE -999999 END
                        END {0}
                ) AS rn
            FROM Urheilijat u
            JOIN Tulokset t ON u.urheilija_id = t.urheilija_id
            LEFT JOIN Seurat s ON u.seura_id = s.seura_id
            JOIN Lajit l ON t.laji_id = l.laji_id
            JOIN Kilpailut k ON l.kilpailu_id = k.kilpailu_id
            WHERE l.lajin_nimi LIKE ? AND t.tulos != 'DNS' AND t.tulos != 'DNF'
    """.format(jarjestys)
    
    params = [jarjestys, jarjestys, f'%{laji}%']
    
    # Lisää sukupuoli-suodatin
    if sukupuoli in ['M', 'N']:
        sql += " AND u.sukupuoli = ?"
        params.append(sukupuoli)
    
    # Lisää vuosi-suodatin
    if vuosi is not None:
        sql += " AND strftime('%Y', k.alkupvm) = ?"
        params.append(str(vuosi))
    
    # Lisää ikäsuodattimet
    if ika_min is not None or ika_max is not None:
        sql += " AND k.alkupvm IS NOT NULL AND u.syntymavuosi IS NOT NULL"
        
        if ika_min is not None and ika_max is not None:
            sql += " AND (CAST(SUBSTR(k.alkupvm, 1, 4) AS INTEGER) - u.syntymavuosi BETWEEN ? AND ?)"
            params.extend([ika_min, ika_max])
        elif ika_min is not None:
            sql += " AND (CAST(SUBSTR(k.alkupvm, 1, 4) AS INTEGER) - u.syntymavuosi >= ?)"
            params.append(ika_min)
        elif ika_max is not None:
            sql += " AND (CAST(SUBSTR(k.alkupvm, 1, 4) AS INTEGER) - u.syntymavuosi <= ?)"
            params.append(ika_max)
    
    sql += """
        )
        SELECT 
            etunimi, 
            sukunimi, 
            seura,
            tulos,
            kilpailun_nimi, 
            alkupvm,
            syntymavuosi, 
            sukupuoli,
            sijoitus
        FROM ParhaatTulokset
        WHERE rn = 1
        ORDER BY tulos_numero {0}
        LIMIT 50
    """.format(jarjestys)
    
    try:
        c.execute(sql, params)
        tulokset = c.fetchall()
    except sqlite3.OperationalError as e:
        conn.close()
        app.logger.error(f"SQL virhe: {str(e)}")
        app.logger.error(f"SQL-kysely: {sql}")
        app.logger.error(f"Parametrit: {params}")
        return render_template('error.html', message='Tietokantavirhe'), 500
    
    # Hae saatavilla olevat vuodet valikkoon
    c.execute("""
        SELECT DISTINCT strftime('%Y', alkupvm) as vuosi
        FROM Kilpailut
        WHERE alkupvm IS NOT NULL
        ORDER BY vuosi DESC
    """)
    vuodet = [r['vuosi'] for r in c.fetchall()]
    
    conn.close()
    
    return render_template('lajin_parhaat.html', 
                         laji=laji,
                         tulokset=tulokset,
                         sukupuoli=sukupuoli,
                         ika_min=ika_min,
                         ika_max=ika_max,
                         vuosi=vuosi,
                         vuodet=vuodet)

@app.route('/urheilijat')
def listaa_urheilijat():
    sukupuoli = request.args.get('sukupuoli', '').upper()
    ika_min = request.args.get('ika_min', type=int)
    ika_max = request.args.get('ika_max', type=int)
    
    current_year = datetime.now().year  # Lisätty tämä rivi
    
    conn = get_db_connection()
    c = conn.cursor()
    
    sql = """
        SELECT 
            urheilija_id,
            TRIM(etunimi) as etunimi,
            TRIM(sukunimi) as sukunimi,
            sukupuoli,
            syntymavuosi
        FROM Urheilijat
        WHERE 1=1
    """
    
    params = []
    
    if sukupuoli in ['M', 'N']:
        sql += " AND sukupuoli = ?"
        params.append(sukupuoli)
    
    sql += " AND sukupuoli IS NOT NULL AND syntymavuosi IS NOT NULL"
    
    c.execute(sql, params)
    kaikki_urheilijat = c.fetchall()
    
    unique_urheilijat = {}
    for urheilija in kaikki_urheilijat:
        avain = f"{urheilija['etunimi'].lower()}-{urheilija['sukunimi'].lower()}-{urheilija['syntymavuosi']}"
        if avain not in unique_urheilijat:
            unique_urheilijat[avain] = urheilija
    
    urheilijat = sorted(unique_urheilijat.values(), 
                       key=lambda x: (x['sukunimi'], x['etunimi']))
    
    if ika_min is not None or ika_max is not None:
        filtered_urheilijat = []
        for urheilija in urheilijat:
            if urheilija['syntymavuosi']:
                ika = current_year - urheilija['syntymavuosi']
                if ((ika_min is None or ika >= ika_min) and 
                    (ika_max is None or ika <= ika_max)):
                    filtered_urheilijat.append(urheilija)
        urheilijat = filtered_urheilijat
    
    conn.close()
    
    return render_template('urheilijat.html', 
                        urheilijat=urheilijat,
                        sukupuoli=sukupuoli,
                        ika_min=ika_min,
                        ika_max=ika_max)

@app.route('/lajit')
def listaa_lajit():
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute("""
        SELECT DISTINCT lajin_nimi 
        FROM Lajit
        ORDER BY lajin_nimi
    """)
    lajit = c.fetchall()
    
    conn.close()
    
    return render_template('lajit.html', lajit=lajit)

if __name__ == '__main__':
    # Varmista että tietokanta on olemassa
    if not os.path.exists(DATABASE_FILE):
        print("Tietokantaa ei löydy! Alustetaan...")
        if not update_database():
            print("Tietokannan alustus epäonnistui")
            exit(1)
    
    # Käynnistä suoraan Gunicornilla Fly.io:ssa
    # (Dockerfile määrittää jo oikean käynnistyskomennon)
    app.run(host='0.0.0.0', port=10000, debug=False)
