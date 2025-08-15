FROM python:3.11-slim-bookworm

WORKDIR /app

# Asenna vain välttämättömät riippuvuudet
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc python3-dev && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Kopioi ensin vaatimukset optimoidakseen kerroskäyttöä
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# Luo hakemistot ja kopioi loput tiedostot
RUN mkdir -p /app/data /app/templates
COPY . .
RUN chmod -R a+rw /app/data

# Määritä Gunicornille muistirajoitukset
CMD ["gunicorn", "-b", "0.0.0.0:8080", \
     "--workers", "2", \
     "--threads", "2", \
     "--worker-class", "gthread", \
     "--timeout", "30", \
     "--max-requests", "100", \
     "--max-requests-jitter", "20", \
     "app:app"]
