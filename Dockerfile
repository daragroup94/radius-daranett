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
    apt-get install -y --no-install-recommends postgresql-client iputils-ping curl iproute2 && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Copy kode aplikasi
COPY app/ ./

# SALIN FILE CSS YANG SUDAH DI-BUILD DARI STAGE 1
COPY --from=builder /app/static/css/output.css ./static/css/output.css

# Entrypoint: tambah route ke subnet NAS lalu jalankan gunicorn
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

EXPOSE 5000

CMD ["/docker-entrypoint.sh"]
