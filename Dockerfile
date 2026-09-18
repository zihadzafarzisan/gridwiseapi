FROM python:3.11-slim

# Prevent Python from writing bytecode and enable unbuffered stdout/stderr
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

# Set container working directory
WORKDIR /app

# Install basic system packages and clean up apt cache to keep image slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python application dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application source code (.dockerignore prevents copying .env or secrets)
COPY . /app

# Expose default HTTP port
EXPOSE 8000

# Container healthcheck compliant with GET /health returning {"status": "ok"}
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Entrypoint: bind to 0.0.0.0 on port 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
