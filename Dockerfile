# Python 3.12 runtime
FROM python:3.12-slim

# Python runtime settings
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Application directory
WORKDIR /app

# Install dependencies
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY backend/ ./backend/
COPY frontend/ ./frontend/
COPY agent/ ./agent/
COPY llm/ ./llm/
COPY rag/ ./rag/
COPY data/ ./data/
COPY automation/ ./automation/
COPY scripts/ ./scripts/
COPY migrations/ ./migrations/
COPY alembic.ini ./

# Create a non-root runtime user
RUN useradd -r -u 10001 -m appuser \
    && mkdir -p /app/rag/storage \
    && chown -R appuser:appuser /app

USER appuser

# FastAPI port
EXPOSE 8000

# Start FastAPI
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
