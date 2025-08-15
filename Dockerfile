FROM python:3.11-slim-bookworm

WORKDIR /app

# Asenna järjestelmäriippuvuudet
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Luo tarvittavat hakemistot
RUN mkdir -p /app/data && mkdir -p /app/templates

# Kopioi kaikki tiedostot
COPY . .

# Asenna Python-riippuvuudet
RUN pip install --no-cache-dir -r requirements.txt

# Ympäristömuuttujat
ENV PYTHONUNBUFFERED=1
ENV TZ=Europe/Helsinki

# Suorita Flask-sovellus
CMD python app.py
