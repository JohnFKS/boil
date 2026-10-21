FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BOIL_HOST=0.0.0.0 \
    BOIL_PORT=7860 \
    BOIL_DATA_DIR=/app/data

WORKDIR /app

COPY . /app

RUN useradd --create-home --uid 10001 boil \
    && mkdir -p /app/data \
    && chown -R boil:boil /app

USER boil

EXPOSE 7860

VOLUME ["/app/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7860/api/health', timeout=3)" || exit 1

CMD ["python3", "run.py"]
