# Single-container browser automation service: Xvfb + system Chromium (CDP)
# + FastAPI (Playwright client connecting to that same-container Chromium).
# No `playwright install` here -- we never launch Playwright's own bundled
# browser in production, only connect_over_cdp to the system Chromium below.
FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    DISPLAY=:99 \
    PYTHONUNBUFFERED=1

# Set to "false" to install OS/Python packages from the default upstream
# sources instead of the Tsinghua mirrors (useful outside mainland-China
# network paths). Declared right before its first use so it never sits
# between FROM and the (expensive, rarely-changing) apt-get layer below --
# an ARG inserted earlier in the file busts every later layer's cache even
# if that layer doesn't reference the ARG at all.
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
        chromium \
        xvfb \
        python3 \
        python3-pip \
        tini \
        curl \
        fonts-noto-cjk \
        fonts-wqy-zenhei \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /tmp/chrome-data

WORKDIR /app

COPY pyproject.toml .
COPY app /app/app
RUN set -eux; \
    if [ "$USE_MIRROR" = "true" ]; then \
        pip install --break-system-packages --no-cache-dir --timeout 180 --retries 5 \
            --index-url https://pypi.tuna.tsinghua.edu.cn/simple .; \
    else \
        pip install --break-system-packages --no-cache-dir --timeout 180 --retries 5 .; \
    fi

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Only the FastAPI port is ever exposed -- CDP (9222) stays on localhost,
# reachable only from the FastAPI process inside this same container.
EXPOSE 8000

ENTRYPOINT ["/usr/bin/tini", "--", "/entrypoint.sh"]
