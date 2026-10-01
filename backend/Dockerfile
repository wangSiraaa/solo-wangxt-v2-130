FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LEVELING_DATABASE_URL=postgresql+psycopg2://leveling:leveling@postgres:5432/leveling \
    LEVELING_REDIS_URL=redis://redis:6379/0

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libsuitesparse-dev && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
