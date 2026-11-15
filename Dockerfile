# Job Application Agent: one-command local setup.  See "Run with Docker" in README.md

# Stage 1: build the Angular screen (Node is only needed while building, not in the final image)
FROM node:24-slim AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npx ng build

# Stage 2: the app
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY requirements-app.txt .
RUN pip install -r requirements-app.txt

# Only what the app needs: no tests, no data, no secrets (see .dockerignore)
COPY app ./app
COPY examples ./examples
COPY --from=ui /ui/dist/frontend/browser ./frontend/dist/frontend/browser

# Run as a normal user, not root
RUN useradd --create-home --uid 1000 agent && mkdir -p /app/data && chown agent /app/data
USER agent

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=4).status == 200 else 1)"

# --no-access-log: our own request log is used instead (it never records query strings)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
