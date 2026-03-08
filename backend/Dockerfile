FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

# Copy and install Python package
COPY pyproject.toml .
COPY src/ src/
COPY alembic/ alembic/
COPY alembic.ini .

RUN pip install --no-cache-dir -e .

# Copy data fixtures for dev/demo
COPY ../data/fixtures/ /app/data/fixtures/ 2>/dev/null || true

EXPOSE 8000

CMD ["uvicorn", "fibokei.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
