FROM python:3.12-slim

WORKDIR /app

# exiftool extracts the embedded preview JPEG from RAW files (NEF/ARW/CR2/etc)
# for the Controller-only session-photo thumbnail feature — the originals on
# the NAS Photo share are RAW-only, no sibling JPEGs exist to read directly.
RUN apt-get update && apt-get install -y --no-install-recommends libimage-exiftool-perl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "-u", "main.py"]
