FROM python:3.11-slim-bookworm

WORKDIR /app

# Asenna järjestelmäriippuvuudet
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*
# Luo data-hakemisto
RUN mkdir -p /app/data

# Kopioi molemmat Python-skriptit
COPY requirements.txt .
COPY tulosten_haku.py .
COPY . .
# Asenna Python-riippuvuudet
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir beautifulsoup4 requests tabulate

# Luo data-hakemisto
RUN mkdir -p /app/data
RUN mkdir -p /app/templates

# Ympäristömuuttujat
ENV PYTHONUNBUFFERED=1
ENV TZ=Europe/Helsinki

# Oletuskomento (voit ajaa jomman kumman skriptin manuaalisesti)
CMD ["python", "./automaatti_haku.py" "./app.py"]