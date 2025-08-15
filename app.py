import os
import sqlite3
from datetime import datetime, timedelta
from flask import Flask, render_template, request, url_for, redirect, flash
import subprocess

app = Flask(__name__)
app.secret_key = 'salainen_avain'  # Tarvitaan flash-viesteille

# Tietokannan asetukset
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
TEMPLATE_DIR = os.path.join(BASE_DIR, 'templates')
app = Flask(__name__, template_folder=TEMPLATE_DIR
DATABASE_FILE = os.path.join(DATA_DIR, "kilpailut.db")
LAST_UPDATE_FILE = os.path.join(DATA_DIR, "last_update.txt")

def get_db_connection():
    conn = sqlite3.connect(DATABASE_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def check_db_update():
    """Tarkistaa päivitysajan ja päivittää tietokannan tarvittaessa"""
    try:
        # Lue viimeisin päivitysaika
        if os.path.exists(LAST_UPDATE_FILE):
            with open(LAST_UPDATE_FILE, 'r') as f:
                last_update_str = f.read().strip()
                last_update = datetime.fromisoformat(last_update_str)
        else:
            last_update = datetime.min  # Hyvin vanha päivämäärä
        
        # Päivitä jos yli 24h vanha
        if datetime.now() - last_update > timedelta(days=1):
            update_database()
    except Exception as e:
        app.logger.error(f"Automaattipäivitys epäonnistui: {str(e)}")

def update_database():
    """Suorita tietokannan päivitys"""
    try:
        # Suorita automaatti_haku.py
        result = subprocess.run(['python', 'automaatti_haku.py'], 
                              capture_output=True, text=True)
        
        if result.returncode == 0:
            # Päivitä päivitysaika
            with open(LAST_UPDATE_FILE, 'w') as f:
                f.write(datetime.now().isoformat())
            return True
        else:
            app.logger.error(f"Päivitys epäonnistui: {result.stderr}")
            return False
    except Exception as e:
        app.logger.error(f"Päivitysprosessi epäonnistui: {str(e)}")
        return False

@app.route('/paivita_tietokanta')
def paivita_tietokanta():
    """Manuaalinen tietokannan päivitys"""
    if update_database():
        flash('Tietokanta päivitetty onnistuneesti!', 'success')
    else:
        flash('Tietokannan päivitys epäonnistui', 'danger')
    return redirect(url_for('index'))

@app.before_request
def before_request():
    """Suorita ennen jokaista pyyntöä"""
    if request.endpoint != 'static':  # Älä tarkista staattisille resursseille
        check_db_update()

@app.context_processor
def inject_template_vars():
    """Lisää yhteiset muuttujat kaikille templateille"""
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
        'db_needs_update': needs_update
    }


@app.route('/')
def index():
    return render_template('index.html')

@app.route('/kilpailut')
def nayta_kilpailut():
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute("SELECT kilpailu_id, kilpailun_nimi, alkupvm FROM Kilpailut ORDER BY alkupvm DESC")
    kilpailut = c.fetchall()
    
    conn.close()
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
    vuosi = request.args.get('vuosi', type=int)  # Uusi parametri
    
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
    
    sql = f"""
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
                        END {jarjestys}
                ) AS rn
            FROM Urheilijat u
            JOIN Tulokset t ON u.urheilija_id = t.urheilija_id
            LEFT JOIN Seurat s ON u.seura_id = s.seura_id
            JOIN Lajit l ON t.laji_id = l.laji_id
            JOIN Kilpailut k ON l.kilpailu_id = k.kilpailu_id
            WHERE l.lajin_nimi LIKE ? AND t.tulos != 'DNS' AND t.tulos != 'DNF'
    """
    
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
            sql += " AND (CAST(SUBSTR(k.alkupvm, 1, 4) AS INTEGER) - u.syntymavuosi BETWEEN ? AND ?"
            params.extend([ika_min, ika_max])
        elif ika_min is not None:
            sql += " AND (CAST(SUBSTR(k.alkupvm, 1, 4) AS INTEGER) - u.syntymavuosi >= ?"
            params.append(ika_min)
        elif ika_max is not None:
            sql += " AND (CAST(SUBSTR(k.alkupvm, 1, 4) AS INTEGER) - u.syntymavuosi <= ?"
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
        ORDER BY tulos_numero {}
        LIMIT 50
    """.format(jarjestys)
    
    c.execute(sql, params)
    tulokset = c.fetchall()
    
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
    
    conn = get_db_connection()
    c = conn.cursor()
    
    # Hae kaikki urheilijat suoraan Urheilijat-taulusta
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
    
    # Lisää sukupuoli-suodatin jos annettu
    if sukupuoli in ['M', 'N']:
        sql += " AND sukupuoli = ?"
        params.append(sukupuoli)
    
    # Suodata pois urheilijat joilta puuttuu tärkeät tiedot
    sql += " AND sukupuoli IS NOT NULL AND syntymavuosi IS NOT NULL"
    
    c.execute(sql, params)
    kaikki_urheilijat = c.fetchall()
    
    # Käsittele duplikaatit Pythonissa
    unique_urheilijat = {}
    for urheilija in kaikki_urheilijat:
        # Luo uniikki avain nimelle ja syntymävuodelle
        avain = f"{urheilija['etunimi'].lower()}-{urheilija['sukunimi'].lower()}-{urheilija['syntymavuosi']}"
        if avain not in unique_urheilijat:
            unique_urheilijat[avain] = urheilija
    
    # Muunna sanakirja listaksi ja järjestä
    urheilijat = sorted(unique_urheilijat.values(), 
                       key=lambda x: (x['sukunimi'], x['etunimi']))
    
    # Suodata ikähaarukalla jos annettu
    if ika_min is not None or ika_max is not None:
        filtered_urheilijat = []
        for urheilija in urheilijat:
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
    # Varmistetaan että tietokanta on olemassa
    if not os.path.exists(DATABASE_FILE):
        print("Tietokantaa ei löydy! Luo ensin tietokanta suorittamalla tulosten_haku.py")
        exit(1)

    port = int(os.environ.get('PORT', 8080))  # Käytä ympäristömuuttujaa tai 8080
    app.run(host='0.0.0.0', port=port)
