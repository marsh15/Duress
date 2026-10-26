FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

COPY . .
RUN uv sync --frozen && chmod +x scripts/*.sh

ENV PATH="/app/.venv/bin:$PATH" \
    DURESS_ROOT=/app \
    DURESS_HOME=/tmp/duress

# The public service is the only thing bound to 0.0.0.0. Managed targets are
# loopback-only and refused otherwise (plan P4f).
CMD ["sh", "-c", "duress serve --host 0.0.0.0 --port ${PORT:-8000}"]
