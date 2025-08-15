FROM python:3.11-slim-bookworm

WORKDIR /app

# Asenna riippuvuudet
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc python3-dev && \
    rm -rf /var/lib/apt/lists/*

# Luo tarvittavat hakemistot
RUN mkdir -p /app/data /app/templates
# Oikeudet data-kansiolle
RUN chmod -R a+rw /app/data
RUN chmod -R a+rw /app/templates

# Kopioi kaikki tiedostot (mukaan lukien data ja templates)
COPY . .

# Asenna Python-riippuvuudet
RUN pip install --no-cache-dir -r requirements.txt



# Ympäristömuuttujat
ENV PYTHONUNBUFFERED=1
ENV TZ=Europe/Helsinki
ENV FLASK_APP=app.py

# Suorituskomento
CMD ["gunicorn", "-b", "0.0.0.0:8080", "--workers", "2", "app:app"]
CMD python app.py


