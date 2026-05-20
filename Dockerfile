# ---------------------------
# STAGE 1: Builder (Untuk build CSS)
# ---------------------------
FROM node:18-alpine AS builder

WORKDIR /app

# Copy package.json dan package-lock.json
COPY package*.json ./

# Install dependensi Node.js
RUN npm install

# Copy semua file source untuk proses build
COPY app/ .

# Copy juga tailwind config
COPY tailwind.config.js ./

# Build CSS Tailwind untuk production
RUN npx tailwindcss -i ./static/css/input.css -o ./static/css/output.css --minify


# ---------------------------
# STAGE 2: Final Image (Runtime)
# ---------------------------
FROM python:3.9-slim

WORKDIR /app

# Copy requirements.txt dan install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install postgresql-client and curl for health checks
RUN apt-get update && \
    apt-get install -y --no-install-recommends postgresql-client iputils-ping curl && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Copy kode aplikasi
COPY app/ ./

# SALIN FILE CSS YANG SUDAH DI-BUILD DARI STAGE 1
COPY --from=builder /app/static/css/output.css ./static/css/output.css

EXPOSE 5000

# PERBAIKAN: Tetap di /app, jangan pindah ke /app/app
CMD ["gunicorn", "--config", "gunicorn.conf.py", "app:app"]
