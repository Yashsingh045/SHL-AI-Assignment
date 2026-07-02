FROM python:3.11-slim

# Keep Python lean and predictable in containers.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/app/.hf_cache \
    PORT=8000

WORKDIR /app

# Install deps first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code + committed data (catalog + prebuilt retrieval index). The service loads
# these offline; it never fetches the catalog URL or shl.com at runtime.
COPY app ./app
COPY data ./data

EXPOSE 8000

# Render / HF Spaces inject $PORT; default to 8000 locally. Shell form so $PORT expands.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
