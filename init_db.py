# init_db.py
import psycopg2
from urllib.parse import urlparse
import os

def get_connection():
    """Luo PostgreSQL-yhteys"""
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        raise Exception("DATABASE_URL environment variable is not set")
    
    url = urlparse(database_url)
    conn = psycopg2.connect(
        database=url.path[1:],
        user=url.username,
        password=url.password,
        host=url.hostname,
        port=url.port
    )
    return conn

def init_database():
    """Alustaa tietokantataulut OIKEILLA sarakenimillä"""
    print("Alustetaan tietokantataulut...")
    
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        # Poista vanhat taulut jos ovat olemassa (varalta)
        cursor.execute("DROP TABLE IF EXISTS Tulokset CASCADE")
        cursor.execute("DROP TABLE IF EXISTS Lajit CASCADE")
        cursor.execute("DROP TABLE IF EXISTS Urheilijat CASCADE")
        cursor.execute("DROP TABLE IF EXISTS Seurat CASCADE")
        cursor.execute("DROP TABLE IF EXISTS Kilpailut CASCADE")
        
        # Luodaan taulut SQLiten mukaisilla sarakenimillä
        cursor.execute("""
            CREATE TABLE Kilpailut (
                kilpailu_id SERIAL PRIMARY KEY,
                kilpailun_nimi VARCHAR(255) NOT NULL,
                paikkakunta VARCHAR(255),
                alkupvm DATE,
                loppupvm DATE,
                last_updated TIMESTAMP
            )
        """)
        
        cursor.execute("""
            CREATE TABLE Seurat (
                seura_id SERIAL PRIMARY KEY,
                seura_nimi VARCHAR(255) NOT NULL,
                paikkakunta VARCHAR(255),
                lyhenne VARCHAR(50)
            )
        """)
        
        cursor.execute("""
            CREATE TABLE Urheilijat (
                urheilija_id SERIAL PRIMARY KEY,
                etunimi VARCHAR(100) NOT NULL,
                sukunimi VARCHAR(100) NOT NULL,
                syntymapaiva DATE,
                syntymavuosi INTEGER,
                sukupuoli CHAR(1),
                seura_id INTEGER REFERENCES Seurat(seura_id)
            )
        """)
        
        cursor.execute("""
            CREATE TABLE Lajit (
                laji_id SERIAL PRIMARY KEY,
                kilpailu_id INTEGER REFERENCES Kilpailut(kilpailu_id),
                lajin_nimi VARCHAR(255) NOT NULL,
                sarja VARCHAR(100)
            )
        """)
        
        cursor.execute("""
            CREATE TABLE Tulokset (
                tulos_id SERIAL PRIMARY KEY,
                laji_id INTEGER REFERENCES Lajit(laji_id),
                urheilija_id INTEGER REFERENCES Urheilijat(urheilija_id),
                sijoitus INTEGER,
                tulos DECIMAL(10,3),
                reaktioaika DECIMAL(5,2),
                tuuli DECIMAL(4,2),
                lisatiedot TEXT,
                UNIQUE(laji_id, urheilija_id)
            )
        """)
        
        conn.commit()
        print("Tietokantataulut luotu onnistuneesti oikeilla sarakenimillä!")
        
    except Exception as e:
        conn.rollback()
        print(f"Virhe tietokannan alustamisessa: {e}")
        raise
    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    init_database()
