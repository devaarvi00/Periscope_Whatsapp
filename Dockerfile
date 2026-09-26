FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# No system build deps needed: the MySQL driver is pure-Python (pymysql) and
# every other dependency ships manylinux wheels.

# Install Python deps first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app source
COPY app/ ./app/
COPY frontend/ ./frontend/
COPY scripts/ ./scripts/

# Non-root user
RUN useradd -m -u 1001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# /health checks MySQL and MongoDB. python is used because slim has no curl.
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=8).status == 200 else 1)"

# --workers MUST stay 1: the WebSocket connection registry (app/core/ws_manager)
# and the APScheduler jobs run in-process. Multiple workers would split live
# connections and run every scheduled job (bulk sends!) once per worker.
# --proxy-headers/--forwarded-allow-ips: trust X-Forwarded-* from nginx. The app
# port is only exposed on the internal Docker network, so "*" is safe here.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
