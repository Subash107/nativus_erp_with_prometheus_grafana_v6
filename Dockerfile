FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install system deps (optional but safe)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
 && rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project
COPY . .

# Ensure data folder exists inside container
RUN mkdir -p /app/data

EXPOSE 5000

# Start via gunicorn, app object inside app.py
CMD ["gunicorn", "-b", "0.0.0.0:5000", "app:app"]
