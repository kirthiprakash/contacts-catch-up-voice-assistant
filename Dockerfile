FROM python:3.12-slim

WORKDIR /app

# Install uv
RUN pip install uv

# Copy dependency files first for layer caching
COPY pyproject.toml uv.lock ./

# Install dependencies
RUN uv sync --frozen --no-dev

# Copy application code
COPY app/ ./app/
COPY scripts/ ./scripts/

# Create directory for SQLite database
RUN mkdir -p /data

# Railway injects PORT at runtime; default to 8000 for local runs
ENV PORT=8000
ENV DATABASE_URL=/data/contacts.db

EXPOSE 8000

CMD uv run uvicorn app.main:app --host 0.0.0.0 --port $PORT
