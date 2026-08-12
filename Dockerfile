# Multi-stage Dockerfile: build frontend then package backend with static assets

# Frontend build stage
FROM node:20-alpine AS frontend-build
WORKDIR /app/frontend
# Install deps
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --silent
# Copy source and build
COPY frontend/ ./
RUN npm run build

# Backend runtime stage
FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=8000

# Install system dependencies for common Python packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    libpq-dev \
  && rm -rf /var/lib/apt/lists/*

# Copy and install Python runtime requirements
COPY requirements-backend.txt ./
RUN pip install --no-cache-dir -r requirements-backend.txt

# Copy backend source
COPY . /app
# Copy built frontend assets from previous stage
COPY --from=frontend-build /app/frontend/dist /app/frontend/dist

EXPOSE ${PORT}

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
