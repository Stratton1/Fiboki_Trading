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

# Bundle starter dataset for production (lightweight H1 parquet files)
COPY data_starter/ data/starter/

# Create empty dirs for optional canonical/fixtures data
RUN mkdir -p /app/data/canonical /app/data/fixtures

EXPOSE 8000

CMD ["sh", "-c", "exec uvicorn fibokei.api.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
