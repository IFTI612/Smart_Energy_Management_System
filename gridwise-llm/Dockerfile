FROM python:3.11-slim

RUN useradd -m -r appuser

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    coinor-cbc \
    coinor-libcbc-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
