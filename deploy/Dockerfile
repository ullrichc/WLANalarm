# Bauen (aus dem Projektwurzelverzeichnis):
#   docker build -f deploy/Dockerfile -t wlanalarm .
FROM python:3.12-slim

# Kein Root im Container - der Dienst braucht keinerlei Sonderrechte.
RUN useradd --create-home --uid 10001 wlanalarm

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN pip install --no-cache-dir ".[mqtt]"

USER wlanalarm
WORKDIR /data
VOLUME ["/data"]

ENV PYTHONUNBUFFERED=1
EXPOSE 8723

HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8723/api/health',timeout=3).status==200 else 1)"

ENTRYPOINT ["wlanalarm"]
CMD ["run", "-c", "/data/config.yaml"]
