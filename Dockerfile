FROM python:3.11-slim-bookworm

WORKDIR /app

# Asenna riippuvuudet
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc python3-dev && \
    rm -rf /var/lib/apt/lists/*

# Luo hakemistot ja kopioi tiedostot
RUN mkdir -p /app/data /app/templates
COPY . .
RUN pip install --no-cache-dir -r requirements.txt gunicorn
RUN chmod -R a+rw /app/data

# Suorituskomento
CMD ["gunicorn", "-b", "0.0.0.0:8080", "--workers", "2", "app:app"]
