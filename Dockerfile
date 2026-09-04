FROM python:3.14-slim

# ffmpeg encodes the MP3 segments; nothing else native is needed.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY narrator ./narrator

ENV PATH="/app/.venv/bin:$PATH" \
    CACHE_DIR=/cache
VOLUME /cache
EXPOSE 13379

RUN useradd --create-home narrator && chown -R narrator /app
USER narrator

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:13379/healthz')"

CMD ["python", "-m", "narrator.main"]
