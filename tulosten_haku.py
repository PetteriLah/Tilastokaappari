import requests
import sqlite3
import os
from datetime import datetime
from dateutil.parser import parse
import argparse
import json
import re
import sys

# Asetukset
DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)
DATABASE_FILE = os.path.join(DATA_DIR, "kilpailut.db")

def clean_json_response(response_text):
    """Poistaa kommentit ja ei-JSON-merkit vastauksesta"""
    lines = [line for line in response_text.split('\n') if not line.strip().startswith('#')]
    cleaned_text = '\n'.join(lines)
    return cleaned_text.lstrip('\ufeff')

def create_database():
    """Luo tietokannan data-hakemistoon"""
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()
    
    # Enable foreign key constraints
    c.execute("PRAGMA foreign_keys = ON")
    
    # Create tables if they don't exist
    c.execute('''CREATE TABLE IF NOT EXISTS Kilpailut (
                 kilpailu_id INTEGER PRIMARY KEY,
                 kilpailun_nimi TEXT NOT NULL,
                 paikkakunta TEXT,
                 alkupvm DATE,
                 loppupvm DATE)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS Lajit (
                 laji_id INTEGER PRIMARY KEY,
                 kilpailu_id INTEGER NOT NULL,
                 lajin_nimi TEXT NOT NULL,
                 sarja TEXT,
                 FOREIGN KEY (kilpailu_id) REFERENCES Kilpailut(kilpailu_id))''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS Seurat (
                 seura_id INTEGER PRIMARY KEY AUTOINCREMENT,
                 seura_nimi TEXT NOT NULL UNIQUE,
                 paikkakunta TEXT,
                 lyhenne TEXT)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS Urheilijat (
                 urheilija_id INTEGER PRIMARY KEY AUTOINCREMENT,
                 etunimi TEXT NOT NULL,
                 sukunimi TEXT NOT NULL,
                 syntymapaiva DATE,
                 syntymavuosi INTEGER,
                 sukupuoli TEXT,
                 seura_id INTEGER,
                 FOREIGN KEY (seura_id) REFERENCES Seurat(seura_id))''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS Tulokset (
                 tulos_id INTEGER PRIMARY KEY AUTOINCREMENT,
                 laji_id INTEGER NOT NULL,
                 urheilija_id INTEGER NOT NULL,
                 sijoitus INTEGER,
                 tulos REAL,
                 reaktioaika REAL,
                 tuuli REAL,
                 lisatiedot TEXT,
                 UNIQUE(laji_id, urheilija_id),
                 FOREIGN KEY (laji_id) REFERENCES Lajit(laji_id),
                 FOREIGN KEY (urheilija_id) REFERENCES Urheilijat(urheilija_id))''')
    
    conn.commit()
    return conn

def extract_series_from_event_name(event_name):
    """Etsii ikäsarjan lajin nimestä"""
    if not event_name:
        return None
    series_match = re.search(r'\b([PTNM]\d{1,2})\b', event_name.upper())
    return series_match.group(1) if series_match else None

def parse_date(date_str):
    """Muuntaa päivämäärämerkkijonon SQLite-yhteensopivaan muotoon"""
    if not date_str:
        return None
    try:
        # Try ISO format first (YYYY-MM-DD)
        try:
            date_only = date_str.split('T')[0]
            dt = datetime.strptime(date_only, '%Y-%m-%d')
            return dt.strftime('%Y-%m-%d')
        except ValueError:
            # If ISO format fails, try Finnish format (DD.MM.YYYY)
            if '.' in date_str:
                dt = datetime.strptime(date_str, '%d.%m.%Y')
                return dt.strftime('%Y-%m-%d')
            # If neither format works, return None
            return None
    except Exception as e:
        print(f"Virhe päivämäärän jäsentämisessä: {date_str} - {str(e)}", file=sys.stderr)
        return None

def fetch_competition_info(competition_id):
    """Hakee kilpailun perustiedot"""
    api_url = f"https://cached-public-api.tuloslista.com/live/v1/competition/{competition_id}"
    try:
        response = requests.get(api_url, timeout=10)
        response.raise_for_status()
        data = json.loads(clean_json_response(response.text))
        
        # Etsi ensimmäinen virallinen kilpailupäivä
        competition_date = None
        for date_str, rounds in data.items():
            if date_str == "Competition":
                continue
            if isinstance(rounds, list):
                for round_data in rounds:
                    if isinstance(round_data, dict) and round_data.get('Status') == 'Official':
                        competition_date = date_str
                        break
                if competition_date:
                    break
        
        # Hae kilpailun perustiedot
        props_url = f"https://cached-public-api.tuloslista.com/live/v1/competition/{competition_id}/properties"
        props_response = requests.get(props_url, timeout=10)
        props_response.raise_for_status()
        props_data = json.loads(clean_json_response(props_response.text))
        
        # Oletusarvot jos tietoja ei löydy
        default_info = {
            'Name': f"Kilpailu {competition_id}",
            'Location': None,
            'StartDate': None,
            'EndDate': None
        }
        
        if props_data and 'Competition' in props_data:
            comp_data = props_data['Competition']
            return {
                'Name': comp_data.get('Name', default_info['Name']),
                'Location': comp_data.get('Location', default_info['Location']),
                'StartDate': parse_date(competition_date),  # Käytä löydettyä päivämäärää
                'EndDate': parse_date(competition_date)     # Sama päivä, koska meillä on vain yksi päivä
            }
        return default_info
    except Exception as e:
        print(f"Virhe kilpailun tietojen haussa: {str(e)}", file=sys.stderr)
        return {
            'Name': f"Kilpailu {competition_id}",
            'Location': None,
            'StartDate': None,
            'EndDate': None
        }

def parse_results(api_data, seura_filter=None):
    """Jäsentää tulokset API-vastauksesta"""
    if not api_data or not isinstance(api_data, dict) or 'Name' not in api_data:
        return "Tuntematon laji", []
    
    event_name = api_data.get('Name', 'Tuntematon laji')
    series = extract_series_from_event_name(event_name)
    results = []
    
    for round_data in api_data.get('Rounds', []):
        if not isinstance(round_data, dict) or 'TotalResults' not in round_data:
            continue
            
        for result in round_data['TotalResults']:
            if not isinstance(result, dict):
                continue
                
            # Käsitellään tulokset
            raw_result = str(result.get('Result', '')).strip().replace(',', '.')
            
            # Muunna aika yli 60 sekuntia oikeaan muotoon (esim. 1.29.94 -> 89.94)
            if '.' in raw_result and raw_result.count('.') > 1:
                parts = raw_result.split('.')
                if len(parts) == 3:  # Muoto: minuutit.sekunnit.sadasosat
                    try:
                        minutes = int(parts[0])
                        seconds = float(f"{parts[1]}.{parts[2]}")
                        total_seconds = minutes * 60 + seconds
                        processed_result = total_seconds
                    except ValueError:
                        processed_result = None
                else:
                    processed_result = None
            else:
                try:
                    processed_result = float(raw_result) if raw_result and raw_result.replace('.', '', 1).isdigit() else None
                except ValueError:
                    processed_result = None
            
            # Suodata seuran mukaan jos annettu
            org_data = result.get('Organization', {}) or {}
            seura_nimi = org_data.get('Name', '-') if isinstance(org_data, dict) else '-'
            if seura_filter and (seura_nimi == '-' or seura_nimi != seura_filter):
                continue
            
            # Hae sukupuoli jos saatavilla
            sukupuoli = result.get('Gender', None)
            if sukupuoli:
                sukupuoli = 'M' if str(sukupuoli).upper() == 'MALE' else 'N' if str(sukupuoli).upper() == 'FEMALE' else None
            
            results.append({
                'sijoitus': int(result.get('ResultRank', 0)) if str(result.get('ResultRank', '0')).isdigit() else 0,
                'nimi': str(result.get('Name', '')),
                'seura': str(seura_nimi),
                'tulos': processed_result,
                'tulos_teksti': str(raw_result),
                'sarja': series,
                'sukupuoli': sukupuoli,
                'syntymavuosi': int(result.get('BirthYear')) if str(result.get('BirthYear', '')).isdigit() else None
            })
    
    return event_name, results

# Lisää tämä funktio tulosten_haku.py:n alkuun (esim. create_database() funktion jälkeen)
def siisti_lajin_nimi(lajin_nimi):
    """Siistii lajin nimen poistamalla etuliitteet ja ylimääräiset tiedot"""
    if not lajin_nimi:
        return ""
    
    # Alkuperäinen nimi tallennetaan vertailua varten
    alkuperainen = lajin_nimi.strip()
    
    # Standardoidaan tunnetut lajit
    standard_lajit = {
        'pituus': 'Pituus',
        'kuula': 'Kuula',
        'keihäs': 'Keihäs',
        'korkeus': 'Korkeus',
        'seiväs': 'Seiväs'
    }
    
    # Tarkista onko laji yksi standardilajeista (case-insensitive)
    for key, value in standard_lajit.items():
        if key in alkuperainen.lower():
            return value
    
    # Erottele sanat ja käsittele jokainen erikseen
    words = alkuperainen.split()
    cleaned_words = []
    
    for word in words:
        # Poista pilkut sanan lopusta
        word = word.rstrip(',')
        
        # Tarkista onko sana M/N/T/P-etuliite
        if len(word) > 0 and word[0].upper() in ['M', 'N', 'T', 'P']:
            # Jos sana on pelkkä etuliite (esim. "M", "N") tai etuliite + numero (esim. "M17", "P15")
            if len(word) == 1 or word[1:].isdigit():
                continue  # Poista tämä sana
        cleaned_words.append(word)
    
    # Yhdistä sanat takaisin
    lajin_nimi = ' '.join(cleaned_words)
    
    # Poista suluissa olevat tiedot
    if '(' in lajin_nimi:
        lajin_nimi = lajin_nimi.split('(')[0].strip()
    
    # Poista "erä" ja "paikka" tiedot
    if 'erä' in lajin_nimi.lower() or 'paikka' in lajin_nimi.lower():
        lajin_nimi = lajin_nimi.split('(')[0].strip()
    
    # Erikoiskäsittely "ottelu"-sanoille
    if 'ottelu' in lajin_nimi.lower():
        words = lajin_nimi.split()
        cleaned_words = []
        for word in words:
            if 'ottelu' in word.lower():
                continue
            if '-' in word and any(c.isdigit() for c in word.split('-')[0]):
                continue
            cleaned_words.append(word)
        lajin_nimi = ' '.join(cleaned_words)
    
    # Poista ylimääräiset välilyönnit ja trimmaus
    lajin_nimi = ' '.join(lajin_nimi.split())
    
    # Jos lajinimi on tyhjä, palauta alkuperäinen
    if not lajin_nimi:
        return alkuperainen
    
    return lajin_nimi.strip()

# Muokkaa save_event_results funktiota (korvaa vanha lajin nimi käsittely):
def save_event_results(conn, competition_id, event_id, event_name, results):
    """Tallentaa tulokset tietokantaan ja päivittää last_updated-sarakkeen"""
    if not conn or not event_id or not event_name or not isinstance(results, list):
        return []
        
    c = conn.cursor()
    series = extract_series_from_event_name(event_name)
    athletes_data = []
    
    # Siistitään lajin nimi ennen tallennusta
    cleaned_event_name = siisti_lajin_nimi(event_name)
    
    try:
        # Hae kilpailun tiedot API:sta
        comp_info = fetch_competition_info(competition_id)
        
        # 1. Varmista että kilpailu on olemassa (päivitä myös last_updated)
        c.execute('''INSERT OR REPLACE INTO Kilpailut 
                     (kilpailu_id, kilpailun_nimi, paikkakunta, alkupvm, loppupvm, last_updated) 
                     VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)''',
                  (int(competition_id), 
                   str(comp_info['Name']),
                   str(comp_info['Location']) if comp_info['Location'] else None,
                   comp_info['StartDate'],
                   comp_info['EndDate']))
        
        # 2. Lisää laji (käytä nyt siistittyä nimeä)
        c.execute('''INSERT OR REPLACE INTO Lajit 
                     (laji_id, kilpailu_id, lajin_nimi, sarja)
                     VALUES (?, ?, ?, ?)''',
                  (int(event_id), int(competition_id), str(cleaned_event_name), str(series) if series else None))
        
        for result in results:
            if not isinstance(result, dict):
                continue
                
            # 3. Käsitellään seura
            seura_nimi = result.get('seura', '-') if result.get('seura', '-') != '-' else None
            seura_id = None
            
            if seura_nimi:
                c.execute('''INSERT OR IGNORE INTO Seurat (seura_nimi) VALUES (?)''', (str(seura_nimi),))
                c.execute('SELECT seura_id FROM Seurat WHERE seura_nimi = ?', (str(seura_nimi),))
                seura_row = c.fetchone()
                seura_id = int(seura_row[0]) if seura_row and str(seura_row[0]).isdigit() else None
            
            # 4. Erotetaan etu- ja sukunimi
            nimi = str(result.get('nimi', ''))
            nimet = nimi.split()
            etunimi = ' '.join(nimet[:-1]) if len(nimet) > 1 else ''
            sukunimi = nimet[-1] if nimet else ''
            
            if not etunimi or not sukunimi:
                continue
                
            # 5. Lisää urheilija
            c.execute('''INSERT OR REPLACE INTO Urheilijat 
                         (etunimi, sukunimi, sukupuoli, syntymavuosi, seura_id)
                         VALUES (?, ?, ?, ?, ?)''',
                      (str(etunimi), str(sukunimi), 
                       str(result.get('sukupuoli')) if result.get('sukupuoli') else None,
                       int(result.get('syntymavuosi')) if str(result.get('syntymavuosi', '')).isdigit() else None,
                       int(seura_id) if seura_id else None))
            
            # 6. Hae urheilija_id
            c.execute('''SELECT urheilija_id FROM Urheilijat 
                         WHERE etunimi = ? AND sukunimi = ?''',
                      (str(etunimi), str(sukunimi)))
            urheilija_row = c.fetchone()
            urheilija_id = int(urheilija_row[0]) if urheilija_row and str(urheilija_row[0]).isdigit() else None
            
            if not urheilija_id:
                print(f"Virhe: Urheilijaa ei löytynyt: {etunimi} {sukunimi}", file=sys.stderr)
                continue
                
            # 7. Lisää tulos
            try:
                c.execute('''INSERT OR REPLACE INTO Tulokset 
                             (laji_id, urheilija_id, sijoitus, tulos, lisatiedot)
                             VALUES (?, ?, ?, ?, ?)''',
                          (int(event_id), int(urheilija_id), 
                           int(result.get('sijoitus', 0)) if str(result.get('sijoitus', '0')).isdigit() else 0,
                           float(result.get('tulos')) if result.get('tulos') is not None else None,
                           str(result.get('tulos_teksti', ''))))
            except (sqlite3.IntegrityError, ValueError) as e:
                print(f"Virhe tuloksen lisäämisessä (laji_id={event_id}, urheilija_id={urheilija_id}): {str(e)}", file=sys.stderr)
                continue
            
            athletes_data.append({
                'id': urheilija_id,
                'name': f"{etunimi} {sukunimi}",
                'series': series
            })
        
        conn.commit()
    
    except Exception as e:
        conn.rollback()
        print(f"Virhe tallennettaessa tuloksia: {str(e)}", file=sys.stderr)
    
    return athletes_data 

def print_results_by_series(conn, competition_id, seura_filter=None):
    """Tulostaa tulokset ryhmiteltynä sarjoittain"""
    if not conn:
        return
        
    c = conn.cursor()
    
    try:
        # Hae kilpailun tiedot
        c.execute('''SELECT kilpailun_nimi, paikkakunta, alkupvm, loppupvm 
                     FROM Kilpailut WHERE kilpailu_id = ?''', (int(competition_id),))
        competition_row = c.fetchone()
        
        # Jos tietoja ei ole tietokannassa, hae API:sta
        if not competition_row or not competition_row[2] or not competition_row[3]:
            comp_info = fetch_competition_info(competition_id)
            competition_name = comp_info['Name']
            location = comp_info['Location']
            start_date = comp_info['StartDate']
            end_date = comp_info['EndDate']
            
            # Päivitä tietokanta
            c.execute('''UPDATE Kilpailut 
                         SET kilpailun_nimi = ?, paikkakunta = ?, alkupvm = ?, loppupvm = ?
                         WHERE kilpailu_id = ?''',
                      (str(competition_name), str(location) if location else None,
                       start_date, 
                       end_date,
                       int(competition_id)))
            conn.commit()
        else:
            competition_name, location, start_date, end_date = competition_row
        
        # Muotoile päivämäärät
        date_info = ""
        if start_date:
            try:
                start_dt = datetime.strptime(start_date, '%Y-%m-%d')
                start_str = start_dt.strftime('%d.%m.%Y')
                if end_date and end_date != start_date:
                    end_dt = datetime.strptime(end_date, '%Y-%m-%d')
                    end_str = end_dt.strftime('%d.%m.%Y')
                    date_info = f" - {start_str}-{end_str}"
                else:
                    date_info = f" - {start_str}"
            except:
                date_info = ""
        
        print(f"\n{'='*50}")
        if seura_filter:
            print(f"TULOKSET - {competition_name}{f' ({location})' if location else ''}{date_info}")
            print(f"Seura: {seura_filter}")
        else:
            print(f"TULOKSET - {competition_name}{f' ({location})' if location else ''}{date_info} (kaikki seurat)")
        
        # Hae lajit ja sarjat
        query = '''SELECT DISTINCT l.laji_id, l.lajin_nimi, l.sarja 
                   FROM Lajit l
                   JOIN Tulokset t ON l.laji_id = t.laji_id
                   WHERE l.kilpailu_id = ?'''
        params = (int(competition_id),)
        
        if seura_filter:
            query += ''' AND t.urheilija_id IN (
                          SELECT u.urheilija_id FROM Urheilijat u
                          JOIN Seurat s ON u.seura_id = s.seura_id
                          WHERE s.seura_nimi = ?)'''
            params = (int(competition_id), str(seura_filter))
        
        c.execute(query, params)
        events = c.fetchall()
        
        total_athletes = 0
        for event in events:
            if not event or len(event) < 3:
                continue
                
            event_id, event_name, series = event
            print(f"\n{'='*30}")
            print(f"Laji: {str(event_name)}")
            if series:
                print(f"Sarja: {str(series)}")
            print('-'*30)
            
            # Hae tulokset
            query = '''SELECT u.etunimi, u.sukunimi, t.sijoitus, t.tulos, s.seura_nimi
                       FROM Tulokset t
                       JOIN Urheilijat u ON t.urheilija_id = u.urheilija_id
                       LEFT JOIN Seurat s ON u.seura_id = s.seura_id
                       WHERE t.laji_id = ?'''
            params = (int(event_id),)
            
            if seura_filter:
                query += ' AND s.seura_nimi = ?'
                params = (int(event_id), str(seura_filter))
            
            query += ' ORDER BY t.sijoitus'
            
            c.execute(query, params)
            results = c.fetchall()
            
            for result in results:
                if not result or len(result) < 5:
                    continue
                    
                etunimi, sukunimi, sijoitus, tulos, seura_nimi = result
                print(f"{int(sijoitus)}. {str(etunimi)} {str(sukunimi)} ({str(seura_nimi) if seura_nimi else '-'}): {float(tulos) if tulos is not None else '-'}")
            
            total_athletes += len(results)
        
        print(f"\nYhteensä {total_athletes} urheilijaa")
        print(f"{'='*50}")
    
    except Exception as e:
        print(f"Virhe tulosten näyttämisessä: {str(e)}", file=sys.stderr)

def main():
    parser = argparse.ArgumentParser(description='Hae kilpailutulokset')
    parser.add_argument('--seura', type=str, help='Suodata seuran mukaan', default=None)
    parser.add_argument('--id', type=int, help='Kilpailun ID', required=True)
    args = parser.parse_args()
    
    try:
        # Alusta tietokanta
        conn = create_database()
        print(f"Tietokanta sijaitsee: {os.path.abspath(DATABASE_FILE)}", file=sys.stderr)
        
        # Hae kilpailun tiedot
        competition_info = fetch_competition_info(args.id)
        print(f"\nHaetaan tulokset kilpailulle: {competition_info['Name']} (ID: {args.id})")
        if competition_info['Location']:
            print(f"Paikkakunta: {competition_info['Location']}")
        if competition_info['StartDate']:
            start_str = datetime.strptime(competition_info['StartDate'], '%Y-%m-%d').strftime('%d.%m.%Y')
            if competition_info['EndDate'] and competition_info['EndDate'] != competition_info['StartDate']:
                end_str = datetime.strptime(competition_info['EndDate'], '%Y-%m-%d').strftime('%d.%m.%Y')
                print(f"Ajankohta: {start_str} - {end_str}")
            else:
                print(f"Päivämäärä: {start_str}")
        
        # Hae kilpailun kierrokset
        try:
            response = requests.get(
                f"https://cached-public-api.tuloslista.com/live/v1/competition/{args.id}",
                timeout=10
            )
            response.raise_for_status()
            competition_rounds = json.loads(clean_json_response(response.text))
        except Exception as e:
            print(f"Virhe kilpailun kierrosten haussa: {str(e)}", file=sys.stderr)
            sys.exit(1)
        
        if not isinstance(competition_rounds, dict):
            print("Virhe: Kilpailun kierrosten data on virheellisessä muodossa", file=sys.stderr)
            sys.exit(1)
        
        athletes_data = []
        for date_str, rounds in competition_rounds.items():
            if date_str == "Competition" or not isinstance(rounds, list):
                continue
                
            for round_data in rounds:
                if not isinstance(round_data, dict) or round_data.get('Status') != 'Official':
                    continue
                    
                event_id = round_data.get('EventId')
                event_name = round_data.get('EventName', 'Tuntematon laji')
                
                if not event_id:
                    continue
                    
                # Hae tapahtuman tulokset
                try:
                    response = requests.get(
                        f"https://cached-public-api.tuloslista.com/live/v1/results/{args.id}/{event_id}",
                        timeout=10
                    )
                    response.raise_for_status()
                    event_results = json.loads(clean_json_response(response.text))
                    
                    event_name, results = parse_results(event_results, args.seura)
                    if results:
                        athletes = save_event_results(conn, args.id, event_id, event_name, results)
                        if athletes:
                            athletes_data.extend(athletes)
                except Exception as e:
                    print(f"Virhe haettaessa tuloksia lajille {event_name}: {str(e)}", file=sys.stderr)
                    continue
        
        # Tulosta tulokset
        print_results_by_series(conn, args.id, args.seura)
        
        print(f"\nTiedot tallennettu tietokantaan: {os.path.abspath(DATABASE_FILE)}")
        
    except Exception as e:
        print(f"\nVIRHE: {str(e)}", file=sys.stderr)
        sys.exit(1)
    finally:
        if 'conn' in locals():
            conn.close()

if __name__ == "__main__":
    main()
