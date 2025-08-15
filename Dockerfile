FROM python:3.11-slim-bookworm

WORKDIR /app

# Asenna riippuvuudet
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc python3-dev && \
    rm -rf /var/lib/apt/lists/*

# Luo tarvittavat hakemistot
RUN mkdir -p /app/data /app/templates

# Kopioi ensin vaatimukset (optimoi build cachea)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# Kopioi ensin templates-kansio erikseen
COPY templates/ /app/templates/

# Kopioi loput tiedostot
COPY . .

# Aseta oikeudet
RUN chmod -R a+rw /app/data

# Ympäristömuuttujat
ENV PYTHONUNBUFFERED=1
ENV TZ=Europe/Helsinki
ENV FLASK_APP=app.py

# Suorituskomento
CMD ["gunicorn", "-b", "0.0.0.0:8080", "--workers", "2", "app:app"]
