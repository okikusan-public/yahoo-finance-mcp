FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.6.14 /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY server.py auth.py ./

ENV PATH="/app/.venv/bin:$PATH" PORT=8080 HOME=/tmp
EXPOSE 8080
USER nobody
CMD ["python", "server.py"]
