FROM debian:trixie-slim
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /bin/

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    FTHLS_PATHS_RECORDINGS_SOURCE_ROOT="/recordings" \
    FTHLS_PATHS_STATE_DB="/data/state.sqlite3" \
    FTHLS_PATHS_OUTPUT_ROOT="/output" \
    NVIDIA_VISIBLE_DEVICES="all" \
    NVIDIA_DRIVER_CAPABILITIES="compute,video,utility"

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY src ./src
COPY web /usr/share/nginx/site
COPY nginx/nginx.conf /etc/nginx/conf.d/default.conf

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        ffmpeg \
        nginx \
    && rm -rf /var/lib/apt/lists/* \
    && uv python install 3.14 \
    && uv sync --no-dev --no-cache \
    && mkdir -p /recordings /data /output \
    && rm -f /etc/nginx/sites-enabled/default

CMD ["sh", "-c", "nginx && exec frigate-timelapse-hls run-loop"]
