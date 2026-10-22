FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/data/models \
    XDG_CACHE_HOME=/data/cache

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app

RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin suite \
    && mkdir -p /data/uploads /data/chunks /data/outputs /data/tmp /data/markitdown/uploads /data/markitdown/outputs \
    && chown -R suite:suite /data /app

# Corre sin root. En compose se puede pisar con SUITE_UID/SUITE_GID para que coincida con el duenio de ./data
USER suite

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
