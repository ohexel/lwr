FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.33 /uv /usr/local/bin/uv

ENV UV_PROJECT_ENVIRONMENT=/opt/capstone-venv \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/capstone-venv/bin:${PATH}"

RUN apt-get update \
    && apt-get install -y --no-install-recommends libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./

RUN uv sync --frozen --no-install-project --no-dev \
    && python -c 'import dagster, dagster_webserver, psycopg' \
    && python -c 'import eccodes' \
    && python -c 'import geopandas' \
    && python -c 'import netCDF4'
