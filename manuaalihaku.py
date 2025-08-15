import subprocess
import time
import os
import sqlite3
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import requests

def tarkista_tapahtuma_id(tapahtuma_id):
    """Tarkistaa onko tapahtuma jo käsitelty tai onko se olemassa"""
    # Tarkista ensin API:sta onko tapahtuma olemassa
    try:
        response = requests.get(
            f"https://cached-public-api.tuloslista.com/live/v1/competition/{tapahtuma_id}/properties",
            timeout=5
        )
        if response.status_code == 404:
            return False  # Tapahtumaa ei ole olemassa
    except requests.RequestException:
        pass  # Jatketaan tietokantatarkistukseen
    
    # Tarkista tietokannasta onko tapahtuma jo käsitelty
    db_path = os.path.join("data", "kilpailut.db")
    if not os.path.exists(db_path):
        return True
        
    try:
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        c.execute("SELECT 1 FROM Kilpailut WHERE kilpailu_id = ?", (tapahtuma_id,))
        return c.fetchone() is None
    except sqlite3.Error as e:
        print(f"Tietokantavirhe ID:llä {tapahtuma_id}: {str(e)}")
        return True
    finally:
        if 'conn' in locals():
            conn.close()

def suorita_tulosten_haku(tapahtuma_id, organisaatio_nimi):
    """Suorittaa tulosten_haku.py-skriptin"""
    # Tarkista onko tapahtuma olemassa tai onko se jo käsitelty
    if not tarkista_tapahtuma_id(tapahtuma_id):
        with open("tulosten_haku_ohitetut.txt", "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - Ohitettu ID: {tapahtuma_id}\n")
        return

    try:
        tulos = subprocess.run(
            ["python", "tulosten_haku.py", "--id", str(tapahtuma_id), "--seura", organisaatio_nimi],
            check=True,
            text=True,
            capture_output=True,
            timeout=60
        )
        
        # Tallenna vain jos tuloksia löytyi
        if "Ei tuloksia" not in tulos.stdout:
            loki = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - ID: {tapahtuma_id} - Seura: {organisaatio_nimi}\n"
            loki += f"Tulos: {tulos.stdout}\n"
            
            with open("tulosten_haku_loki.txt", "a", encoding="utf-8") as f:
                f.write(loki + "\n")
                
            print(f"Tallennettu ID: {tapahtuma_id} (Seura: {organisaatio_nimi})")
        else:
            with open("tulosten_haku_eituloksia.txt", "a", encoding="utf-8") as f:
                f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - Ei tuloksia ID: {tapahtuma_id}\n")
            print(f"Ei tuloksia ID:llä {tapahtuma_id}")

    except subprocess.TimeoutExpired:
        with open("tulosten_haku_aikakatkaisut.txt", "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - Aikakatkaisu ID: {tapahtuma_id}\n")
    except subprocess.CalledProcessError as e:
        virhe = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - Virhe ID: {tapahtuma_id}"
        
        # Käsittele 404 virheet erikseen
        if "404 Client Error" in (e.stderr or ""):
            with open("tulosten_haku_eiloydy.txt", "a", encoding="utf-8") as f:
                f.write(f"{virhe} - Tapahtumaa ei löydy\n")
            print(f"Tapahtumaa ei löydy ID:llä {tapahtuma_id}")
        else:
            with open("tulosten_haku_virheet.txt", "a", encoding="utf-8") as f:
                f.write(f"{virhe} - {e.stderr if e.stderr else 'Tuntematon virhe'}\n")
            print(f"Virhe ID:llä {tapahtuma_id}")
    except Exception as e:
        with open("tulosten_haku_virheet.txt", "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - Odottamaton virhe ID: {tapahtuma_id} - {str(e)}\n")
        print(f"Odottamaton virhe ID:llä {tapahtuma_id}")

def main():
    # Kysy käyttäjältä organisaation nimi
    organisaatio_nimi = input("Anna seura, jonka tulokset haetaan: ").strip()
    if not organisaatio_nimi:
        print("Seuran nimi ei voi olla tyhjä!")
        return

    # Kysy ID-väli
    print("\nAnna ID-väli (esim. 16671-17674):")
    try:
        min_id = int(input("Pienin ID: ").strip())
        max_id = int(input("Suurin ID: ").strip())
        
        if min_id > max_id:
            print("Virhe: Pienin ID ei voi olla suurempi kuin suurin ID")
            return
    except ValueError:
        print("Virhe: ID:t pitää olla kokonaislukuja")
        return

    # Kysy säikeiden määrä
    try:
        max_workers = int(input("\nAnna säikeiden määrä (1-10, suositus 3-5): ").strip())
        max_workers = max(1, min(10, max_workers))
    except ValueError:
        max_workers = 3
        print("Käytetään oletusarvoa (3 säiettä)")

    # Alusta lokitiedostot
    for tiedosto in [
        "tulosten_haku_loki.txt",
        "tulosten_haku_virheet.txt",
        "tulosten_haku_eiloydy.txt",
        "tulosten_haku_ohitetut.txt",
        "tulosten_haku_aikakatkaisut.txt",
        "tulosten_haku_eituloksia.txt"
    ]:
        with open(tiedosto, "a", encoding="utf-8") as f:
            f.write(f"\n\n=== Uusi ajokerta {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===\n")
            f.write(f"ID-väli: {min_id}-{max_id}, Seura: {organisaatio_nimi}\n\n")

    # Luo lista kaikista ID:istä annetulla välillä
    tapahtuma_idt = list(range(min_id, max_id + 1))
    print(f"\nKäsitellään {len(tapahtuma_idt)} tapahtumaa (ID:t {min_id}-{max_id})")
    print(f"Käytetään {max_workers} säiettä")
    print("Lokitiedot tallennetaan eri tiedostoihin:")
    print("- tulosten_haku_loki.txt: Onnistuneet haut")
    print("- tulosten_haku_eituloksia.txt: Tapahtumat ilman tuloksia")
    print("- tulosten_haku_eiloydy.txt: Tapahtumia joita ei löydy")
    print("- tulosten_haku_ohitetut.txt: Jo käsitellyt tapahtumat")
    print("- tulosten_haku_virheet.txt: Muut virheet")
    print("- tulosten_haku_aikakatkaisut.txt: Aikakatkaisut\n")

    # Suorita kutsut hallitusti
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for tapahtuma_id in tapahtuma_idt:
            executor.submit(suorita_tulosten_haku, tapahtuma_id, organisaatio_nimi)
            time.sleep(0.5)  # 500ms viive kutsujen välissä

    print("\nKaikki tapahtumat käsitelty")

if __name__ == "__main__":
    main()