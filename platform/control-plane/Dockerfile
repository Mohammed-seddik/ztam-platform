FROM python:3.12.9-slim

WORKDIR /app/platform/control-plane

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY platform/control-plane/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY platform/control-plane/main.py ./main.py
COPY scripts/tenant_manager.py /app/scripts/tenant_manager.py

RUN addgroup --system appuser \
    && adduser --system --ingroup appuser appuser \
    && mkdir -p /data \
    && chown -R appuser:appuser /app /data
USER appuser

EXPOSE 8010

HEALTHCHECK --interval=10s --timeout=5s --retries=5 \
    CMD curl -sf http://localhost:8010/health || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8010"]
