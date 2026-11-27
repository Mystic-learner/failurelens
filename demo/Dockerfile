FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv
COPY pyproject.toml ./
COPY shipper ./shipper
RUN pip install .

RUN useradd --create-home appuser
COPY demo ./demo
RUN chown -R appuser /srv/demo
USER appuser

CMD ["sh", "/srv/demo/run.sh"]
