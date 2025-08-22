import os
import psycopg2
from psycopg2 import sql

# Yhdistä Renderin PostgreSQL-tietokantaan
DATABASE_URL = os.environ.get('DATABASE_URL')

def init_database():
    conn = psycopg2.connect(DATABASE_URL)
    cursor = conn.cursor()
    
    # Luo taulut
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Kilpailut (
            kilpailu_id INTEGER PRIMARY KEY,
            kilpailun_nimi TEXT NOT NULL,
            paikkakunta TEXT,
            alkupvm DATE,
            loppupvm DATE,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Lajit (
            laji_id INTEGER PRIMARY KEY,
            kilpailu_id INTEGER NOT NULL,
            lajin_nimi TEXT NOT NULL,
            sarja TEXT,
            FOREIGN KEY (kilpailu_id) REFERENCES Kilpailut(kilpailu_id)
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Seurat (
            seura_id SERIAL PRIMARY KEY,
            seura_nimi TEXT NOT NULL UNIQUE,
            paikkakunta TEXT,
            lyhenne TEXT
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Urheilijat (
            urheilija_id SERIAL PRIMARY KEY,
            etunimi TEXT NOT NULL,
            sukunimi TEXT NOT NULL,
            syntymapaiva DATE,
            syntymavuosi INTEGER,
            sukupuoli TEXT,
            seura_id INTEGER,
            FOREIGN KEY (seura_id) REFERENCES Seurat(seura_id)
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Tulokset (
            tulos_id SERIAL PRIMARY KEY,
            laji_id INTEGER NOT NULL,
            urheilija_id INTEGER NOT NULL,
            sijoitus INTEGER,
            tulos REAL,
            reaktioaika REAL,
            tuuli REAL,
            lisatiedot TEXT,
            UNIQUE(laji_id, urheilija_id),
            FOREIGN KEY (laji_id) REFERENCES Lajit(laji_id),
            FOREIGN KEY (urheilija_id) REFERENCES Urheilijat(urheilija_id)
        )
    ''')
    
    conn.commit()
    conn.close()
    print("Tietokanta alustettu onnistuneesti!")

if __name__ == "__main__":
    init_database()
