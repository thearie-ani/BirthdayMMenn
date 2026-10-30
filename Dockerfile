# ==============================================================================
# BirthdayMate Telegram Bot - Production Dockerfile
# Optimized for Linux x86-64 (e.g. Google Cloud e2-micro Always Free VM)
# ==============================================================================

# Base image: Official Python 3.12 Slim (Debian Bookworm)
FROM python:3.12-slim

# Prevent Python from writing .pyc files to disk and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# Create unprivileged system group and user
RUN groupadd -g 1000 appgroup && \
    useradd -u 1000 -g appgroup -s /bin/bash -m appuser

# Set working directory
WORKDIR /app

# Create persistence directories for SQLite database and backups
RUN mkdir -p /app/data /app/backups && \
    chown -R appuser:appgroup /app

# Copy dependency definition and install packages
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/requirements.txt

# Copy application source code and configuration
COPY --chown=appuser:appgroup app /app/app
COPY --chown=appuser:appgroup pytest.ini /app/pytest.ini
COPY --chown=appuser:appgroup .env.example /app/.env.example

# Switch to non-root user for security
USER appuser

# Entry point: run application module directly so signals (SIGTERM/SIGINT) are handled cleanly
CMD ["python", "-m", "app.main"]
