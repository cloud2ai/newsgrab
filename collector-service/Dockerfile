# Pure-Python job API service: no browser, no Xvfb -- resolution/rendering
# is delegated entirely to playwright-service over HTTP.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

# Set to "false" to install OS/Python packages from the default upstream
# sources instead of the Tsinghua mirrors (useful outside mainland-China
# network paths). Declared right before its first use so it never sits
# between FROM and the apt-get layer below -- an ARG inserted earlier
# busts every later layer's cache even if that layer doesn't reference it.
ARG USE_MIRROR=true

RUN set -eux; \
    if [ "$USE_MIRROR" = "true" ]; then \
        rm -f /etc/apt/sources.list.d/debian.sources; \
        printf '%s\n' \
            "deb http://mirrors.tuna.tsinghua.edu.cn/debian/ bookworm main contrib non-free non-free-firmware" \
            "deb http://mirrors.tuna.tsinghua.edu.cn/debian/ bookworm-updates main contrib non-free non-free-firmware" \
            "deb http://mirrors.tuna.tsinghua.edu.cn/debian-security/ bookworm-security main contrib non-free non-free-firmware" \
            > /etc/apt/sources.list; \
    fi; \
    apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        tini \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /data

WORKDIR /app

COPY pyproject.toml .
COPY app /app/app
RUN set -eux; \
    if [ "$USE_MIRROR" = "true" ]; then \
        pip install --no-cache-dir --timeout 180 --retries 5 \
            --index-url https://pypi.tuna.tsinghua.edu.cn/simple .; \
    else \
        pip install --no-cache-dir --timeout 180 --retries 5 .; \
    fi

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV DEDUP_CACHE_PATH=/data/dedup_cache.db

EXPOSE 8000

ENTRYPOINT ["/usr/bin/tini", "--", "/entrypoint.sh"]
