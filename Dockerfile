FROM python:3.11-slim

# curl para healthcheck
RUN (apt-get update && apt-get install -y --no-install-recommends curl) || true; \
    rm -rf /var/lib/apt/lists/* 2>/dev/null || true

# Usuario no-root
RUN groupadd --gid 1001 appgroup && \
    useradd --uid 1001 --gid appgroup --shell /bin/bash --create-home appuser

WORKDIR /app

# Instalar dependencias Python (capa cacheada — solo se reconstruye si pyproject.toml cambia)
COPY pyproject.toml .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir ".[agents]"

# Instalar dependencias del sistema para Playwright + browser Chromium
# PLAYWRIGHT_BROWSERS_PATH apunta a un directorio legible por el usuario no-root
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers
RUN playwright install-deps chromium && \
    playwright install chromium && \
    chmod -R o+rx /opt/pw-browsers

# Copiar código fuente
COPY --chown=appuser:appgroup . .
RUN chmod +x start.sh

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request,os; urllib.request.urlopen('http://localhost:'+os.environ.get('PORT','8000')+'/health')" || exit 1

CMD ["sh", "start.sh"]
