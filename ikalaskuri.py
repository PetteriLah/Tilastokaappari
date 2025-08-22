import psycopg2
from datetime import datetime
import re
from collections import defaultdict
import logging
import os
from psycopg2.extras import DictCursor

# Logituksen asetukset
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    filename='ikalaskuri.log',
    filemode='w'
)
logger = logging.getLogger(__name__)

# Tietokantayhteys
DATABASE_URL = os.environ.get('DATABASE_URL')

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def get_athlete_data(urheilija_id):
    """Hakee urheilijan kaikki tiedot tietokannasta"""
    logger.debug(f"Haetaan urheilijan {urheilija_id} tiedot")
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    
    try:
        # Hae urheilijan perustiedot
        cursor.execute("""
            SELECT urheilija_id, etunimi, sukunimi, 
                   syntymapaiva, syntymavuosi, sukupuoli, seura_id
            FROM Urheilijat 
            WHERE urheilija_id = %s
        """, (urheilija_id,))
        athlete_data = cursor.fetchone()
        
        if not athlete_data:
            logger.warning(f"Urheilijaa ID {urheilija_id} ei löytynyt")
            return None, []
        
        # Muotoile urheilijan perustiedot
        athlete = {
            'urheilija_id': athlete_data['urheilija_id'],
            'etunimi': athlete_data['etunimi'],
            'sukunimi': athlete_data['sukunimi'],
            'syntymapaiva': athlete_data['syntymapaiva'],
            'syntymavuosi': athlete_data['syntymavuosi'],
            'sukupuoli': athlete_data['sukupuoli'],
            'seura_id': athlete_data['seura_id']
        }
        
        logger.debug(f"Urheilijan perustiedot: {athlete}")
        
        # Hae urheilijan kaikki tulokset
        cursor.execute("""
            SELECT t.tulos_id, l.laji_id, l.lajin_nimi, l.sarja,
                   k.kilpailu_id, k.kilpailun_nimi, k.alkupvm
            FROM Tulokset t
            JOIN Lajit l ON t.laji_id = l.laji_id
            JOIN Kilpailut k ON l.kilpailu_id = k.kilpailu_id
            WHERE t.urheilija_id = %s
            ORDER BY k.alkupvm
        """, (urheilija_id,))
        
        results = cursor.fetchall()
        logger.debug(f"Löytyi {len(results)} kilpailutulosta urheilijalle {urheilija_id}")
        
        # Muotoile tulokset
        competitions = []
        for row in results:
            competitions.append({
                'tulos_id': row['tulos_id'],
                'laji_id': row['laji_id'],
                'lajin_nimi': row['lajin_nimi'],
                'sarja': row['sarja'],
                'kilpailu_id': row['kilpailu_id'],
                'kilpailun_nimi': row['kilpailun_nimi'],
                'alkupvm': row['alkupvm']
            })
            logger.debug(f"Kilpailutulos: Laji={row['lajin_nimi']}, Sarja={row['sarja']}, Pvm={row['alkupvm']}")
        
        return athlete, competitions
    
    except Exception as e:
        logger.error(f"Virhe haettaessa urheilijan {urheilija_id} tietoja: {str(e)}")
        return None, []
    finally:
        conn.close()

def parse_age_group(sarja):
    """Jäsentää ikäsarjan ja palauttaa sukupuolen ja ikävuoden"""
    if not sarja:
        logger.debug("Sarja on tyhjä")
        return None, None
    
    # Poista mahdolliset välilyönnit ja muuta isoiksi kirjaimiksi
    sarja = sarja.strip().upper()
    logger.debug(f"Jäsennetään sarja: {sarja}")
    
    # Tunnista sukupuoli
    sukupuoli = None
    if sarja.startswith(('T', 'N')):
        sukupuoli = 'N'
    elif sarja.startswith(('P', 'M')):
        sukupuoli = 'M'
    
    # Etsi ikä numeroina (saa olla 1-2 numeroa)
    ika_match = re.search(r'(\d{1,2})', sarja)
    if not ika_match:
        logger.debug(f"Ei löytynyt ikää sarjasta: {sarja}")
        return sukupuoli, None
    
    ika = int(ika_match.group(1))
    logger.debug(f"Jäsennetty sarja: sukupuoli={sukupuoli}, ikä={ika}")
    return sukupuoli, ika

def determine_birth_year(athlete, competitions):
    """Päättelee syntymävuoden kilpailuista ja sarjoista"""
    logger.debug("Aloitetaan syntymävuoden päätteleminen")
    
    # Kerää kaikki ikäsarjat ja kilpailuvuodet
    age_data = []
    for comp in competitions:
        if not comp.get('sarja') or not comp.get('alkupvm'):
            continue
        
        try:
            # Päivämäärän käsittely
            if isinstance(comp['alkupvm'], str):
                kilpailu_pvm = datetime.strptime(comp['alkupvm'], '%Y-%m-%d')
            else:
                kilpailu_pvm = comp['alkupvm']
            kilpailu_vuosi = kilpailu_pvm.year
            
            # Sarjan ikäluokan käsittely
            sukupuoli, ika = parse_age_group(comp['sarja'])
            if ika is None:
                continue
                
            syntymavuosi = kilpailu_vuosi - ika
            age_data.append((syntymavuosi, ika, comp['sarja'], kilpailu_vuosi))
            
        except Exception as e:
            logger.error(f"Virhe datan jäsentämisessä: {e}")
            continue
    
    if not age_data:
        logger.debug("Ei kelvollisia ikätietoja")
        return athlete.get('syntymavuosi')  # Palauta nykyinen jos ei uutta tietoa
    
    # Etsi kaikista nuorin mahdollinen syntymävuosi (suurin luku)
    uusi_syntymavuosi = max(syntymavuosi for syntymavuosi, _, _, _ in age_data)
    
    # Vertaa nykyiseen syntymävuoteen
    nykyinen = athlete.get('syntymavuosi')
    if nykyinen is None:
        return uusi_syntymavuosi
    elif uusi_syntymavuosi > nykyinen:
        logger.debug(f"Löydetty nuorempi syntymävuosi: {uusi_syntymavuosi} > {nykyinen}")
        return uusi_syntymavuosi
    
    return nykyinen

def determine_gender(athlete, competitions):
    """Päättelee sukupuolen sarjojen perusteella"""
    logger.debug("Aloitetaan sukupuolen päätteleminen")
    
    # Jos sukupuoli on jo tiedossa, palauta se
    if athlete.get('sukupuoli') in ['M', 'N']:
        logger.debug(f"Käytetään jo olemassa olevaa sukupuolta: {athlete['sukupuoli']}")
        return athlete['sukupuoli']
    
    # Kerää kaikki sukupuolitiedot sarjoista
    gender_data = []
    for comp in competitions:
        logger.debug(f"Käsitellään kilpailua sukupuolen päättelemiseksi: {comp}")
        
        if not comp.get('sarja'):
            logger.debug("Ei sarjaa, ohitetaan")
            continue
        
        sukupuoli, _ = parse_age_group(comp['sarja'])
        if sukupuoli in ['M', 'N']:
            gender_data.append(sukupuoli)
            logger.debug(f"Lisätty sukupuoli {sukupuoli} gender_dataan")
    
    if not gender_data:
        logger.debug("Ei sukupuolitietoja sarjoista")
        return None
    
    # Tarkista johdonmukaisuus
    unique_genders = set(gender_data)
    if len(unique_genders) == 1:
        result = unique_genders.pop()
        logger.debug(f"Kaikki sarjat samaa sukupuolta: {result}")
        return result
    else:
        # Jos ristiriitaisia tietoja, valitaan yleisin
        from collections import Counter
        result = Counter(gender_data).most_common(1)[0][0]
        logger.debug(f"Ristiriitaisia sukupuolitietoja, valittiin yleisin: {result}")
        return result

def update_athlete_info(urheilija_id, syntymavuosi=None, sukupuoli=None):
    """Päivittää urheilijan tiedot tietokantaan"""
    logger.debug(f"Päivitetään urheilijan {urheilija_id} tietoja: syntymavuosi={syntymavuosi}, sukupuoli={sukupuoli}")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Hae nykyiset tiedot
        cursor.execute("SELECT syntymavuosi, sukupuoli FROM Urheilijat WHERE urheilija_id = %s", (urheilija_id,))
        row = cursor.fetchone()
        current_syntymavuosi, current_sukupuoli = row if row else (None, None)
        
        updates = []
        params = []
        
        # Päivitä syntymävuosi jos uusi on suurempi (nuorempi ikä)
        if syntymavuosi is not None:
            if current_syntymavuosi is None or syntymavuosi > current_syntymavuosi:
                updates.append("syntymavuosi = %s")
                params.append(syntymavuosi)
        
        # Päivitä sukupuoli vain jos sitä ei ole
        if sukupuoli is not None and current_sukupuoli is None:
            updates.append("sukupuoli = %s")
            params.append(sukupuoli)
        
        if updates:
            params.append(urheilija_id)
            query = f"UPDATE Urheilijat SET {', '.join(updates)} WHERE urheilija_id = %s"
            cursor.execute(query, params)
            conn.commit()
            logger.info(f"Päivitetty urheilija {urheilija_id}")
            return True
        
        logger.debug("Ei päivitystarvetta")
        return False
    
    except Exception as e:
        logger.error(f"Virhe päivitettäessä urheilijaa {urheilija_id}: {str(e)}")
        return False
    finally:
        conn.close()

def process_athlete(urheilija_id):
    """Käsittelee yksittäisen urheilijan tiedot"""
    logger.info(f"\n{'='*50}")
    logger.info(f"Käsitellään urheilijaa ID: {urheilija_id}")
    
    athlete, competitions = get_athlete_data(urheilija_id)
    if not athlete:
        logger.error(f"Urheilijaa ID {urheilija_id} ei löydy")
        return
    
    athlete_name = f"{athlete['etunimi']} {athlete['sukunimi']}"
    logger.info(f"Urheilija: {athlete_name}")
    logger.debug(f"Kilpailut: {len(competitions)} kpl")
    
    # Päättele sukupuoli
    sukupuoli = determine_gender(athlete, competitions)
    
    # Päättele syntymävuosi
    syntymavuosi = determine_birth_year(athlete, competitions)
    
    # Päivitä tietokanta
    updated = update_athlete_info(urheilija_id, syntymavuosi, sukupuoli)
    
    # Tulosta yhteenveto
    logger.info("\nYhteenveto:")
    logger.info(f"Nykyiset tiedot: syntymavuosi={athlete.get('syntymavuosi')}, sukupuoli={athlete.get('sukupuoli')}")
    logger.info(f"Päätellyt tiedot: syntymavuosi={syntymavuosi}, sukupuoli={sukupuoli}")
    logger.info(f"Tietokanta päivitetty: {'Kyllä' if updated else 'Ei'}")

def main():
    logger.info("Urheilijan iän ja sukupuolen päätelyohjelma")
    logger.info("=========================================")
    
    # Hae kaikki urheilijat
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            SELECT urheilija_id FROM Urheilijat 
            ORDER BY urheilija_id
        """)
        
        athlete_ids = [row[0] for row in cursor.fetchall()]
        
        if not athlete_ids:
            logger.info("Ei urheilijoita tietokannassa")
            return
        
        logger.info(f"Käsiteltäviä urheilijoita: {len(athlete_ids)}")
        
        for urheilija_id in athlete_ids:
            process_athlete(urheilija_id)
    
    except Exception as e:
        logger.error(f"Virhe pääohjelmassa: {str(e)}")
    finally:
        conn.close()

if __name__ == "__main__":
    main()
