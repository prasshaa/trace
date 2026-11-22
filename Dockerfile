# Trace production image (release, D52): the existing FastAPI app and static frontend, unchanged. No secrets in the image.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv/trace
COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt
COPY backend backend
COPY data-foundation/knowledge_packs data-foundation/knowledge_packs
RUN useradd --system --uid 10001 trace && chown -R trace /srv/trace
USER trace
WORKDIR /srv/trace/backend
EXPOSE 8080
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "2", \
     "--proxy-headers", "--forwarded-allow-ips=*", "--no-server-header"]
