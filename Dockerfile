# showntell's hosted mode in one image (docs/self-hosting.md). Runs as a non-root user on a
# read-only root filesystem; /data is the only writable place: walkthroughs, audio, auth.db, and
# the voice and language models, fetched there on first start.
FROM python:3.13-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.12.17 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE THIRD_PARTY_NOTICES.md ./
COPY src ./src
RUN uv sync --locked --no-dev --extra video --extra hosted \
    && /app/.venv/bin/playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/* /root/.cache

RUN useradd --uid 10001 --no-create-home --home-dir /data/home --shell /usr/sbin/nologin showntell \
    && mkdir /data && chown showntell:showntell /data
USER 10001:10001

ENV PATH=/app/.venv/bin:$PATH \
    HOME=/data/home \
    XDG_CACHE_HOME=/data/cache \
    HF_HOME=/data/hf \
    SHOWNTELL_DATA=/data \
    SHOWNTELL_MODE=hosted \
    SHOWNTELL_HOST=0.0.0.0 \
    SHOWNTELL_PORT=4747 \
    SHOWNTELL_SPEECH=onnx
VOLUME /data
EXPOSE 4747
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:4747/health', timeout=4)"]
ENTRYPOINT ["/bin/sh", "-c", "mkdir -p \"$HOME\" && exec showntell serve \"$@\"", "showntell"]
