FROM python:3.11-slim-bookworm

WORKDIR /app

# Asenna riippuvuudet
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc python3-dev && \
    rm -rf /var/lib/apt/lists/*

# Luo tarvittavat hakemistot
RUN mkdir -p /app/data && chmod -R a+rw /app/data
RUN mkdir -p /app/templates

# Kopioi ensin vaatimukset
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# Kopioi kaikki sovellustiedostot
COPY . .

# Ympäristömuuttujat
ENV PYTHONUNBUFFERED=1
ENV TZ=Europe/Helsinki
ENV PORT=8080

# Suorituskomento
CMD ["gunicorn", "-b", "0.0.0.0:8080", "--workers", "2", "app:app"]
