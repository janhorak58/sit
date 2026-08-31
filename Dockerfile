FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg pulseaudio-utils curl unzip \
    && curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY transcriber/ ./transcriber/

EXPOSE 47831
CMD ["uvicorn", "transcriber.app:app", "--host", "0.0.0.0", "--port", "47831"]
