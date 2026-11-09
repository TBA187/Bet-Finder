# BetFinder production image
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY data/registry ./data/registry
COPY main.py .

# runtime data (odds history, settings, tracker) lives on a volume
RUN useradd --create-home betfinder && mkdir -p /app/data/runtime && chown -R betfinder /app/data
USER betfinder
VOLUME ["/app/data/runtime"]

EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')"
# one worker on purpose: the JSON storage is designed for a single process
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
