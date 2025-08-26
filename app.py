import threading
import time
from datetime import datetime, timedelta
import os
import psycopg2
from flask import Flask, render_template, request, url_for, redirect, flash
import subprocess

app = Flask(__name__)
app.secret_key = 'salainen_avain'

# PostgreSQL-tietokannan asetukset
DATABASE_URL = os.environ.get('DATABASE_URL')

# Päivitystilan seuranta
update_in_progress = False
last_update_status = {"success": None, "message": ""}

def get_db_connection():
    """Muodosta yhteys PostgreSQL-tietokantaan"""
    try:
        # Käytä suoraan DATABASE_URL:ia
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        return conn
    except Exception as e:
        app.logger.error(f"Tietokantayhteys epäonnistui: {str(e)}")
        raise

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
    global update_in_progress, last_update_status

    update_in_progress = True
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
    last_update_dt = get_last_update_time()
    needs_update = True

    if last_update_dt > datetime.min:
        last_update = last_update_dt.strftime('%d.%m.%Y %H:%M')
        needs_update = (datetime.now() - last_update_dt) > timedelta(days=1)

    return {
        'current_year': datetime.now().year,
        'db_last_update': last_update,
        'db_needs_update': needs_update,
        'update_in_progress': update_in_progress,
        'last_update_status': last_update_status
    }

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/kilpailut')
def nayta_kilpailut():
    try:
        with get_db_connection() as conn:
            c = conn.cursor()
            # Muutettu kysely: hae vain kilpailut joissa on tuloksia
            c.execute("""
                SELECT DISTINCT k.kilpailu_id, k.kilpailun_nimi, k.alkupvm 
                FROM Kilpailut k
                JOIN Lajit l ON k.kilpailu_id = l.kilpailu_id
                JOIN Tulokset t ON l.laji_id = t.laji_id
                ORDER BY k.alkupvm DESC
            """)
            kilpailut = c.fetchall()

            # Muunnetaan tuplet sanakirjoiksi
            kilpailut_list = []
            for kilpailu in kilpailut:
                kilpailut_list.append({
                    'kilpailu_id': kilpailu[0],
                    'kilpailun_nimi': kilpailu[1],
                    'alkupvm': kilpailu[2]
                })

        return render_template('kilpailut.html', kilpailut=kilpailut_list)
    except Exception as e:
        app.logger.error(f"Kilpailujen hakuvirhe: {str(e)}")
        return render_template('error.html', message='Tietokantayhteys epäonnistui'), 500

@app.route('/kilpailu/<int:kilpailu_id>')
def nayta_kilpailun_tulokset(kilpailu_id):
    try:
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
                       COALESCE(CAST(t.tulos AS TEXT), t.lisatiedot) as tulos,
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
    except Exception as e:
        app.logger.error(f"Kilpailun tulosten hakuvirhe: {str(e)}")
        return render_template('error.html', message='Tietokantavirhe'), 500

@app.route('/urheilija')
def hae_urheilijan_tulokset():
    nimi = request.args.get('nimi', '').strip()
    sukupuoli = request.args.get('sukupuoli', '').upper()
    ika_min = request.args.get('ika_min', type=int)
    ika_max = request.args.get('ika_max', type=int)

    if not nimi:
        return render_template('error.html', message='Anna urheilijan nimi'), 400

    try:
        conn = get_db_connection()
        c = conn.cursor()

        sql = """
            SELECT l.lajin_nimi, l.sarja, k.kilpailun_nimi, k.alkupvm, 
                   COALESCE(CAST(t.tulos AS TEXT), t.lisatiedot) as tulos,
                   t.sijoitus, u.syntymavuosi, u.sukupuoli
            FROM Tulokset t
            JOIN Urheilijat u ON t.urheilija_id = u.urheilija_id
            JOIN Lajit l ON t.laji_id = l.laji_id
            JOIN Kilpailut k ON l.kilpailu_id = k.kilpailu_id
            WHERE (u.etunimi ILIKE %s OR u.sukunimi ILIKE %s OR (u.etunimi || ' ' || u.sukunimi) ILIKE %s)
        """
        params = [f'%{nimi}%', f'%{nimi}%', f'%{nimi}%']

        if sukupuoli in ['M', 'N']:
            sql += " AND u.sukupuoli = %s"
            params.append(sukupuoli)

        if ika_min is not None or ika_max is not None:
            sql += " AND k.alkupvm IS NOT NULL AND u.syntymavuosi IS NOT NULL"

            if ika_min is not None and ika_max is not None:
                sql += " AND (EXTRACT(YEAR FROM k.alkupvm) - u.syntymavuosi BETWEEN %s AND %s)"
                params.extend([ika_min, ika_max])
            elif ika_min is not None:
                sql += " AND (EXTRACT(YEAR FROM k.alkupvm) - u.syntymavuosi >= %s)"
                params.append(ika_min)
            elif ika_max is not None:
                sql += " AND (EXTRACT(YEAR FROM k.alkupvm) - u.syntymavuosi <= %s)"
                params.append(ika_max)

        sql += " ORDER BY k.alkupvm DESC, l.lajin_nimi"

        c.execute(sql, params)
        results = c.fetchall()

        tulokset = []
        for result in results:
            tulokset.append({
                'lajin_nimi': result[0],
                'sarja': result[1],
                'kilpailun_nimi': result[2],
                'alkupvm': result[3],
                'tulos': result[4],
                'sijoitus': result[5],
                'syntymavuosi': result[6],
                'sukupuoli': result[7]
            })

        conn.close()

        return render_template('urheilijan_tulokset.html', 
                             nimi=nimi,
                             tulokset=tulokset,
                             sukupuoli=sukupuoli,
                             ika_min=ika_min,
                             ika_max=ika_max)
    except Exception as e:
        app.logger.error(f"Urheilijan tulosten hakuvirhe: {str(e)}")
        return render_template('error.html', message='Tietokantavirhe'), 500

@app.route('/laji')
def hae_lajin_parhaat_tulokset():
    laji = request.args.get('laji', '').strip()
    sukupuoli = request.args.get('sukupuoli', '').upper()
    ika_min = request.args.get('ika_min', type=int)
    ika_max = request.args.get('ika_max', type=int)
    vuosi = request.args.get('vuosi', type=int)

    if not laji:
        return render_template('error.html', message='Anna lajin nimi'), 400

    # Määritellään oikea järjestyssuunta eri lajeille
    jarjestys = "DESC"  # Oletus: pituuslajit (suurin paras)
    
    # Aikalajit (pienin aika on paras)
    aikalajit = ['kävely', 'aidat', 'esteet', 'viesti', 'aitaviesti', 'maantiejuoksu',
                'maraton', 'puolimaraton', '10000m', '5000m', '3000m', '1500m', '800m', '400m',
                '200m', '100m', '60m']

    # Tarkistetaan onko kyseessä aikalaji
    if (laji.lower().endswith('m') or 
        any(aikalaji in laji.lower() for aikalaji in aikalajit)):
        jarjestys = "ASC"

    try:
        conn = get_db_connection()
        c = conn.cursor()

        # Määritellään tulosvertailufunktio järjestyksen mukaan
        if jarjestys == "ASC":
            order_direction = "ASC"
            default_value = "999999"
        else:
            order_direction = "DESC"
            default_value = "-999999"

        sql = f"""
            WITH ParhaatTulokset AS (
                SELECT 
                    u.urheilija_id,
                    u.etunimi, 
                    u.sukunimi, 
                    COALESCE(s.seura_nimi, '-') as seura,
                    COALESCE(CAST(t.tulos AS TEXT), t.lisatiedot) as tulos,
                    k.kilpailun_nimi, 
                    k.alkupvm,
                    u.syntymavuosi, 
                    u.sukupuoli,
                    t.sijoitus,
                    CASE 
                        WHEN CAST(t.tulos AS TEXT) ~ '^[0-9]+:[0-9]+([.][0-9]+)?$' THEN
                            CAST(SPLIT_PART(CAST(t.tulos AS TEXT), ':', 1) AS INTEGER) * 60 + 
                            CAST(SPLIT_PART(CAST(t.tulos AS TEXT), ':', 2) AS NUMERIC)
                        WHEN CAST(t.tulos AS TEXT) ~ '^[0-9]+([.][0-9]+)?$' THEN
                            CAST(t.tulos AS NUMERIC)
                        ELSE {default_value}
                    END AS tulos_numero,
                    ROW_NUMBER() OVER (
                        PARTITION BY u.urheilija_id 
                        ORDER BY 
                            CASE 
                                WHEN CAST(t.tulos AS TEXT) ~ '^[0-9]+:[0-9]+([.][0-9]+)?$' THEN
                                    CAST(SPLIT_PART(CAST(t.tulos AS TEXT), ':', 1) AS INTEGER) * 60 + 
                                    CAST(SPLIT_PART(CAST(t.tulos AS TEXT), ':', 2) AS NUMERIC)
                                WHEN CAST(t.tulos AS TEXT) ~ '^[0-9]+([.][0-9]+)?$' THEN
                                    CAST(t.tulos AS NUMERIC)
                                ELSE {default_value}
                            END {order_direction}
                    ) AS rn
                FROM Urheilijat u
                JOIN Tulokset t ON u.urheilija_id = t.urheilija_id
                LEFT JOIN Seurat s ON u.seura_id = s.seura_id
                JOIN Lajit l ON t.laji_id = l.laji_id
                JOIN Kilpailut k ON l.kilpailu_id = k.kilpailu_id
                WHERE l.lajin_nimi ILIKE %s 
                AND CAST(t.tulos AS TEXT) != 'DNS' 
                AND CAST(t.tulos AS TEXT) != 'DNF'
        """

        params = [f'%{laji}%']

        if sukupuoli in ['M', 'N']:
            sql += " AND u.sukupuoli = %s"
            params.append(sukupuoli)

        if vuosi is not None:
            sql += " AND EXTRACT(YEAR FROM k.alkupvm) = %s"
            params.append(vuosi)

        if ika_min is not None or ika_max is not None:
            sql += " AND k.alkupvm IS NOT NULL AND u.syntymavuosi IS NOT NULL"

            if ika_min is not None and ika_max is not None:
                sql += " AND (EXTRACT(YEAR FROM k.alkupvm) - u.syntymavuosi BETWEEN %s AND %s)"
                params.extend([ika_min, ika_max])
            elif ika_min is not None:
                sql += " AND (EXTRACT(YEAR FROM k.alkupvm) - u.syntymavuosi >= %s)"
                params.append(ika_min)
            elif ika_max is not None:
                sql += " AND (EXTRACT(YEAR FROM k.alkupvm) - u.syntymavuosi <= %s)"
                params.append(ika_max)

        sql += f"""
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
            ORDER BY tulos_numero {jarjestys}
            LIMIT 50
        """

        c.execute(sql, params)
        results = c.fetchall()

        tulokset = []
        for result in results:
            tulokset.append({
                'etunimi': result[0],
                'sukunimi': result[1],
                'seura': result[2],
                'tulos': result[3],
                'kilpailun_nimi': result[4],
                'alkupvm': result[5],
                'syntymavuosi': result[6],
                'sukupuoli': result[7],
                'sijoitus': result[8]
            })

        c.execute("""
            SELECT DISTINCT EXTRACT(YEAR FROM alkupvm) as vuosi
            FROM Kilpailut
            WHERE alkupvm IS NOT NULL
            ORDER BY vuosi DESC
        """)
        vuodet_results = c.fetchall()
        vuodet = [r[0] for r in vuodet_results]

        conn.close()

        return render_template('lajin_parhaat.html', 
                             laji=laji,
                             tulokset=tulokset,
                             sukupuoli=sukupuoli,
                             ika_min=ika_min,
                             ika_max=ika_max,
                             vuosi=vuosi,
                             vuodet=vuodet)
    except Exception as e:
        app.logger.error(f"Lajin parhaiden tulosten hakuvirhe: {str(e)}")
        return render_template('error.html', message='Tietokantavirhe'), 500

@app.route('/urheilijat')
def listaa_urheilijat():
    sukupuoli = request.args.get('sukupuoli', '').upper()
    ika_min = request.args.get('ika_min', type=int)
    ika_max = request.args.get('ika_max', type=int)

    current_year = datetime.now().year

    try:
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
            sql += " AND sukupuoli = %s"
            params.append(sukupuoli)

        sql += " AND sukupuoli IS NOT NULL AND syntymavuosi IS NOT NULL"

        c.execute(sql, params)
        kaikki_urheilijat = c.fetchall()

        unique_urheilijat = {}
        for urheilija in kaikki_urheilijat:
            avain = f"{urheilija[1].lower()}-{urheilija[2].lower()}-{urheilija[4]}"
            if avain not in unique_urheilijat:
                unique_urheilijat[avain] = {
                    'urheilija_id': urheilija[0],
                    'etunimi': urheilija[1],
                    'sukunimi': urheilija[2],
                    'sukupuoli': urheilija[3],
                    'syntymavuosi': urheilija[4]
                }

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
    except Exception as e:
        app.logger.error(f"Urheilijoiden hakuvirhe: {str(e)}")
        return render_template('error.html', message='Tietokantavirhe'), 500

@app.route('/lajit')
def listaa_lajit():
    try:
        conn = get_db_connection()
        c = conn.cursor()

        c.execute("""
            SELECT DISTINCT lajin_nimi 
            FROM Lajit
            ORDER BY lajin_nimi
        """)
        lajit_results = c.fetchall()
        lajit = [r[0] for r in lajit_results]

        conn.close()

        return render_template('lajit.html', lajit=lajit)
    except Exception as e:
        app.logger.error(f"Lajien hakuvirhe: {str(e)}")
        return render_template('error.html', message='Tietokantavirhe'), 500

if __name__ == '__main__':
    if not DATABASE_URL:
        print("Tietokantaosoitetta ei löydy ympäristömuuttujasta DATABASE_URL!")
        exit(1)

    try:
        conn = get_db_connection()
        print("Tietokantayhteys toimii!")
        conn.close()
    except Exception as e:
        print(f"Tietokantayhteys epäonnistui: {e}")
        print("Sovellus käynnistyy ilman tietokantayhteyttä")

    app.run(host='0.0.0.0', port=10000, debug=False)
