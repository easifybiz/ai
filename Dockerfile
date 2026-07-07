FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements-api.txt .
RUN pip install --no-cache-dir --timeout 300 --retries 5 -r requirements-api.txt

COPY src/ ./src/
COPY artifacts/ ./artifacts/
COPY api.py .

EXPOSE 8080

CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8080"]
