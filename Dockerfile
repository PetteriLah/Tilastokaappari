FROM python:3.11-slim-bookworm

WORKDIR /app

# Asenna riippuvuudet
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc python3-dev && \
    rm -rf /var/lib/apt/lists/*

# Luo tarvittavat hakemistot
RUN mkdir -p /app/data /app/templates

# Aseta oikeudet
RUN chmod -R a+rw /app/data
RUN chmod -R a+rw /app/templates

# Kopioi riippuvuudet ensin
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# Kopioi sovelluksen tiedostot
COPY . .


# Ympäristömuuttujat
ENV PYTHONUNBUFFERED=1
ENV TZ=Europe/Helsinki
ENV FLASK_APP=app.py

# Suorituskomento
CMD ["python", "app.py"]
