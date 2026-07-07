FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

# Install required system packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (better Docker cache)
COPY requirements-api.txt .
RUN pip install --upgrade pip && \
    pip install -r requirements-api.txt

# Copy application
COPY api.py .
COPY src ./src
COPY artifacts ./artifacts

# Verify files exist (optional but useful for debugging)
RUN ls -lah /app && \
    ls -lah /app/artifacts

# Cloud Run provides PORT environment variable
ENV PORT=8080

EXPOSE 8080

CMD ["sh", "-c", "uvicorn api:app --host 0.0.0.0 --port ${PORT}"]
