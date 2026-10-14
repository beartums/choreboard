# The web UI, built to static files the API serves.
FROM node:24-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npx tsc --noEmit && npx vite build --outDir /static

FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY pyproject.toml README.md ./
COPY choreboard ./choreboard
COPY --from=web /static ./choreboard/static
RUN pip install --no-deps .

# Unprivileged, with the database on a mounted volume.
RUN useradd --uid 1000 --create-home choreboard && mkdir -p /data && chown choreboard /data
USER 1000
VOLUME /data
ENV CHOREBOARD_DB=/data/choreboard.db

ARG CHOREBOARD_VERSION=dev
ENV CHOREBOARD_VERSION=${CHOREBOARD_VERSION}

EXPOSE 8080
CMD ["uvicorn", "--factory", "choreboard.api:create_app", "--host", "0.0.0.0", "--port", "8080", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
