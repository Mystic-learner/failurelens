FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv
COPY pyproject.toml alembic.ini ./
COPY app ./app
COPY shipper ./shipper
COPY alembic ./alembic
RUN pip install .

RUN useradd --create-home appuser
USER appuser
EXPOSE 8000

# One process on purpose: queue, windows and the WebSocket hub live in memory.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --timeout-graceful-shutdown 30"]
