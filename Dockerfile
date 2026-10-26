FROM python:3.14.2-slim

COPY --from=ghcr.io/astral-sh/uv:0.9.17 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --locked --no-dev --no-install-project

COPY alembic.ini ./
COPY src ./src
RUN uv sync --locked --no-dev

RUN useradd --create-home app
USER app

ENV PATH="/app/.venv/bin:$PATH" \
    CS2M_HOST=0.0.0.0
EXPOSE 8080

CMD ["sh", "-c", "alembic upgrade head && cs2market"]
